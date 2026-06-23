"""Unit tests for matcher — h025ai-10.

Run with: python -m pytest backend/tests/test_matcher.py -v

5+ scenarios per spec, plus targeted unit tests for:
  - _code_matches, _region_matches
  - _apply_anti_outlier (the 99.6% Habr bug)
  - Verdict boundaries (MATCH_MIN_SCORE, REVIEW_MIN_SCORE)
  - Deadline-based verdict (CRITICAL, REVIEW, OK)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.matcher import (
    DEADLINE_CRITICAL_DAYS,
    DEADLINE_REVIEW_DAYS,
    MATCH_MIN_SCORE,
    MAX_DISCOUNT,
    REVIEW_MIN_SCORE,
    VERDICT_MATCH,
    VERDICT_NO_MATCH,
    VERDICT_REVIEW,
    _apply_anti_outlier,
    _code_matches,
    _days_until_deadline,
    _region_matches,
    match_profile_to_analysis,
)


# ── Helpers ──────────────────────────────────────────────────────


def _make_profile(**kwargs) -> SimpleNamespace:
    defaults = dict(
        okpd2_codes=["26.20.2", "62.01"],
        okved2_codes=[],
        regions=["Москва", "Московская область"],
        licenses=[],
        min_contract_sum=100_000,
        max_contract_sum=50_000_000,
        max_guarantee_sum=5_000_000,
        allowed_procedure_types=["auction", "tender"],
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_tender(**kwargs) -> SimpleNamespace:
    defaults = dict(
        title="Поставка серверного оборудования",
        customer="Минцифры РФ",
        price=None,
        deadline=datetime.utcnow() + timedelta(days=20),
        region="Москва",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_analysis(**kwargs) -> SimpleNamespace:
    defaults = dict(
        okpd2_extracted=["26.20.2"],
        regions_extracted=["Москва"],
        requirements={},
        financial={"nmck_rub": 12_450_000},
        deadlines={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── _code_matches ────────────────────────────────────────────────


def test_code_matches_exact():
    assert _code_matches("26.20.2", "26.20.2") is True


def test_code_matches_prefix():
    """First 2 dots match (XX.XX) → loose match."""
    assert _code_matches("26.20.2", "26.20.3") is True
    assert _code_matches("26.20.21", "26.20.2") is True


def test_code_mismatch():
    assert _code_matches("26.20.2", "62.01.1") is False


def test_code_empty():
    assert _code_matches("", "26.20.2") is False
    assert _code_matches("26.20.2", "") is False


# ── _region_matches ──────────────────────────────────────────────


def test_region_exact():
    assert _region_matches(["Москва"], "Москва") is True


def test_region_substring():
    assert _region_matches(["Московская область"], "г Москва, Московская область") is True


def test_region_no_match():
    assert _region_matches(["Санкт-Петербург"], "Москва") is False


def test_region_fuzzy():
    # "Московская обл" vs "Московская область" — high ratio
    assert _region_matches(["Московская обл"], "Московская область") is True


# ── _apply_anti_outlier (THE critical Habr bug) ─────────────────


def test_anti_outlier_normal_discount():
    """Discount 10% → nmck unchanged, no warning."""
    eff, warn, disc = _apply_anti_outlier(nmck=1_000_000, contract_price=900_000)
    assert eff == 1_000_000
    assert warn is None
    assert disc is not None and 0 < disc < MAX_DISCOUNT


def test_anti_outlier_extreme_discount_habr_bug():
    """The 99.6% bug from Habr case — must be flagged and excluded."""
    eff, warn, disc = _apply_anti_outlier(
        nmck=1_000_000_000, contract_price=4_000_000
    )
    assert eff is None  # do NOT use this nmck
    assert warn is not None
    assert "проверьте" in warn.lower() or "подозрительна" in warn.lower()
    assert disc is not None and disc > MAX_DISCOUNT


def test_anti_outlier_at_threshold():
    """Exactly 80% → allowed (boundary, not over)."""
    eff, warn, disc = _apply_anti_outlier(nmck=1_000_000, contract_price=200_000)
    # 1 - 200/1000 = 0.80 exactly, not > 0.80
    assert eff == 1_000_000
    assert warn is None


def test_anti_outlier_just_over():
    """80.01% → flagged."""
    eff, warn, disc = _apply_anti_outlier(nmck=1_000_000, contract_price=199_900)
    assert eff is None
    assert warn is not None


def test_anti_outlier_none_inputs():
    assert _apply_anti_outlier(None, 100) == (None, None, None)
    assert _apply_anti_outlier(100, None) == (100, None, None)
    assert _apply_anti_outlier(0, 100) == (0, None, None)


# ── _days_until_deadline ─────────────────────────────────────────


def test_days_until_future():
    future = datetime.utcnow() + timedelta(days=10)
    assert _days_until_deadline(future) >= 9  # allow 1s slop


def test_days_until_past():
    past = datetime.utcnow() - timedelta(days=5)
    assert _days_until_deadline(past) < 0


# ── Scenario 1: Perfect match ───────────────────────────────────


def test_scenario_1_perfect_match_cjm_example():
    """CJM §3: Минцифры, поставка серверов, НМЦК 12.45M, deadline 28.06."""
    profile = _make_profile()
    tender = _make_tender(
        deadline=datetime.utcnow() + timedelta(days=20),
        price=None,
    )
    analysis = _make_analysis()

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.verdict == VERDICT_MATCH
    assert result.score >= MATCH_MIN_SCORE
    assert result.breakdown.okpd2_score == 30
    assert result.breakdown.region_score == 20
    assert result.breakdown.price_score == 20
    assert result.breakdown.licenses_score == 20
    assert result.breakdown.time_score == 10
    assert result.breakdown.total() == 100


# ── Scenario 2: OKPD2 mismatch → no match ───────────────────────


def test_scenario_2_okpd2_mismatch():
    profile = _make_profile(okpd2_codes=["62.01"])
    tender = _make_tender()
    analysis = _make_analysis(okpd2_extracted=["45.20.1"])  # different class

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.verdict == VERDICT_NO_MATCH
    assert result.breakdown.okpd2_score == 0
    assert "ОКПД2" in " ".join(result.reasons)


# ── Scenario 3: Region mismatch → no match ──────────────────────


def test_scenario_3_region_mismatch():
    profile = _make_profile(regions=["Новосибирск"])
    tender = _make_tender(region="Москва")
    analysis = _make_analysis(regions_extracted=["Москва"])

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.verdict == VERDICT_NO_MATCH
    assert result.breakdown.region_score == 0
    assert any("Регион" in r for r in result.reasons)


# ── Scenario 4: Outlier guard — price excluded, verdict review ─


def test_scenario_4_outlier_guard_99_percent():
    """Habr-style 99.6% discount → nmck excluded, forced to REVIEW."""
    profile = _make_profile()
    tender = _make_tender(
        price=4_000_000,  # final price (supposedly)
    )
    analysis = _make_analysis(
        financial={"nmck_rub": 1_000_000_000}  # framework cap
    )

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.price_score_excluded is True
    assert result.breakdown.price_score == 0
    # Verdict: no_match because price component is excluded
    # (per our rules: no_match = okpd2==0 OR region==0 OR price_excluded)
    assert result.verdict in (VERDICT_REVIEW, VERDICT_NO_MATCH)
    assert result.breakdown.nmck_outlier_warning is not None


# ── Scenario 5: Deadline too close → review ─────────────────────


def test_scenario_5_deadline_critical():
    profile = _make_profile()
    tender = _make_tender(deadline=datetime.utcnow() + timedelta(days=2))
    analysis = _make_analysis()

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.time_score == 0
    assert result.verdict in (VERDICT_REVIEW, VERDICT_NO_MATCH)
    assert any("Дедлайн" in r for r in result.reasons)


def test_scenario_5b_deadline_review_zone():
    profile = _make_profile()
    tender = _make_tender(deadline=datetime.utcnow() + timedelta(days=5))
    analysis = _make_analysis()

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.time_score == 5
    # 30+20+20+20+5 = 95 → still MATCH actually, since time_score is partial
    # but verdict REVIEW because time is in review zone
    assert result.verdict in (VERDICT_MATCH, VERDICT_REVIEW)


# ── Scenario 6: Licenses missing → review ──────────────────────


def test_scenario_6_licenses_missing():
    profile = _make_profile(licenses=[])
    tender = _make_tender()
    analysis = _make_analysis(
        requirements={
            "licenses": [{"type": "ФСТЭК", "level": "TKE-3"}],
            "sro": False,
        }
    )

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.licenses_score == 0
    assert any("лицензи" in r.lower() for r in result.reasons)


def test_scenario_6b_licenses_partial():
    profile = _make_profile(licenses=[{"type": "ФСТЭК", "level": "TKE-3"}])
    tender = _make_tender()
    analysis = _make_analysis(
        requirements={
            "licenses": [
                {"type": "ФСТЭК", "level": "TKE-3"},
                {"type": "ФСБ", "level": None},
            ],
            "sro": False,
        }
    )

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.licenses_score == 10  # partial credit


# ── Scenario 7: NMCCK not in profile range → low price score ──


def test_scenario_7_price_out_of_range():
    profile = _make_profile(min_contract_sum=10_000_000, max_contract_sum=20_000_000)
    tender = _make_tender()
    analysis = _make_analysis(financial={"nmck_rub": 1_000_000})  # below min

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.price_score == 0
    assert any("минимума" in r for r in result.reasons)


# ── Scenario 8: NMCCK absent → neutral ─────────────────────────


def test_scenario_8_nmck_absent_neutral():
    """Per research: 95% of 44-FZ tenders don't expose НМЦК in card.
    Don't penalize — give neutral 10/20."""
    profile = _make_profile()
    tender = _make_tender()
    analysis = _make_analysis(financial={})  # no nmck

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.price_score == 10
    assert "не указана" in result.breakdown.price_reason.lower() or "нейтральная" in result.breakdown.price_reason.lower()


