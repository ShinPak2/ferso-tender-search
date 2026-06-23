"""AI extraction service — h025ai-9.

Sends tender document text to DeepSeek API and parses a structured JSON
extraction (subject, ОКПД2, requirements, financial, deadlines, criteria,
citations) per SPEC.md §8.2.

Caching:
  - In-process dict + Redis (optional) keyed by SHA256 of (content_hash + prompt_version).
  - Caching avoids re-paying for the same document across re-analysis runs.
  - Cache hit path is the default; explicit `bypass_cache=True` for re-runs.

Provider:
  - DeepSeek (OpenAI-compatible chat completions API)
  - Default model: deepseek-chat (configurable via DEEPSEEK_MODEL)
  - JSON-mode response_format (model guarantees valid JSON)

Anti-outlier guard (delegated to document_parser.parse_financial_strict):
  - This module does NOT re-parse financial fields from the AI's response
    when canonical parsing is available. Strict-mode НМЦК from
    document_parser is the source of truth; AI extraction is the
    "best-effort secondary" that we cross-validate against.

Failure modes:
  - API key missing → returns mock extraction (deterministic from hash)
  - Network/HTTP error → returns None, logs warning (caller handles)
  - JSON parse error → returns raw text in `raw_ai_response` and minimal
    extraction (subject only) so the record isn't lost
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────

# Current prompt version — bump to invalidate caches if schema changes
PROMPT_VERSION = "1.2"

# Model defaults (overridable via env)
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEFAULT_TIMEOUT = 60.0

# Anti-outlier guard: max plausible discount
MAX_DISCOUNT = 0.80

# Cache TTL (seconds) for in-process + Redis
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days


# ── Public dataclasses ───────────────────────────────────────────


@dataclass
class AIExtraction:
    """Structured extraction result per SPEC.md §8.2."""

    subject: str | None = None
    okpd2_codes: list[str] = field(default_factory=list)
    okved2_codes: list[str] = field(default_factory=list)
    requirements: dict[str, Any] = field(default_factory=dict)
    financial: dict[str, Any] = field(default_factory=dict)
    deadlines: dict[str, Any] = field(default_factory=dict)
    evaluation_criteria: list[dict[str, Any]] = field(default_factory=list)
    source_pages: list[int] = field(default_factory=list)
    source_quotes: list[str] = field(default_factory=list)
    penalties: str | None = None

    # Provenance
    confidence: float = 0.0
    model: str = DEFAULT_MODEL
    prompt_version: str = PROMPT_VERSION
    content_hash: str | None = None
    fetched_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    from_cache: bool = False
    raw_ai_response: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Prompt builder ──────────────────────────────────────────────


def _build_prompt(document_text: str) -> list[dict[str, str]]:
    """Build the chat-completion messages for DeepSeek.

    System prompt is strict JSON-only. User prompt asks for the
    SPEC.md §8.2 schema and includes the document text (truncated).
    """
    system = (
        "Ты — эксперт по анализу тендерной документации РФ (44-ФЗ / 223-ФЗ). "
        "Извлеки из документа структурированные данные. "
        "Верни ТОЛЬКО валидный JSON по схеме ниже. "
        "Никакого текста до или после JSON, никаких markdown-блоков.\n\n"
        "Схема ответа:\n"
        "{\n"
        '  "subject": "<краткое описание предмета закупки 1-2 предложения>",\n'
        '  "okpd2_codes": ["<код1>", "<код2>"],\n'
        '  "okved2_codes": ["<код1>"],\n'
        '  "requirements": {\n'
        '    "licenses": [{"type": "ФСТЭК", "level": "TKE-3", "number": "...", "valid_until": "YYYY-MM-DD"}],\n'
        '    "sro": <true|false>,\n'
        '    "experience_years": <число или null>,\n'
        '    "iso_certifications": ["ISO 9001"],\n'
        '    "other": ["<требование>"]\n'
        '  },\n'
        '  "financial": {\n'
        '    "application_guarantee_rub": <число или null>,\n'
        '    "application_guarantee_pct": <число или null>,\n'
        '    "contract_guarantee_rub": <число или null>,\n'
        '    "contract_guarantee_pct": <число или null>,\n'
        '    "nmck_rub": <число или null>,\n'
        '    "nmck_source": "<card|doc|unknown>"\n'
        '  },\n'
        '  "deadlines": {\n'
        '    "submission": "<ISO date или null>",\n'
        '    "execution_days": <число или null>,\n'
        '    "execution_until": "<ISO date или null>"\n'
        '  },\n'
        '  "evaluation_criteria": [\n'
        '    {"name": "<название>", "weight_pct": <число>}\n'
        '  ],\n'
        '  "penalties": "<краткое описание штрафов/пени или null>",\n'
        '  "source_pages": [<номера страниц, на которых найдены ключевые данные>],\n'
        '  "source_quotes": ["<точная цитата из документа с указанием страницы>"]\n'
        "}\n\n"
        "ВАЖНО:\n"
        "1. Если поле не найдено — null или пустой массив.\n"
        "2. Цитаты — дословно из документа, до 200 символов каждая.\n"
        "3. ОКПД2 — формат XX.XX.X.XX, несколько через запятую.\n"
        "4. НМЦК — только если явно указана в документе ('Начальная (максимальная) цена контракта' / 'НМЦК').\n"
        "5. Если есть сомнения — лучше null, чем выдумывать."
    )

    # Truncate document text to keep token usage reasonable.
    # ~25k chars ≈ 6-7k tokens for Russian text. Most tender docs fit.
    truncated = document_text[:25_000] if document_text else ""
    user = (
        f"Документ:\n\n```\n{truncated}\n```\n\n"
        "Верни ТОЛЬКО JSON по схеме."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── Cache (in-process + Redis) ──────────────────────────────────


class _ExtractionCache:
    def __init__(self):
        self._mem: dict[str, AIExtraction] = {}
        self._redis = None
        try:
            import redis.asyncio as redis_async

            url = os.getenv("REDIS_URL")
            if url:
                self._redis = redis_async.from_url(url, decode_responses=True)
        except Exception as e:
            logger.debug("AI extraction: Redis unavailable: %s", e)

    def _key(self, content_hash: str) -> str:
        return f"aiextract:{PROMPT_VERSION}:{content_hash}"

    async def get(self, content_hash: str) -> AIExtraction | None:
        key = self._key(content_hash)
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    d = json.loads(raw)
                    d["from_cache"] = True
                    return AIExtraction(**d)
            except Exception as e:
                logger.debug("Redis GET failed: %s", e)
        return self._mem.get(key)

    async def set(self, content_hash: str, ex: AIExtraction) -> None:
        key = self._key(content_hash)
        d = ex.to_dict()
        d["from_cache"] = False
        payload = json.dumps(d, ensure_ascii=False)
        if self._redis:
            try:
                await self._redis.set(key, payload, ex=CACHE_TTL_SECONDS)
                return
            except Exception as e:
                logger.debug("Redis SET failed: %s", e)
        self._mem[key] = ex


_cache = _ExtractionCache()


# ── JSON extraction from LLM response ──────────────────────────


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Pull the JSON object out of an LLM response (handles stray text)."""
    if not text:
        return None
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try ```json ... ``` fenced block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try first balanced JSON object
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ── Mapping helpers ─────────────────────────────────────────────


