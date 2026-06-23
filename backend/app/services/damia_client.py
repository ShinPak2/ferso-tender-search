"""DaMIA API-ФНС client — h025ai-15.

Provides company data by INN via the commercial DaMIA aggregator.

Why DaMIA instead of direct FNS (egrul.nalog.ru)?
  - Direct FNS: captcha, throttling, no SLA, OLE/Excel downloads
  - DaMIA: REST + JSON, 0.1-0.5 ₽ per request, SLA guaranteed
  - 5 000 ₽/mo budget for MVP ≈ 10K-50K INN lookups

Endpoints used:
  GET https://api.damia.ru/fns/company?inn={inn}&key={API_KEY}
  Returns: company data, OKVED2 codes, management, capital, address.

Caching:
  - Redis, key = damia:fns:<inn>, TTL 30 days (per spec h025ai-15)
  - Graceful fallback to in-memory dict if Redis unavailable

Configuration:
  - DAMIA_API_KEY env var; if missing → module is disabled, fetches
    return None and the wizard shows "Сервис временно недоступен".

Reference: research/ftp-xml-recon.md (DaMIA-ФНС alternative section)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────

DAMIA_BASE_URL = "https://api.damia.ru"
DAMIA_FNS_PATH = "/fns/company"
DEFAULT_TIMEOUT = 15.0
CACHE_TTL_DAYS = 30


# ── Public dataclasses ───────────────────────────────────────────


@dataclass
class EgrulCompany:
    """Normalized company record from DaMIA-ФНС.

    Mirrors the fields we need for the supplier profile wizard.
    """

    inn: str
    ogrn: str | None
    kpp: str | None
    legal_name: str
    short_name: str | None
    legal_address: str | None
    registration_date: str | None
    authorized_capital: float | None
    status: str | None  # 'active' | 'liquidating' | 'liquidated'
    okved2_codes: list[str] = field(default_factory=list)
    okpd2_suggested: list[str] = field(default_factory=list)  # derived from OKVED2
    management_name: str | None = None
    management_post: str | None = None

    # DaMIA-specific
    raw: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Cache (Redis or in-memory) ──────────────────────────────────


class _DamiaCache:
    """Tiny cache for DaMIA responses. Redis preferred, in-memory fallback."""

    def __init__(self):
        self._mem: dict[str, tuple[str, datetime]] = {}
        self._redis = None
        try:
            import redis.asyncio as redis_async

            url = os.getenv("REDIS_URL")
            if url:
                self._redis = redis_async.from_url(url, decode_responses=True)
                logger.info("DaMIA: Redis cache enabled")
        except Exception as e:
            logger.warning("DaMIA: Redis unavailable, using in-memory cache: %s", e)

    def _key(self, inn: str) -> str:
        return f"damia:fns:{inn}"

    async def get(self, inn: str) -> EgrulCompany | None:
        key = self._key(inn)
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return EgrulCompany(**json.loads(raw))
            except Exception as e:
                logger.debug("DaMIA Redis GET failed: %s", e)
        if key in self._mem:
            payload, expires = self._mem[key]
            if expires > datetime.utcnow():
                return EgrulCompany(**json.loads(payload))
            self._mem.pop(key, None)
        return None

    async def set(self, inn: str, company: EgrulCompany) -> None:
        key = self._key(inn)
        payload = json.dumps(company.to_dict(), ensure_ascii=False)
        expires = datetime.utcnow() + timedelta(days=CACHE_TTL_DAYS)
        if self._redis:
            try:
                await self._redis.set(key, payload, ex=CACHE_TTL_DAYS * 86400)
                return
            except Exception as e:
                logger.debug("DaMIA Redis SET failed: %s", e)
        self._mem[key] = (payload, expires)


_cache = _DamiaCache()


# ── Mapping helpers ──────────────────────────────────────────────


def _okved2_to_okpd2(okved_codes: list[str] | None) -> list[str]:
    """Naive mapping OKVED2 → OKPD2.

    In OKVED2/OKPD2 the first 4 digits are identical for the same product
    class. We keep this simple for MVP; the user can edit later.
    """
    if not okved_codes:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for code in okved_codes:
        # OKVED2 is XX.XX.X.XX, OKPD2 same format
        # Truncate to 4 digits for a rough match
        digits = code.replace(".", "")[:4]
        if len(digits) == 4 and digits not in seen:
            okpd = f"{digits[:2]}.{digits[2:]}.0"
            out.append(okpd)
            seen.add(digits)
    return out[:20]  # cap at 20


def _parse_damia_response(data: dict[str, Any], inn: str) -> EgrulCompany:
    """Parse DaMIA-ФНС response into our normalized record.

    DaMIA's actual response shape (v2026):
      {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": "770701001",
        "name": {
          "full": "ПАО СБЕРБАНК",
          "short": "СБЕРБАНК"
        },
        "address": {"value": "г Москва, ул Вавилова, д 19"},
        "registration_date": "1991-06-20",
        "status": {"code": "active", "name": "Действующая"},
        "capital": {"value": 67760000000, "currency": "RUB"},
        "management": {"name": "Греф Герман Оскарович", "post": "Президент"},
        "okved2": ["64.19", "64.91", ...],
        ...
      }
    We accept both nested (full) and flat (legacy) shapes.
    """
    # Handle nested 'name' object
    if isinstance(data.get("name"), dict):
        full = data["name"].get("full") or data["name"].get("full_name") or ""
        short = data["name"].get("short") or data["name"].get("short_name")
    else:
        full = data.get("name") or data.get("full_name") or data.get("legal_name") or ""
        short = data.get("short_name")

    # Address
    if isinstance(data.get("address"), dict):
        addr = data["address"].get("value") or data["address"].get("full")
    else:
        addr = data.get("address") or data.get("legal_address")

    # Status
    if isinstance(data.get("status"), dict):
        status = data["status"].get("code")
    else:
        status = data.get("status")

    # Capital
    if isinstance(data.get("capital"), dict):
        cap = data["capital"].get("value")
    else:
        cap = data.get("authorized_capital") or data.get("capital")

    # Management
    if isinstance(data.get("management"), dict):
        mgmt_name = data["management"].get("name")
        mgmt_post = data["management"].get("post")
    else:
        mgmt_name = data.get("management_name")
        mgmt_post = data.get("management_post")

    okved_codes = data.get("okved2") or data.get("okved") or []
    if isinstance(okved_codes, str):
        okved_codes = [c.strip() for c in okved_codes.split(",") if c.strip()]

    return EgrulCompany(
        inn=inn,
        ogrn=data.get("ogrn"),
        kpp=data.get("kpp"),
        legal_name=full,
        short_name=short,
        legal_address=addr,
        registration_date=data.get("registration_date") or data.get("reg_date"),
        authorized_capital=float(cap) if cap is not None else None,
        status=status,
        okved2_codes=okved_codes,
        okpd2_suggested=_okved2_to_okpd2(okved_codes),
        management_name=mgmt_name,
        management_post=mgmt_post,
        raw=data,
    )


# ── Public API ───────────────────────────────────────────────────


def is_enabled() -> bool:
    """True if DAMIA_API_KEY is set in environment."""
    return bool(os.getenv("DAMIA_API_KEY", "").strip())


def get_api_key() -> str | None:
    """Return the API key, or None if not configured."""
    key = os.getenv("DAMIA_API_KEY", "").strip()
    return key or None


class DamiaError(Exception):
    """Base class for DaMIA client errors."""


class DamiaDisabledError(DamiaError):
    """Raised when DAMIA_API_KEY is not set."""


class DamiaNotFoundError(DamiaError):
    """Raised when INN is not found in DaMIA database."""


class DamiaRateLimitError(DamiaError):
    """Raised on HTTP 429 — caller should backoff and retry."""


class DamiaTransientError(DamiaError):
    """Raised on 5xx or network errors — caller should retry."""


async def fetch_company_by_inn(
    inn: str,
    *,
    use_cache: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> EgrulCompany | None:
    """Fetch company data by INN via DaMIA-ФНС.

    Args:
        inn: 10 or 12 digit Russian tax ID.
        use_cache: If True, check Redis/in-memory first.
        timeout: HTTP timeout in seconds.
        client: Optional pre-built httpx client (for testing).

    Returns:
        EgrulCompany on success, or None if:
          - DAMIA_API_KEY is not configured
          - INN is invalid (length)
          - DaMIA returned 404 (company not found)
          - DaMIA returned 5xx (after retry) — caller handles graceful
            fallback in the endpoint

    Raises:
        DamiaError subclasses only for caller-actionable errors.
        Returns None for graceful "service unavailable" cases.
    """
    inn = (inn or "").strip()
    if not inn or len(inn) not in (10, 12) or not inn.isdigit():
        logger.info("DaMIA: invalid INN '%s'", inn)
        return None

    if not is_enabled():
        logger.warning("DaMIA: DAMIA_API_KEY not set — graceful fallback")
        return None

    # Cache check
    if use_cache:
        cached = await _cache.get(inn)
        if cached is not None:
            logger.debug("DaMIA: cache hit for INN %s", inn)
            return cached

    api_key = get_api_key()
    url = f"{DAMIA_BASE_URL}{DAMIA_FNS_PATH}"
    params = {"inn": inn, "key": api_key}
    headers = {"Accept": "application/json"}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout, headers=headers)

    try:
        try:
            resp = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            logger.error("DaMIA: timeout for INN %s: %s", inn, e)
            return None
        except httpx.HTTPError as e:
            logger.error("DaMIA: HTTP error for INN %s: %s", inn, e)
            return None

        if resp.status_code == 200:
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                logger.error("DaMIA: invalid JSON for INN %s: %s", inn, e)
                return None
            company = _parse_damia_response(data, inn)
            await _cache.set(inn, company)
            return company
        elif resp.status_code == 404:
            logger.info("DaMIA: INN %s not found", inn)
            return None
        elif resp.status_code == 429:
            raise DamiaRateLimitError(f"DaMIA rate limit hit for {inn}")
        elif resp.status_code in (500, 502, 503, 504):
            raise DamiaTransientError(
                f"DaMIA server error {resp.status_code} for {inn}"
            )
        else:
            logger.error(
                "DaMIA: unexpected status %s for INN %s: %s",
                resp.status_code,
                inn,
                resp.text[:200],
            )
            return None
    finally:
        if owns_client:
            await client.aclose()


async def healthcheck() -> dict[str, Any]:
    """Diagnostic: check DaMIA configuration and connectivity.

    Returns:
        {
            "enabled": bool,
            "has_key": bool,
            "cache": "redis" | "memory" | "none",
            "base_url": str,
        }
    """
    return {
        "enabled": is_enabled(),
        "has_key": bool(get_api_key()),
        "cache": "redis" if _cache._redis else ("memory" if _cache._mem is not None else "none"),
        "base_url": DAMIA_BASE_URL,
        "fns_path": DAMIA_FNS_PATH,
    }


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "CACHE_TTL_DAYS",
    "DAMIA_BASE_URL",
    "DAMIA_FNS_PATH",
    "DamiaDisabledError",
    "DamiaError",
    "DamiaNotFoundError",
    "DamiaRateLimitError",
    "DamiaTransientError",
    "EgrulCompany",
    "fetch_company_by_inn",
    "get_api_key",
    "healthcheck",
    "is_enabled",
]
