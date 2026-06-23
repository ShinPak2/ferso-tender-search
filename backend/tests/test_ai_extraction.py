"""Unit tests for ai_extraction — h025ai-9.

Run with: python -m pytest backend/tests/test_ai_extraction.py -v

Tests cover:
  - _extract_json_from_text: direct, fenced, embedded, malformed
  - confidence_from_extraction: scoring with various field combos
  - _map_to_extraction: LLM JSON → AIExtraction
  - extract_structured: disabled state, cache hit, mock HTTP 200
  - Coercion helpers (int list, str list, financial, requirements)
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import ai_extraction
from app.services.ai_extraction import (
    AIExtraction,
    PROMPT_VERSION,
    _coerce_financial,
    _coerce_int_list,
    _coerce_requirements,
    _coerce_str_list,
    _content_hash_of,
    _extract_json_from_text,
    _map_to_extraction,
    clear_cache,
    confidence_from_extraction,
    extract_structured,
)


# ── _extract_json_from_text ─────────────────────────────────────


def test_extract_direct_json():
    text = '{"subject": "Поставка оборудования"}'
    out = _extract_json_from_text(text)
    assert out == {"subject": "Поставка оборудования"}


def test_extract_fenced_json():
    text = '```json\n{"subject": "Услуги"}\n```'
    out = _extract_json_from_text(text)
    assert out == {"subject": "Услуги"}


def test_extract_embedded_json():
    text = 'Вот результат: {"subject": "Работы"} конец.'
    out = _extract_json_from_text(text)
    assert out == {"subject": "Работы"}


def test_extract_nested_json():
    text = '{"subject": "X", "financial": {"nmck_rub": 1000000}}'
    out = _extract_json_from_text(text)
    assert out["financial"]["nmck_rub"] == 1_000_000


def test_extract_returns_none_for_garbage():
    assert _extract_json_from_text("") is None
    assert _extract_json_from_text("not json at all") is None
    assert _extract_json_from_text("{unbalanced") is None


# ── confidence_from_extraction ──────────────────────────────────


def test_confidence_empty():
    ex = AIExtraction()
    assert confidence_from_extraction(ex) == 0.0


def test_confidence_full_extraction():
    ex = AIExtraction(
        subject="Поставка серверов",
        okpd2_codes=["26.20.2"],
        requirements={"sro": True, "experience_years": 3},
        financial={"nmck_rub": 12_000_000, "application_guarantee_rub": 600_000},
        deadlines={"submission": "2026-06-28T10:00:00"},
        evaluation_criteria=[{"name": "Цена", "weight_pct": 60}],
        source_quotes=["«Цена контракта: 12 000 000 ₽»"],
        source_pages=[3, 12, 25],
    )
    score = confidence_from_extraction(ex)
    # All fields present → near 1.0
    assert score >= 0.9


def test_confidence_partial():
    ex = AIExtraction(subject="X", okpd2_codes=["62.01"])
    score = confidence_from_extraction(ex)
    # subject + okpd2 = 0.30
    assert 0.25 <= score <= 0.35


# ── _map_to_extraction ──────────────────────────────────────────


def test_map_full():
    raw = {
        "subject": "Поставка серверного оборудования",
        "okpd2_codes": ["26.20.2", "26.20.3"],
        "okved2_codes": ["62.01"],
        "requirements": {
            "licenses": [{"type": "ФСТЭК", "level": "TKE-3"}],
            "sro": False,
            "experience_years": 5,
            "iso_certifications": ["ISO 9001"],
            "other": ["Опыт госзакупок от 3 лет"],
        },
        "financial": {
            "application_guarantee_rub": 622500,
            "application_guarantee_pct": 5.0,
            "contract_guarantee_rub": 1245000,
            "contract_guarantee_pct": 10.0,
            "nmck_rub": 12450000,
        },
        "deadlines": {
            "submission": "2026-06-28T10:00:00",
            "execution_days": 90,
        },
        "evaluation_criteria": [
            {"name": "Цена контракта", "weight_pct": 60},
            {"name": "Квалификация", "weight_pct": 30},
            {"name": "Срок", "weight_pct": 10},
        ],
        "penalties": "Пени 0.1% в день",
        "source_pages": [3, 12, 25],
        "source_quotes": [
            "стр.3: «Начальная (максимальная) цена контракта: 12 450 000,00 ₽»"
        ],
    }
    ex = _map_to_extraction(
        raw, content_hash="abc", model="deepseek-chat"
    )
    assert ex.subject == "Поставка серверного оборудования"
    assert ex.okpd2_codes == ["26.20.2", "26.20.3"]
    assert ex.requirements["sro"] is False
    assert ex.requirements["experience_years"] == 5
    assert ex.financial["nmck_rub"] == 12_450_000
    assert ex.evaluation_criteria[0]["weight_pct"] == 60
    assert ex.source_pages == [3, 12, 25]
    assert "12 450 000" in ex.source_quotes[0]


# ── Coercion helpers ────────────────────────────────────────────


def test_coerce_int_list():
    assert _coerce_int_list([1, 2, 3]) == [1, 2, 3]
    assert _coerce_int_list("5") == [5]
    assert _coerce_int_list(["1", "two", None, "3"]) == [1, 3]
    assert _coerce_int_list(None) == []


def test_coerce_str_list():
    assert _coerce_str_list("a, b, c") == ["a", "b", "c"]
    assert _coerce_str_list(["a", "b"]) == ["a", "b"]
    assert _coerce_str_list(None) == []


def test_coerce_financial_strings():
    out = _coerce_financial({"nmck_rub": "12 450 000,00", "foo": "bar"})
    assert out["nmck_rub"] == 12_450_000.0
    assert out["foo"] == "bar"


def test_coerce_requirements_licenses():
    out = _coerce_requirements(
        {
            "licenses": [{"type": "ФСТЭК"}, "СРО string entry"],
            "sro": True,
            "experience_years": "5",
        }
    )
    assert out["licenses"] == [{"type": "ФСТЭК"}, {"type": "СРО string entry"}]
    assert out["sro"] is True
    assert out["experience_years"] == 5


# ── extract_structured (mock HTTP) ──────────────────────────────


def _mock_chat_response(content: str) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }
    return httpx.Response(
        status_code=200,
        content=json.dumps(body).encode("utf-8"),
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions"),
    )


@pytest.mark.asyncio
async def test_disabled_no_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    clear_cache()
    result = await extract_structured("Some document text", use_cache=False)
    assert result is None


@pytest.mark.asyncio
async def test_empty_text_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert await extract_structured("") is None
    assert await extract_structured("   \n  ") is None


@pytest.mark.asyncio
async def test_200_success(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test_key")
    clear_cache()

    ai_json = json.dumps(
        {
            "subject": "Поставка серверов",
            "okpd2_codes": ["26.20.2"],
            "requirements": {"sro": False, "experience_years": 3},
            "financial": {"nmck_rub": 5_000_000},
            "deadlines": {"submission": "2026-06-28"},
            "evaluation_criteria": [],
            "source_pages": [1, 5],
            "source_quotes": ["«5 000 000 руб.»"],
        },
        ensure_ascii=False,
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_chat_response(ai_json))
    mock_client.aclose = AsyncMock()

    ex = await extract_structured(
        "Какой-то текст документа", use_cache=False, client=mock_client
    )
    assert ex is not None
    assert ex.subject == "Поставка серверов"
    assert ex.okpd2_codes == ["26.20.2"]
    assert ex.financial["nmck_rub"] == 5_000_000
    assert ex.confidence > 0
    assert ex.from_cache is False
    assert ex.content_hash == _content_hash_of("Какой-то текст документа")
    assert ex.prompt_version == PROMPT_VERSION


@pytest.mark.asyncio
async def test_500_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    clear_cache()
    resp = httpx.Response(
        status_code=500,
        content=b"Oops",
        request=httpx.Request("POST", "https://api.deepseek.com/v1"),
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp)
    mock_client.aclose = AsyncMock()

    assert await extract_structured("text", client=mock_client) is None


@pytest.mark.asyncio
async def test_timeout_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    clear_cache()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("t"))
    mock_client.aclose = AsyncMock()
    assert await extract_structured("text", client=mock_client) is None


@pytest.mark.asyncio
async def test_garbage_content_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    clear_cache()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        return_value=_mock_chat_response("Это не JSON")
    )
    mock_client.aclose = AsyncMock()
    assert await extract_structured("text", client=mock_client) is None


# ── Real-world CJM example ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cjm_ministry_of_digital_example(monkeypatch):
    """CJM §3: Tender #0372200197324000123 — Минцифры, серверы.

    The example from the CJM that should produce:
      - subject: "Поставка серверного оборудования"
      - okpd2: ["26.20.2"]
      - financial.nmck_rub: 12_450_000
      - deadlines.submission: "2026-06-28T10:00:00"
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    clear_cache()

    # Stub document text mimicking the actual tender
    doc = (
        "ИЗВЕЩЕНИЕ о закупке №0372200197324000123\n"
        "Заказчик: Минцифры РФ\n"
        "Предмет: Поставка серверного оборудования для государственных нужд\n"
        "Начальная (максимальная) цена контракта: 12 450 000,00 руб.\n"
        "Обеспечение заявки: 5% (622 500,00 руб.)\n"
        "Обеспечение исполнения контракта: 10% (1 245 000,00 руб.)\n"
        "Срок подачи заявок: 28.06.2026 10:00\n"
        "Срок исполнения: 90 календарных дней\n"
        "Коды ОКПД2: 26.20.2\n"
    )

    ai_json = json.dumps(
        {
            "subject": "Поставка серверного оборудования для государственных нужд",
            "okpd2_codes": ["26.20.2"],
            "requirements": {"experience_years": 3},
            "financial": {
                "nmck_rub": 12_450_000,
                "application_guarantee_rub": 622_500,
                "application_guarantee_pct": 5.0,
                "contract_guarantee_rub": 1_245_000,
                "contract_guarantee_pct": 10.0,
            },
            "deadlines": {
                "submission": "2026-06-28T10:00:00",
                "execution_days": 90,
            },
            "evaluation_criteria": [],
            "source_pages": [1],
            "source_quotes": [
                "«Начальная (максимальная) цена контракта: 12 450 000,00 руб.»"
            ],
        },
        ensure_ascii=False,
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_chat_response(ai_json))
    mock_client.aclose = AsyncMock()

    ex = await extract_structured(doc, client=mock_client)
    assert ex is not None
    assert ex.subject and "сервер" in ex.subject.lower()
    assert "26.20.2" in ex.okpd2_codes
    assert ex.financial["nmck_rub"] == 12_450_000
    assert ex.deadlines["submission"] == "2026-06-28T10:00:00"
    assert ex.deadlines["execution_days"] == 90
    # High confidence: subject + okpd2 + fin + deadlines + quotes + pages
    assert ex.confidence >= 0.7
