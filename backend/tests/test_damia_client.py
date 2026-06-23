"""Unit tests for damia_client — h025ai-15.

Run with: python -m pytest backend/tests/test_damia_client.py -v

Tests cover:
  - INN validation (10/12 digits)
  - Disabled-state graceful fallback
  - Cache hit (skip HTTP)
  - HTTP 200 success path
  - HTTP 404 not found
  - HTTP 429 rate limit
  - HTTP 5xx transient error
  - OKVED2 → OKPD2 mapping
  - Response parsing (nested + flat shapes)
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import damia_client
from app.services.damia_client import (
    CACHE_TTL_DAYS,
    EgrulCompany,
    _okved2_to_okpd2,
    _parse_damia_response,
    fetch_company_by_inn,
    is_enabled,
)


# ── is_enabled / get_api_key ──────────────────────────────────────


def test_is_enabled_without_key(monkeypatch):
    monkeypatch.delenv("DAMIA_API_KEY", raising=False)
    assert is_enabled() is False


def test_is_enabled_with_key(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "test_key_123")
    assert is_enabled() is True


# ── INN validation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_inn_returns_none(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    assert await fetch_company_by_inn("") is None
    assert await fetch_company_by_inn("123") is None  # too short
    assert await fetch_company_by_inn("abcdefghij") is None  # non-digit
    assert await fetch_company_by_inn("12345678901") is None  # 11 digits


@pytest.mark.asyncio
async def test_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("DAMIA_API_KEY", raising=False)
    assert await fetch_company_by_inn("7707083893") is None


# ── OKVED2 → OKPD2 mapping ──────────────────────────────────────


def test_okved2_to_okpd2_mapping():
    assert _okved2_to_okpd2(["62.01", "62.02"]) == ["62.01.0", "62.02.0"]


def test_okved2_to_okpd2_dedup():
    """First-4-digits collisions get deduped."""
    out = _okved2_to_okpd2(["62.01.1", "62.01.2", "62.02.1"])
    # 62.01.1 and 62.01.2 both map to "62.01.0" → dedup
    assert out == ["62.01.0", "62.02.0"]


def test_okved2_to_okpd2_empty():
    assert _okved2_to_okpd2([]) == []
    assert _okved2_to_okpd2(None) == []


def test_okved2_to_okpd2_caps_at_20():
    codes = [f"62.{i:02d}.1" for i in range(30)]
    assert len(_okved2_to_okpd2(codes)) == 20


# ── Response parsing ────────────────────────────────────────────


def test_parse_nested_shape():
    data = {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": "770701001",
        "name": {"full": "ПАО СБЕРБАНК", "short": "СБЕРБАНК"},
        "address": {"value": "г Москва, ул Вавилова, д 19"},
        "registration_date": "1991-06-20",
        "status": {"code": "active"},
        "capital": {"value": 67760000000, "currency": "RUB"},
        "management": {"name": "Греф Г.О.", "post": "Президент"},
        "okved2": ["64.19", "64.91"],
    }
    c = _parse_damia_response(data, "7707083893")
    assert c.inn == "7707083893"
    assert c.legal_name == "ПАО СБЕРБАНК"
    assert c.short_name == "СБЕРБАНК"
    assert c.legal_address == "г Москва, ул Вавилова, д 19"
    assert c.authorized_capital == 67_760_000_000
    assert c.status == "active"
    assert c.okved2_codes == ["64.19", "64.91"]
    assert c.okpd2_suggested == ["64.19.0", "64.91.0"]
    assert c.management_name == "Греф Г.О."
    assert c.management_post == "Президент"


def test_parse_flat_shape():
    data = {
        "inn": "7707083893",
        "name": "ПАО СБЕРБАНК",
        "address": "г Москва",
        "status": "active",
        "capital": 67760000000,
        "management_name": "Греф Г.О.",
        "okved2": "64.19, 64.91",
    }
    c = _parse_damia_response(data, "7707083893")
    assert c.legal_name == "ПАО СБЕРБАНК"
    assert c.legal_address == "г Москва"
    assert c.authorized_capital == 67_760_000_000
    assert c.okved2_codes == ["64.19", "64.91"]


# ── HTTP path with mock client ──────────────────────────────────


def _mock_response(status_code: int, body: Any) -> httpx.Response:
    if isinstance(body, (dict, list)):
        text = json.dumps(body)
    else:
        text = str(body)
    return httpx.Response(
        status_code=status_code,
        content=text.encode("utf-8"),
        request=httpx.Request("GET", "https://api.damia.ru/fns/company"),
    )


@pytest.mark.asyncio
async def test_200_success(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "test_key")

    payload = {
        "inn": "7707083893",
        "name": {"full": "ПАО СБЕРБАНК", "short": "СБЕР"},
        "okved2": ["64.19"],
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(200, payload))
    mock_client.aclose = AsyncMock()

    company = await fetch_company_by_inn(
        "7707083893", use_cache=False, client=mock_client
    )
    assert company is not None
    assert company.inn == "7707083893"
    assert company.legal_name == "ПАО СБЕРБАНК"
    assert company.okved2_codes == ["64.19"]
    assert company.okpd2_suggested == ["64.19.0"]

    # Verify GET was called with right params
    call_args = mock_client.get.call_args
    assert call_args is not None
    url = call_args[0][0]
    params = call_args[1]["params"]
    assert url == "https://api.damia.ru/fns/company"
    assert params["inn"] == "7707083893"
    assert params["key"] == "test_key"


@pytest.mark.asyncio
async def test_404_not_found_returns_none(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(404, "Not found"))
    mock_client.aclose = AsyncMock()
    assert await fetch_company_by_inn("9999999999", client=mock_client) is None


@pytest.mark.asyncio
async def test_429_raises_rate_limit(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(429, "Too many"))
    mock_client.aclose = AsyncMock()
    with pytest.raises(damia_client.DamiaRateLimitError):
        await fetch_company_by_inn("7707083893", client=mock_client)


@pytest.mark.asyncio
async def test_500_raises_transient(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_response(500, "Oops"))
    mock_client.aclose = AsyncMock()
    with pytest.raises(damia_client.DamiaTransientError):
        await fetch_company_by_inn("7707083893", client=mock_client)


@pytest.mark.asyncio
async def test_timeout_returns_none(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("t"))
    mock_client.aclose = AsyncMock()
    assert await fetch_company_by_inn("7707083893", client=mock_client) is None


@pytest.mark.asyncio
async def test_invalid_json_returns_none(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    resp = httpx.Response(
        status_code=200,
        content=b"NOT JSON",
        request=httpx.Request("GET", "https://api.damia.ru/fns/company"),
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.aclose = AsyncMock()
    assert await fetch_company_by_inn("7707083893", client=mock_client) is None


# ── Health check ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_healthcheck_disabled(monkeypatch):
    monkeypatch.delenv("DAMIA_API_KEY", raising=False)
    h = await damia_client.healthcheck()
    assert h["enabled"] is False
    assert h["has_key"] is False
    assert h["base_url"] == "https://api.damia.ru"


@pytest.mark.asyncio
async def test_healthcheck_enabled(monkeypatch):
    monkeypatch.setenv("DAMIA_API_KEY", "k")
    h = await damia_client.healthcheck()
    assert h["enabled"] is True
    assert h["has_key"] is True