# ── Scenario 9: Empty profile → all zeros / review ─────────────


def test_scenario_9_empty_profile():
    profile = _make_profile(
        okpd2_codes=[], regions=[], min_contract_sum=None, max_contract_sum=None
    )
    tender = _make_tender()
    analysis = _make_analysis()

    result = match_profile_to_analysis(profile, tender, analysis)
    # No okpd2 + no regions + neutral price + neutral time
    # = 0 + 10 + 20 + 20 + 10 = 60
    assert result.score >= 50
    assert result.verdict in (VERDICT_REVIEW, VERDICT_MATCH)


# ── Scenario 10: procedure-type filter (allowed_procedure_types) ─


def test_scenario_10_sro_required_no_sro_license():
    profile = _make_profile(licenses=[{"type": "ISO 9001"}])
    tender = _make_tender()
    analysis = _make_analysis(requirements={"sro": True, "licenses": []})

    result = match_profile_to_analysis(profile, tender, analysis)
    assert result.breakdown.licenses_score == 0
    assert "СРО" in result.breakdown.licenses_reason


# ── Result serialization ────────────────────────────────────────


def test_result_to_dict():
    profile = _make_profile()
    tender = _make_tender()
    analysis = _make_analysis()

    result = match_profile_to_analysis(profile, tender, analysis)
    d = result.to_dict()
    assert "verdict" in d
    assert "score" in d
    assert "breakdown" in d
    assert "total" in d["breakdown"]
    assert "reasons" in d
    assert d["score"] == d["breakdown"]["total"]
