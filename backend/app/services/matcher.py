"""Subscription-tender matcher service."""
import json
import logging
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session
from ..models import Subscription, SubscriptionMatch, SubscriptionStatus, Tender

logger = logging.getLogger(__name__)


async def match_all_subscriptions() -> int:
    """
    Match all active subscriptions against recent tenders.
    Returns number of new matches created.
    """
    total_matches = 0

    async with async_session() as db:
        # Get all active subscriptions
        result = await db.execute(
            select(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)
        )
        subscriptions = result.scalars().all()

        # Get recent tenders (last 7 days)
        from datetime import datetime, timedelta
        week_ago = datetime.utcnow() - timedelta(days=7)

        tenders_result = await db.execute(
            select(Tender).where(Tender.created_at >= week_ago)
        )
        tenders = tenders_result.scalars().all()

        for sub in subscriptions:
            for tender in tenders:
                score = _calculate_match_score(sub, tender)
                if score > 30:  # Threshold for matching
                    # Check if match already exists
                    existing = await db.execute(
                        select(SubscriptionMatch).where(
                            SubscriptionMatch.subscription_id == sub.id,
                            SubscriptionMatch.tender_id == tender.id,
                        )
                    )
                    if not existing.scalar_one_or_none():
                        match = SubscriptionMatch(
                            subscription_id=sub.id,
                            tender_id=tender.id,
                            relevance_score=round(score, 1),
                        )
                        db.add(match)
                        total_matches += 1

        if total_matches > 0:
            await db.commit()

    logger.info(f"Matcher: created {total_matches} new matches")
    return total_matches


def _calculate_match_score(sub: Subscription, tender: Tender) -> float:
    """Calculate relevance score between a subscription and a tender (0-100)."""
    score = 0.0

    # Keyword matching (most important)
    if sub.keywords:
        try:
            keywords = json.loads(sub.keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = []

        if keywords:
            text = f"{tender.title or ''} {tender.description or ''} {tender.customer or ''}".lower()
            keyword_hits = 0
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in text:
                    # Fuzzy match bonus
                    ratio = SequenceMatcher(None, kw_lower, text).ratio()
                    keyword_hits += 1 if ratio > 0.3 else 0

            if keywords:
                score += (keyword_hits / len(keywords)) * 50

    # Law type matching
    if sub.law_type and tender.law_type:
        if sub.law_type == tender.law_type:
            score += 20

    # Price range matching
    if tender.price:
        if sub.price_min is not None and tender.price >= sub.price_min:
            score += 10
        if sub.price_max is not None and tender.price <= sub.price_max:
            score += 10
        if sub.price_min is None and sub.price_max is None:
            score += 5  # No price filter = neutral

    # Region matching
    if sub.region and tender.region:
        if sub.region.lower() in (tender.region or "").lower():
            score += 20

    return min(score, 100.0)
