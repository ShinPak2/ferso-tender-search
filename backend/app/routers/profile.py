"""Supplier profile router — h025ai-13 / h025ai-15.

Endpoints:
  GET  /api/profile/me           — current user's supplier profile
  POST /api/profile              — create or update profile (full)
  PATCH /api/profile             — partial update
  GET  /api/profile/egrul/{inn}  — fetch EGRUL data via DaMIA (h025ai-15)
  POST /api/profile/refresh-egrul — force re-fetch (clears cache)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User
from ..models.supplier_profile import SupplierProfile
from ..routers.auth import get_current_user
from ..services import damia_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────


class LicenseItem(BaseModel):
    type: str
    level: str | None = None
    number: str | None = None
    issued_at: str | None = None
    valid_until: str | None = None


class ProfileIn(BaseModel):
    inn: str | None = Field(None, max_length=12)
    ogrn: str | None = Field(None, max_length=15)
    kpp: str | None = Field(None, max_length=9)
    legal_name: str | None = None
    legal_address: str | None = None
    okpd2_codes: list[str] = []
    okved2_codes: list[str] = []
    regions: list[str] = []
    licenses: list[LicenseItem] = []
    min_contract_sum: float | None = None
    max_contract_sum: float | None = None
    max_guarantee_sum: float | None = None
    allowed_procedure_types: list[str] = []


class ProfileOut(BaseModel):
    inn: str | None
    ogrn: str | None
    kpp: str | None
    legal_name: str | None
    legal_address: str | None
    okpd2_codes: list[str]
    okved2_codes: list[str]
    regions: list[str]
    licenses: list[dict[str, Any]]
    min_contract_sum: float | None
    max_contract_sum: float | None
    max_guarantee_sum: float | None
    allowed_procedure_types: list[str]
    egrul_cached_at: str | None
    manually_edited: bool


# ── Helpers ──────────────────────────────────────────────────────


def _serialize(profile: SupplierProfile) -> dict[str, Any]:
    return {
        "inn": profile.inn,
        "ogrn": profile.ogrn,
        "kpp": profile.kpp,
        "legal_name": profile.legal_name,
        "legal_address": profile.legal_address,
        "okpd2_codes": profile.okpd2_codes or [],
        "okved2_codes": profile.okved2_codes or [],
        "regions": profile.regions or [],
        "licenses": profile.licenses or [],
        "min_contract_sum": profile.min_contract_sum,
        "max_contract_sum": profile.max_contract_sum,
        "max_guarantee_sum": profile.max_guarantee_sum,
        "allowed_procedure_types": profile.allowed_procedure_types or [],
        "egrul_cached_at": (
            profile.egrul_cached_at.isoformat() if profile.egrul_cached_at else None
        ),
        "manually_edited": bool(profile.manually_edited),
    }


async def _get_or_create_profile(
    user: User, db: AsyncSession
) -> SupplierProfile:
    result = await db.execute(
        select(SupplierProfile).where(SupplierProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = SupplierProfile(user_id=user.id)
        db.add(profile)
        await db.flush()
    return profile


# ── Routes ───────────────────────────────────────────────────────


@router.get("/me", response_model=ProfileOut)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's supplier profile (create empty if missing)."""
    profile = await _get_or_create_profile(current_user, db)
    await db.commit()
    return _serialize(profile)


