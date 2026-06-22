"""Admin router: statistics, users, tariffs, settings management."""
import json
import secrets
from datetime import datetime
from typing import Any

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    AdminSettings,
    Subscription,
    SubscriptionMatch,
    Tariff,
    Tender,
    User,
    UserRole,
    TariffName,
)
from ..routers.auth import get_admin_user

router = APIRouter()


# ── Pydantic Schemas ───────────────────────────────────────────


class UserUpdateRequest(BaseModel):
    role: str | None = None
    tariff: str | None = None
    is_active: bool | None = None
    name: str | None = None


class TariffCreateRequest(BaseModel):
    name: str
    display_name: str
    price_monthly: int
    tender_limit: int
    subscription_limit: int
    ai_analysis_type: str = "basic"
    is_active: bool = True


class TariffUpdateRequest(BaseModel):
    display_name: str | None = None
    price_monthly: int | None = None
    tender_limit: int | None = None
    subscription_limit: int | None = None
    ai_analysis_type: str | None = None
    is_active: bool | None = None


class PlategaSettingsRequest(BaseModel):
    merchant_id: str
    secret_key: str
    webhook_url: str | None = None


# ── Helpers ────────────────────────────────────────────────────


def _serialize_user(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "company": u.company,
        "role": u.role,
        "tariff": u.tariff,
        "monthly_limit": u.monthly_limit,
        "tenders_viewed_this_month": u.tenders_viewed_this_month,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _serialize_tariff(t: Tariff) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "display_name": t.display_name,
        "price_monthly": t.price_monthly,
        "tender_limit": t.tender_limit,
        "subscription_limit": t.subscription_limit,
        "ai_analysis_type": t.ai_analysis_type,
        "is_active": t.is_active,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _get_or_create_settings(db: AsyncSession) -> dict[str, str]:
    """Get Platega settings as dict, creating defaults if missing."""
    result = await db.execute(select(AdminSettings))
    rows = result.scalars().all()
    data = {r.key: r.value for r in rows}
    for key in ("merchant_id", "secret_key", "webhook_url"):
        if key not in data:
            s = AdminSettings(key=key, value="")
            db.add(s)
            data[key] = ""
    if not rows:
        await db.flush()
    return data


def _activation_guide() -> str:
    return """# Инструкция по подключению Platega

## Шаг 1: Регистрация в Platega
1. Перейдите на https://platega.com
2. Зарегистрируйтесь как мерчант
3. Получите Merchant ID и Secret Key в личном кабинете

## Шаг 2: Настройка Webhook
1. В личном кабинете Platega укажите URL для webhook:
   https://tenders.ivoryhome.ru/api/billing/webhook
2. Активируйте webhook

## Шаг 3: Настройка в TenderSearch
1. Введите Merchant ID и Secret Key в форму ниже
2. Нажмите «Сохранить»
3. Выполните тестовый платёж на 1₽ для проверки

## Шаг 4: Проверка
1. Создайте тестовую подписку
2. Оплатите через Platega
3. Убедитесь что тариф обновился в ЛК
"""


# ── Stats ──────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get platform statistics."""
    users_count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    users_active = (await db.execute(select(func.count()).select_from(User).where(User.is_active == True))).scalar() or 0
    tenders_count = (await db.execute(select(func.count()).select_from(Tender))).scalar() or 0
    subs_count = (await db.execute(select(func.count()).select_from(Subscription))).scalar() or 0
    subs_active = (await db.execute(select(func.count()).select_from(Subscription).where(Subscription.status == "active"))).scalar() or 0
    matches_count = (await db.execute(select(func.count()).select_from(SubscriptionMatch))).scalar() or 0

    # Tenders with AI analysis
    ai_analyzed = (
        await db.execute(
            select(func.count()).select_from(Tender).where(Tender.ai_analyzed_at.is_not(None))
        )
    ).scalar() or 0

    # Users by tariff
    tariff_counts = {}
    tariff_rows = await db.execute(
        select(User.tariff, func.count()).group_by(User.tariff)
    )
    for row in tariff_rows:
        tariff_counts[row[0]] = row[1]

    # Tenders by law type
    law_counts = {}
    law_rows = await db.execute(
        select(Tender.law_type, func.count()).group_by(Tender.law_type)
    )
    for row in law_rows:
        law_counts[row[0] or "unknown"] = row[1]

    # Recent registrations (last 7 days)
    seven_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    seven_days_ago = seven_days_ago - timedelta(days=7)
    new_users = (
        await db.execute(
            select(func.count()).select_from(User).where(User.created_at >= seven_days_ago)
        )
    ).scalar() or 0

    return {
        "users_total": users_count,
        "users_active": users_active,
        "users_new_7d": new_users,
        "tenders_total": tenders_count,
        "subscriptions_total": subs_count,
        "subscriptions_active": subs_active,
        "matches_total": matches_count,
        "ai_analyzed_total": ai_analyzed,
        "users_by_tariff": tariff_counts,
        "tenders_by_law": law_counts,
    }


# ── Users ──────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    search: str = Query("", description="Search by email or name"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List all users with search and pagination (admin only)."""
    query = select(User)

    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(User.email.ilike(pattern), User.name.ilike(pattern))
        )

    # Count total
    count_q = select(func.count()).select_from(User)
    if search:
        count_q = count_q.where(
            or_(User.email.ilike(pattern), User.name.ilike(pattern))
        )
    total = (await db.execute(count_q)).scalar() or 0

    # Paginate
    query = query.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    users = result.scalars().all()

    return {
        "items": [_serialize_user(u) for u in users],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page) if total > 0 else 1,
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user details (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Count user's subscriptions
    subs_count = (
        await db.execute(
            select(func.count()).select_from(Subscription).where(Subscription.user_id == user.id)
        )
    ).scalar() or 0

    data = _serialize_user(user)
    data["subscriptions_count"] = subs_count
    return data


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    data: UserUpdateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user role, tariff, status (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.role is not None:
        if data.role not in (UserRole.USER, UserRole.ADMIN):
            raise HTTPException(status_code=400, detail=f"Invalid role: {data.role}")
        user.role = data.role

    if data.tariff is not None:
        # Validate tariff exists
        tariff_result = await db.execute(select(Tariff).where(Tariff.name == data.tariff))
        if not tariff_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Unknown tariff: {data.tariff}")
        user.tariff = data.tariff

    if data.is_active is not None:
        user.is_active = data.is_active

    if data.name is not None:
        user.name = data.name

    user.updated_at = datetime.utcnow()
    db.add(user)
    await db.flush()

    return _serialize_user(user)


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset user password to a random one (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_password = secrets.token_urlsafe(12)
    user.hashed_password = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    db.add(user)
    await db.flush()

    return {
        "message": "Password reset successful",
        "new_password": new_password,
        "user_id": str(user.id),
    }


# ── Tariffs ────────────────────────────────────────────────────


@router.get("/tariffs")
async def list_tariffs(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all tariffs (admin only)."""
    result = await db.execute(select(Tariff).order_by(Tariff.price_monthly))
    tariffs = result.scalars().all()
    return [_serialize_tariff(t) for t in tariffs]


@router.post("/tariffs", status_code=201)
async def create_tariff(
    data: TariffCreateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tariff (admin only)."""
    # Check name uniqueness
    existing = await db.execute(select(Tariff).where(Tariff.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Tariff '{data.name}' already exists")

    tariff = Tariff(
        name=data.name,
        display_name=data.display_name,
        price_monthly=data.price_monthly,
        tender_limit=data.tender_limit,
        subscription_limit=data.subscription_limit,
        ai_analysis_type=data.ai_analysis_type,
        is_active=data.is_active,
    )
    db.add(tariff)
    await db.flush()
    await db.refresh(tariff)
    return _serialize_tariff(tariff)


@router.patch("/tariffs/{tariff_id}")
async def update_tariff(
    tariff_id: str,
    data: TariffUpdateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing tariff (admin only)."""
    result = await db.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    if data.display_name is not None:
        tariff.display_name = data.display_name
    if data.price_monthly is not None:
        tariff.price_monthly = data.price_monthly
    if data.tender_limit is not None:
        tariff.tender_limit = data.tender_limit
    if data.subscription_limit is not None:
        tariff.subscription_limit = data.subscription_limit
    if data.ai_analysis_type is not None:
        tariff.ai_analysis_type = data.ai_analysis_type
    if data.is_active is not None:
        tariff.is_active = data.is_active

    db.add(tariff)
    await db.flush()
    return _serialize_tariff(tariff)


@router.delete("/tariffs/{tariff_id}")
async def delete_tariff(
    tariff_id: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a tariff (admin only). Cannot delete 'free' tariff."""
    result = await db.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")

    if tariff.name == TariffName.FREE:
        raise HTTPException(status_code=400, detail="Cannot delete the 'free' tariff")

    await db.delete(tariff)
    await db.flush()
    return {"message": f"Tariff '{tariff.name}' deleted"}


# ── Platega Settings ───────────────────────────────────────────


@router.get("/settings")
async def get_settings(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Platega payment settings (admin only)."""
    data = await _get_or_create_settings(db)
    return data


@router.put("/settings")
async def update_settings(
    settings_data: PlategaSettingsRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Platega payment settings (admin only)."""
    fields = {
        "merchant_id": settings_data.merchant_id,
        "secret_key": settings_data.secret_key,
        "webhook_url": settings_data.webhook_url or "",
    }

    # Upsert each field
    for key, value in fields.items():
        result = await db.execute(
            select(AdminSettings).where(AdminSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
            db.add(row)
        else:
            db.add(AdminSettings(key=key, value=value))

    await db.flush()
    return {"message": "Settings saved", "settings": fields}


# ── Activation Guide ───────────────────────────────────────────


@router.get("/activation-guide")
async def get_activation_guide(
    admin: User = Depends(get_admin_user),
):
    """Get the Platega activation instructions (admin only)."""
    return {"guide": _activation_guide()}
