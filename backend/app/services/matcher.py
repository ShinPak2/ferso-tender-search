"""Subscription-tender matcher — h025ai-10.

Computes a match score (0-100) and verdict (✅ / ⚠️ / ❌) between a
supplier profile and a tender analysis (per SPEC.md §8.2).

Scoring breakdown (max 100):
  +30  ОКПД2 codes intersect
  +20  Region overlap
  +20  Price in supplier's range
  +20  All required licenses held
  +10  Submission deadline ≥ 7 days away (time buffer)

Anti-outlier guard (h025ai-10 / SPEC §8.2):
  If `discount = 1 - (contract_price / nmck_rub) > 0.80`:
    - The price component is NOT counted (avoids 99.6% bug from Habr case)
    - The verdict is forced to ⚠️ ТРЕБУЕТ ВНИМАНИЯ
    - A `nmck_outlier_warning` field is populated for the UI

Verdict rules (SPEC §8.2):
  ✅ СОВПАДАЕТ       — ОКПД2 ∩ ≠ ∅, region in list, price in range, no blocking requirements
  ⚠️ ТРЕБУЕТ ВНИМАНИЯ — partial match OR anti-outlier triggered OR deadline < 7 days
  ❌ НЕ ПОДХОДИТ     — ОКПД2 doesn't match OR region outside OR price outside OR deadline < 3 days
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session
from ..models import Subscription, SubscriptionMatch, SubscriptionStatus, Tender
from ..models.supplier_profile import SupplierProfile
from ..models.tender_analysis import TenderAnalysis

logger = logging.getLogger(__name__)


# ── Verdict constants ────────────────────────────────────────────

VERDICT_MATCH = "match"           # ✅ СОВПАДАЕТ
VERDICT_REVIEW = "review"         # ⚠️ ТРЕБУЕТ ВНИМАНИЯ
VERDICT_NO_MATCH = "no_match"     # ❌ НЕ ПОДХОДИТ

# Score thresholds
MATCH_MIN_SCORE = 70
REVIEW_MIN_SCORE = 40

# Anti-outlier
MAX_DISCOUNT = 0.80

# Time thresholds
DEADLINE_CRITICAL_DAYS = 3
DEADLINE_REVIEW_DAYS = 7
TIME_BUFFER_FULL_POINTS = 7  # days+ to get full +10


# ── Public dataclasses ───────────────────────────────────────────


@dataclass
class MatchBreakdown:
    """Detailed scoring breakdown for one match."""

    okpd2_score: int = 0
    okpd2_reason: str = ""

    region_score: int = 0
    region_reason: str = ""

    price_score: int = 0
    price_reason: str = ""

    licenses_score: int = 0
    licenses_reason: str = ""

    time_score: int = 0
    time_reason: str = ""

    # Outlier guard
    discount: float | None = None
    nmck_outlier_warning: str | None = None
    price_score_excluded: bool = False  # True if guard excluded price score

    def total(self) -> int:
        return (
            self.okpd2_score
            + self.region_score
            + self.price_score
            + self.licenses_score
            + self.time_score
        )


@dataclass
class MatchResult:
    verdict: str  # VERDICT_MATCH | VERDICT_REVIEW | VERDICT_NO_MATCH
    score: int  # 0-100
    breakdown: MatchBreakdown
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self.breakdown)
        out["total"] = self.breakdown.total()
        return {
            "verdict": self.verdict,
            "score": self.score,
            "breakdown": out,
            "reasons": self.reasons,
        }


# ── Helpers ──────────────────────────────────────────────────────


def _code_prefix(code: str, depth: int = 2) -> str:
    """First N dots of a code: '26.20.2' depth=2 → '26.20'."""
    if not code:
        return ""
    parts = code.split(".")
    return ".".join(parts[:depth])


def _code_matches(a: str, b: str) -> bool:
    """Loose code match: equal OR 2-digit prefix match."""
    if not a or not b:
        return False
    if a == b:
        return True
    return _code_prefix(a, 2) == _code_prefix(b, 2) and len(_code_prefix(a, 2)) >= 2


def _region_matches(profile_regions: list[str], tender_region: str | None) -> bool:
    if not tender_region or not profile_regions:
        return False
    t = tender_region.lower()
    for r in profile_regions:
        if r.lower() in t or t in r.lower():
            return True
        # Fuzzy: ratio > 0.7 on first 12 chars
        if SequenceMatcher(None, r.lower()[:12], t[:12]).ratio() > 0.7:
            return True
    return False


def _days_until_deadline(deadline: datetime | None) -> int | None:
    if not deadline:
        return None
    delta = deadline - datetime.utcnow()
    return max(-365, delta.days)  # clamp to -365 to avoid bizarre values


def _check_licenses(
    supplier_licenses: list[dict[str, Any]],
    tender_requirements: dict[str, Any],
) -> tuple[int, str]:
    """Return (score, reason).

    supplier_licenses: [{type, level, number, valid_until}, ...]
    tender_requirements: {licenses: [{type, level, ...}], sro: bool, ...}

    If no required licenses → 20/20 (nothing to check).
    If all required licenses held → 20/20.
    If partial → 10/20.
    If missing required → 0/20.
    """
    req_licenses = tender_requirements.get("licenses") or []
    if not req_licenses and not tender_requirements.get("sro"):
        return 20, "Дополнительных лицензий не требуется"

    required_types = {l.get("type") for l in req_licenses if l.get("type")}
    if tender_requirements.get("sro"):
        required_types.add("СРО")

    if not required_types:
        return 20, "Нет требований к лицензиям"

    held_types = {l.get("type") for l in supplier_licenses if l.get("type")}
    missing = required_types - held_types

    if not missing:
        return 20, f"Все требуемые лицензии в наличии ({', '.join(required_types)})"
    if len(missing) < len(required_types):
        return 10, f"Частично: отсутствует {', '.join(missing)}"
    return 0, f"Отсутствуют обязательные лицензии: {', '.join(missing)}"


def _apply_anti_outlier(
    nmck: float | None, contract_price: float | None
) -> tuple[float | None, str | None, float | None]:
    """Returns (effective_nmck, warning, discount_pct).

    If discount > 80% → nmck is treated as suspicious. Returns None for
    effective_nmck to signal "don't use for scoring".
    """
    if nmck is None or contract_price is None:
        return nmck, None, None
    if nmck <= 0:
        return nmck, None, None
    discount = 1 - (contract_price / nmck)
    if discount > MAX_DISCOUNT:
        return (
            None,
            f"Дисконт {discount:.0%} > {MAX_DISCOUNT:.0%} — НМЦК подозрительна "
            f"(возможно рамочный лимит или ошибка парсинга). Проверьте вручную.",
            discount,
        )
    return nmck, None, discount


# ── Public matching functions ───────────────────────────────────


def match_profile_to_analysis(
    profile: SupplierProfile,
    tender: Tender,
    analysis: TenderAnalysis | None,
) -> MatchResult:
    """Compute match score between a profile and a tender analysis.

    Pure function — no DB. Used by the matcher worker and the API layer.
    """
    breakdown = MatchBreakdown()
    reasons: list[str] = []

    # Pull fields safely
    profile_okpd2 = profile.okpd2_codes or []
    profile_regions = profile.regions or []
    profile_licenses = profile.licenses or []
    profile_min = profile.min_contract_sum
    profile_max = profile.max_contract_sum
    profile_max_guarantee = profile.max_guarantee_sum
    profile_procedure_types = profile.allowed_procedure_types or []

    tender_okpd2 = (analysis.okpd2_extracted if analysis else None) or []
    tender_regions = (analysis.regions_extracted if analysis else None) or []
    tender_region = tender.region or (tender_regions[0] if tender_regions else None)
    tender_requirements = (analysis.requirements if analysis else None) or {}
    tender_financial = (analysis.financial if analysis else None) or {}
    tender_deadlines = (analysis.deadlines if analysis else None) or {}
    tender_nmck = tender_financial.get("nmck_rub") or tender.price

    contract_price = tender.price  # final price if available (rare for active)
    days = None
    if tender.deadline:
        days = _days_until_deadline(tender.deadline)
    elif tender_deadlines.get("submission"):
        try:
            from dateutil.parser import parse as _parse
            sub = _parse(tender_deadlines["submission"])
            days = (sub - datetime.utcnow()).days
        except Exception:
            pass

    # 1) ОКПД2
    if profile_okpd2 and tender_okpd2:
        matched = [c for c in profile_okpd2 if any(_code_matches(c, t) for t in tender_okpd2)]
        if matched:
            breakdown.okpd2_score = 30
            breakdown.okpd2_reason = (
                f"Совпадение ОКПД2: {', '.join(matched[:3])}"
            )
        else:
            breakdown.okpd2_score = 0
            breakdown.okpd2_reason = (
                f"ОКПД2 тендера ({', '.join(tender_okpd2[:2])}...) не в вашем списке"
            )
            reasons.append("ОКПД2 не совпадает")
    elif not profile_okpd2:
        breakdown.okpd2_score = 0
        breakdown.okpd2_reason = "Профиль: ОКПД2 не указаны"
    else:
        breakdown.okpd2_score = 0
        breakdown.okpd2_reason = "ОКПД2 тендера не извлечены"

    # 2) Регион
    if profile_regions and tender_region:
        if _region_matches(profile_regions, tender_region):
            breakdown.region_score = 20
            breakdown.region_reason = f"Регион совпадает: {tender_region}"
        else:
            breakdown.region_score = 0
            breakdown.region_reason = f"Регион {tender_region} вне вашего списка"
            reasons.append("Регион не совпадает")
    elif not profile_regions:
        breakdown.region_score = 10  # neutral
        breakdown.region_reason = "Регионы в профиле не указаны"
    else:
        breakdown.region_score = 0
        breakdown.region_reason = "Регион тендера не указан"

    # 3) Сумма
    nmck_for_scoring, outlier_warning, discount = _apply_anti_outlier(
        tender_nmck, contract_price
    )
    breakdown.discount = discount
    breakdown.nmck_outlier_warning = outlier_warning

    if outlier_warning:
        breakdown.price_score_excluded = True
        breakdown.price_score = 0
        breakdown.price_reason = outlier_warning
        reasons.append("НМЦК подозрительна — проверьте вручную")
    elif profile_min is None and profile_max is None:
        breakdown.price_score = 20
        breakdown.price_reason = "Лимиты по сумме не заданы"
    elif tender_nmck is None:
        breakdown.price_score = 10
        breakdown.price_reason = "НМЦК не указана — нейтральная оценка"
    else:
        in_range = True
        if profile_min is not None and tender_nmck < profile_min:
            in_range = False
            reasons.append(f"НМЦК {tender_nmck:,.0f} ₽ ниже минимума {profile_min:,.0f} ₽")
        if profile_max is not None and tender_nmck > profile_max:
            in_range = False
            reasons.append(f"НМЦК {tender_nmck:,.0f} ₽ выше максимума {profile_max:,.0f} ₽")
        if in_range:
            breakdown.price_score = 20
            breakdown.price_reason = f"НМЦК {tender_nmck:,.0f} ₽ в диапазоне профиля"
        else:
            breakdown.price_score = 0
            breakdown.price_reason = "Сумма вне диапазона"

    # 4) Лицензии
    lic_score, lic_reason = _check_licenses(profile_licenses, tender_requirements)
    breakdown.licenses_score = lic_score
    breakdown.licenses_reason = lic_reason
    if lic_score < 20:
        reasons.append("Требуются лицензии, которых нет в профиле")

    # 5) Время
    if days is None:
        breakdown.time_score = 0
        breakdown.time_reason = "Дедлайн не указан"
    elif days < DEADLINE_CRITICAL_DAYS:
        breakdown.time_score = 0
        breakdown.time_reason = f"Дедлайн через {days} дн. — критично мало"
        reasons.append(f"Дедлайн через {days} дн. (< {DEADLINE_CRITICAL_DAYS})")
    elif days < DEADLINE_REVIEW_DAYS:
        breakdown.time_score = 5
        breakdown.time_reason = f"Дедлайн через {days} дн. — мало времени"
    else:
        breakdown.time_score = 10
        breakdown.time_reason = f"Дедлайн через {days} дн. — времени достаточно"

    # Verdict logic
    score = breakdown.total()
    no_match = (
        breakdown.okpd2_score == 0
        or breakdown.region_score == 0
        or breakdown.price_score_excluded  # outlier
    )
    if no_match or score < REVIEW_MIN_SCORE:
        verdict = VERDICT_NO_MATCH
    elif (
        outlier_warning
        or breakdown.licenses_score < 20
        or (days is not None and days < DEADLINE_REVIEW_DAYS)
    ):
        verdict = VERDICT_REVIEW
    elif score >= MATCH_MIN_SCORE:
        verdict = VERDICT_MATCH
    else:
        verdict = VERDICT_REVIEW

    return MatchResult(verdict=verdict, score=score, breakdown=breakdown, reasons=reasons)


async def match_tender_for_user(
    user_id: str,
    tender_id: str,
    db: AsyncSession | None = None,
) -> MatchResult | None:
    """Compute match for one user-tender pair, loading profile + analysis from DB."""
    async def _run(session: AsyncSession) -> MatchResult | None:
        prof_q = await session.execute(
            select(SupplierProfile).where(SupplierProfile.user_id == user_id)
        )
        profile = prof_q.scalar_one_or_none()
        if profile is None:
            return None

        ten_q = await session.execute(
            select(Tender).where(Tender.id == tender_id)
        )
        tender = ten_q.scalar_one_or_none()
        if tender is None:
            return None

        # Latest current analysis
        ana_q = await session.execute(
            select(TenderAnalysis)
            .where(TenderAnalysis.tender_id == tender_id)
            .where(TenderAnalysis.is_current == 1)
            .order_by(TenderAnalysis.analyzed_at.desc())
            .limit(1)
        )
        analysis = ana_q.scalar_one_or_none()

        return match_profile_to_analysis(profile, tender, analysis)

    if db is not None:
        return await _run(db)
    async with async_session() as session:
        return await _run(session)


async def match_all_subscriptions() -> int:
    """For every active subscription × every recent tender, compute match
    and insert SubscriptionMatch rows.

    Returns number of new matches created.

    Note: this is the legacy keyword-based matcher. The new profile-based
    matcher (h025ai-10) is used by the tender-detail endpoint; this
    function is kept for the subscription feed (matched by keywords).
    """
    from datetime import timedelta

    total_matches = 0
    async with async_session() as db:
        result = await db.execute(
            select(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)
        )
        subscriptions = result.scalars().all()
        if not subscriptions:
            return 0

        week_ago = datetime.utcnow() - timedelta(days=7)
        tenders_result = await db.execute(
            select(Tender).where(Tender.created_at >= week_ago)
        )
        tenders = tenders_result.scalars().all()

        for sub in subscriptions:
            for tender in tenders:
                score = _keyword_score(sub, tender)
                if score > 30:
                    existing = await db.execute(
                        select(SubscriptionMatch).where(
                            SubscriptionMatch.subscription_id == sub.id,
                            SubscriptionMatch.tender_id == tender.id,
                        )
                    )
                    if not existing.scalar_one_or_none():
                        db.add(
                            SubscriptionMatch(
                                subscription_id=sub.id,
                                tender_id=tender.id,
                                relevance_score=round(score, 1),
                            )
                        )
                        total_matches += 1
        if total_matches > 0:
            await db.commit()

    logger.info("Matcher (keywords): created %d new matches", total_matches)
    return total_matches


def _keyword_score(sub: Subscription, tender: Tender) -> float:
    """Legacy keyword-based score (0-100), used for the subscription feed."""
    import json as _json

    score = 0.0
    if sub.keywords:
        try:
            keywords = _json.loads(sub.keywords)
        except (ValueError, TypeError):
            keywords = []
        if keywords:
            text = (
                f"{tender.title or ''} {tender.description or ''} {tender.customer or ''}"
            ).lower()
            hits = sum(
                1
                for kw in keywords
                if kw.lower() in text
                or SequenceMatcher(None, kw.lower(), text).ratio() > 0.3
            )
            if keywords:
                score += (hits / len(keywords)) * 50

    if sub.law_type and tender.law_type and sub.law_type == tender.law_type:
        score += 20
    if tender.price:
        if sub.price_min is not None and tender.price >= sub.price_min:
            score += 10
        if sub.price_max is not None and tender.price <= sub.price_max:
            score += 10
        if sub.price_min is None and sub.price_max is None:
            score += 5
    if sub.region and tender.region and sub.region.lower() in (tender.region or "").lower():
        score += 20
    return min(score, 100.0)


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "DEADLINE_CRITICAL_DAYS",
    "DEADLINE_REVIEW_DAYS",
    "MATCH_MIN_SCORE",
    "MAX_DISCOUNT",
    "MatchBreakdown",
    "MatchResult",
    "REVIEW_MIN_SCORE",
    "VERDICT_MATCH",
    "VERDICT_NO_MATCH",
    "VERDICT_REVIEW",
    "match_all_subscriptions",
    "match_profile_to_analysis",
    "match_tender_for_user",
]
