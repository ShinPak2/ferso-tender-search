"""Subscriptions router: CRUD, matching."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Subscription, SubscriptionMatch, SubscriptionStatus, Tender, User
from ..routers.auth import get_current_user
from ..services.matcher import match_all_subscriptions

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────


class SubscriptionCreate(BaseModel):
    name: str
    keywords: list[str] = []
    law_type: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    region: str | None = None
    notify_email: bool = True


class SubscriptionResponse(BaseModel):
    id: str
    name: str
    keywords: str | None
    law_type: str | None
    price_min: float | None
    price_max: float | None
    region: str | None
    status: str
    notify_email: bool
    match_count: int = 0
    created_at: str | None

    class Config:
        from_attributes = True


class MatchResponse(BaseModel):
    id: str
    tender_id: str
    tender_title: str
    tender_price: float | None
    tender_customer: str | None
    tender_deadline: str | None
    relevance_score: float | None
    is_viewed: bool
    created_at: str | None


# ── Routes ─────────────────────────────────────────────────────────


@router.post("", response_model=SubscriptionResponse, status_code=201)
async def create_subscription(
    data: SubscriptionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new subscription."""
    # Check subscription limits
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    current_count = len(result.scalars().all())

    limits = {"free": 1, "pro": 5, "business": 20}
    max_subs = limits.get(current_user.tariff, 1)

    if current_count >= max_subs:
        raise HTTPException(
            status_code=403,
            detail=f"Subscription limit reached ({max_subs}) for {current_user.tariff} plan",
        )

    sub = Subscription(
        user_id=current_user.id,
        name=data.name,
        keywords=json.dumps(data.keywords, ensure_ascii=False),
        law_type=data.law_type,
        price_min=data.price_min,
        price_max=data.price_max,
        region=data.region,
        notify_email=data.notify_email,
    )
    db.add(sub)
    await db.flush()
    await db.refresh(sub)

    return SubscriptionResponse(
        id=str(sub.id),
        name=sub.name,
        keywords=sub.keywords,
        law_type=sub.law_type,
        price_min=sub.price_min,
        price_max=sub.price_max,
        region=sub.region,
        status=sub.status,
        notify_email=sub.notify_email,
        match_count=0,
        created_at=sub.created_at.isoformat() if sub.created_at else None,
    )


@router.get("")
async def list_subscriptions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's subscriptions with match counts."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subs = result.scalars().all()

    out = []
    for sub in subs:
        count_result = await db.execute(
            select(SubscriptionMatch).where(
                SubscriptionMatch.subscription_id == sub.id
            )
        )
        match_count = len(count_result.scalars().all())

        out.append(
            {
                "id": str(sub.id),
                "name": sub.name,
                "keywords": sub.keywords,
                "law_type": sub.law_type,
                "price_min": sub.price_min,
                "price_max": sub.price_max,
                "region": sub.region,
                "status": sub.status,
                "notify_email": sub.notify_email,
                "match_count": match_count,
                "created_at": sub.created_at.isoformat() if sub.created_at else None,
            }
        )
    return out


@router.delete("/{subscription_id}")
async def delete_subscription(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a subscription (owner only)."""
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == current_user.id,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    await db.delete(sub)
    await db.flush()
    return {"ok": True}


@router.get("/{subscription_id}/matches")
async def get_subscription_matches(
    subscription_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get matched tenders for a subscription."""
    # Verify ownership
    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == current_user.id,
        )
    )
    sub = sub_result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    matches_result = await db.execute(
        select(SubscriptionMatch, Tender)
        .join(Tender, SubscriptionMatch.tender_id == Tender.id)
        .where(SubscriptionMatch.subscription_id == subscription_id)
        .order_by(SubscriptionMatch.relevance_score.desc())
    )
    rows = matches_result.all()

    out = []
    for match, tender in rows:
        out.append(
            {
                "id": str(match.id),
                "tender_id": str(tender.id),
                "tender_title": tender.title,
                "tender_price": tender.price,
                "tender_customer": tender.customer,
                "tender_deadline": tender.deadline.isoformat() if tender.deadline else None,
                "relevance_score": match.relevance_score,
                "is_viewed": match.is_viewed,
                "created_at": match.created_at.isoformat() if match.created_at else None,
            }
        )
    return out