def _coerce_int_list(value: Any) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for v in value if isinstance(value, list) else [value]:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def _coerce_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(v).strip() for v in value if v is not None]


def _coerce_financial(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in value.items():
        if v is None:
            out[k] = None
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v) if "pct" in k or "rub" in k else v
        else:
            try:
                out[k] = float(str(v).replace(",", ".").replace(" ", ""))
            except (TypeError, ValueError):
                out[k] = v
    return out


def _coerce_requirements(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in value.items():
        if k == "licenses":
            if isinstance(v, list):
                out[k] = [
                    lic if isinstance(lic, dict) else {"type": str(lic)}
                    for lic in v
                ]
            else:
                out[k] = []
        elif k == "sro":
            out[k] = bool(v) if v is not None else False
        elif k == "experience_years":
            try:
                out[k] = int(v) if v is not None else None
            except (TypeError, ValueError):
                out[k] = None
        elif k == "iso_certifications":
            out[k] = _coerce_str_list(v)
        elif k == "other":
            out[k] = _coerce_str_list(v)
        else:
            out[k] = v
    return out


def _map_to_extraction(
    raw: dict[str, Any],
    *,
    content_hash: str | None,
    model: str,
) -> AIExtraction:
    """Map LLM JSON response into AIExtraction dataclass."""
    return AIExtraction(
        subject=raw.get("subject"),
        okpd2_codes=_coerce_str_list(raw.get("okpd2_codes")),
        okved2_codes=_coerce_str_list(raw.get("okved2_codes")),
        requirements=_coerce_requirements(raw.get("requirements", {})),
        financial=_coerce_financial(raw.get("financial", {})),
        deadlines=raw.get("deadlines") or {},
        evaluation_criteria=raw.get("evaluation_criteria") or [],
        source_pages=_coerce_int_list(raw.get("source_pages")),
        source_quotes=_coerce_str_list(raw.get("source_quotes"))[:10],
        penalties=raw.get("penalties"),
        confidence=0.0,  # computed by confidence_from_extraction
        model=model,
        content_hash=content_hash,
        raw_ai_response=raw,
    )


def confidence_from_extraction(ex: AIExtraction) -> float:
    """Compute a 0..1 confidence score based on which fields are present.

    Higher = more fields extracted + more citations.
    """
    score = 0.0
    if ex.subject:
        score += 0.15
    if ex.okpd2_codes:
        score += 0.15
    if ex.requirements and any(ex.requirements.values()):
        score += 0.15
    fin = ex.financial or {}
    if any(fin.get(k) is not None for k in ("application_guarantee_rub", "contract_guarantee_rub")):
        score += 0.10
    if fin.get("nmck_rub") is not None:
        score += 0.10
    if ex.deadlines and (ex.deadlines.get("submission") or ex.deadlines.get("execution_days")):
        score += 0.10
    if ex.evaluation_criteria:
        score += 0.10
    if ex.source_quotes:
        score += 0.10
    if ex.source_pages:
        score += 0.05
    return min(1.0, score)


# ── Public API ───────────────────────────────────────────────────


def _content_hash_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def extract_structured(
    document_text: str,
    *,
    model: str | None = None,
    use_cache: bool = True,
    bypass_cache: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> AIExtraction | None:
    """Extract structured data from a tender document.

    Args:
        document_text: Plain text of the document (from document_parser).
        model: LLM model name. Default: DEEPSEEK_MODEL env or 'deepseek-chat'.
        use_cache: If True, check cache before calling API.
        bypass_cache: If True, always call API and overwrite cache.
        timeout: HTTP timeout in seconds.
        client: Optional pre-built httpx client (for testing).

    Returns:
        AIExtraction on success.
        None if API key is missing, network error, or unrecoverable parse error.
    """
    if not document_text or not document_text.strip():
        logger.info("AI extraction: empty document text")
        return None

    model = model or DEFAULT_MODEL
    content_hash = _content_hash_of(document_text)

    # Cache lookup
    if use_cache and not bypass_cache:
        cached = await _cache.get(content_hash)
        if cached is not None:
            logger.debug("AI extraction: cache hit for %s", content_hash[:8])
            return cached

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.warning("AI extraction: DEEPSEEK_API_KEY not set — returning None")
        return None

    messages = _build_prompt(document_text)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,  # low — we want deterministic extraction
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},  # JSON-mode
    }
    url = f"{DEFAULT_BASE_URL}/chat/completions"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout, headers=headers)

    try:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            logger.error("AI extraction: timeout: %s", e)
            return None
        except httpx.HTTPError as e:
            logger.error("AI extraction: HTTP error: %s", e)
            return None

        if resp.status_code != 200:
            logger.error(
                "AI extraction: %s — %s", resp.status_code, resp.text[:300]
            )
            return None

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            logger.error("AI extraction: bad JSON: %s", e)
            return None

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            logger.error("AI extraction: empty content in response")
            return None

        parsed = _extract_json_from_text(content)
        if not parsed:
            logger.error(
                "AI extraction: no JSON found in content: %r", content[:200]
            )
            return None

        ex = _map_to_extraction(
            parsed, content_hash=content_hash, model=model
        )
        ex.confidence = confidence_from_extraction(ex)

        # Cache
        await _cache.set(content_hash, ex)
        return ex
    finally:
        if owns_client:
            await client.aclose()


async def extract_from_cache_only(
    document_text: str,
) -> AIExtraction | None:
    """Return cached extraction if available, else None. No API call."""
    if not document_text:
        return None
    return await _cache.get(_content_hash_of(document_text))


def clear_cache() -> None:
    """Test helper: wipe in-process cache. Redis must be cleared separately."""
    _cache._mem.clear()


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "AIExtraction",
    "CACHE_TTL_SECONDS",
    "MAX_DISCOUNT",
    "PROMPT_VERSION",
    "clear_cache",
    "confidence_from_extraction",
    "extract_from_cache_only",
    "extract_structured",
]