@router.post("", response_model=ProfileOut)
async def upsert_profile(
    payload: ProfileIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the supplier profile."""
    profile = await _get_or_create_profile(current_user, db)

    profile.inn = payload.inn
    profile.ogrn = payload.ogrn
    profile.kpp = payload.kpp
    profile.legal_name = payload.legal_name
    profile.legal_address = payload.legal_address
    profile.okpd2_codes = payload.okpd2_codes
    profile.okved2_codes = payload.okved2_codes
    profile.regions = payload.regions
    profile.licenses = [l.model_dump() for l in payload.licenses]
    profile.min_contract_sum = payload.min_contract_sum
    profile.max_contract_sum = payload.max_contract_sum
    profile.max_guarantee_sum = payload.max_guarantee_sum
    profile.allowed_procedure_types = payload.allowed_procedure_types
    profile.manually_edited = 1

    await db.flush()
    await db.commit()
    await db.refresh(profile)
    return _serialize(profile)


@router.patch("", response_model=ProfileOut)
async def patch_profile(
    payload: ProfileIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update — same payload as POST but only non-None fields applied."""
    profile = await _get_or_create_profile(current_user, db)

    updates = payload.model_dump(exclude_unset=True)
    licenses = updates.pop("licenses", None)
    for k, v in updates.items():
        if v is not None:
            setattr(profile, k, v)
    if licenses is not None:
        profile.licenses = licenses
        profile.manually_edited = 1

    await db.flush()
    await db.commit()
    await db.refresh(profile)
    return _serialize(profile)


@router.get("/egrul/{inn}")
async def get_egrul(
    inn: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch EGRUL data by INN via DaMIA-ФНС (h025ai-15).

    Behavior:
      - DAMIA_API_KEY not set → 503 with graceful message
      - INN not found → 404
      - DaMIA down → 503 with retry hint
      - Otherwise → 200 with normalized EgrulCompany data
        and stored in supplier_profiles (egrul_data, egrul_cached_at).
    """
    if not damia_client.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Сервис ЕГРЮЛ временно недоступен. Попробуйте позже или "
            "заполните реквизиты вручную.",
        )

    try:
        company = await damia_client.fetch_company_by_inn(inn)
    except damia_client.DamiaRateLimitError:
        raise HTTPException(
            status_code=429, detail="Слишком много запросов. Подождите немного."
        )
    except damia_client.DamiaTransientError:
        raise HTTPException(
            status_code=503,
            detail="Сервис ЕГРЮЛ временно недоступен. Попробуйте позже.",
        )

    if company is None:
        raise HTTPException(
            status_code=404, detail=f"Компания с ИНН {inn} не найдена в ЕГРЮЛ"
        )

    # Persist to profile for next time
    profile = await _get_or_create_profile(current_user, db)
    profile.inn = company.inn
    profile.ogrn = company.ogrn
    profile.kpp = company.kpp
    profile.legal_name = company.legal_name
    profile.legal_address = company.legal_address
    profile.okved2_codes = company.okved2_codes
    profile.okpd2_codes = (
        profile.okpd2_codes or company.okpd2_suggested
    )  # don't clobber manual edits
    profile.egrul_data = company.raw
    from datetime import datetime as _dt

    profile.egrul_cached_at = _dt.utcnow()
    await db.commit()

    return company.to_dict()


@router.post("/refresh-egrul")
async def refresh_egrul(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Force-refresh EGRUL data (clears 30-day cache for this user)."""
    profile = await _get_or_create_profile(current_user, db)
    if not profile.inn:
        raise HTTPException(
            status_code=400,
            detail="Сначала укажите ИНН в профиле",
        )

    if not damia_client.is_enabled():
        raise HTTPException(status_code=503, detail="DaMIA API key not configured")

    # Force refetch by skipping cache
    company = await damia_client.fetch_company_by_inn(profile.inn, use_cache=False)
    if company is None:
        raise HTTPException(
            status_code=404, detail=f"ИНН {profile.inn} не найден в ЕГРЮЛ"
        )

    # Re-cache
    from datetime import datetime as _dt

    profile.egrul_data = company.raw
    profile.egrul_cached_at = _dt.utcnow()
    if not profile.manually_edited:
        profile.legal_name = company.legal_name
        profile.legal_address = company.legal_address
        profile.okved2_codes = company.okved2_codes
        profile.ogrn = company.ogrn
        profile.kpp = company.kpp
    await db.commit()

    return company.to_dict()


@router.get("/damia-health")
async def damia_health(
    current_user: User = Depends(get_current_user),
):
    """Diagnostic: DaMIA configuration status (admin/monitoring)."""
    return await damia_client.healthcheck()


# ── Module exports ───────────────────────────────────────────────

__all__ = ["router"]
