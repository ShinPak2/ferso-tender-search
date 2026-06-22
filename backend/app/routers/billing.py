"""Billing router: tariffs, payments."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import User
from ..routers.auth import get_current_user

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────


class TariffResponse(BaseModel):
    name: str
    display_name: str
    price_monthly: int
    tender_limit: int
    subscription_limit: int
    ai_analysis_type: str


TARIFFS = [
    TariffResponse(
        name="free",
        display_name="Free",
        price_monthly=0,
        tender_limit=10,
        subscription_limit=1,
        ai_analysis_type="basic",
    ),
    TariffResponse(
        name="pro",
        display_name="Pro",
        price_monthly=1990,
        tender_limit=100,
        subscription_limit=5,
        ai_analysis_type="full",
    ),
    TariffResponse(
        name="business",
        display_name="Business",
        price_monthly=4990,
        tender_limit=500,
        subscription_limit=20,
        ai_analysis_type="priority",
    ),
]


class PaymentCreate(BaseModel):
    tariff_name: str


# ── Routes ─────────────────────────────────────────────────────────


@router.get("/tariffs")
async def get_tariffs():
    """Get all available tariffs (public)."""
    return [t.dict() for t in TARIFFS]


@router.post("/billing/create-payment")
async def create_payment(
    data: PaymentCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a payment for tariff upgrade (stub - integrates with payment gateway)."""
    valid_tariffs = {"free", "pro", "business"}
    if data.tariff_name not in valid_tariffs:
        return {"error": "Invalid tariff"}

    tariff = next((t for t in TARIFFS if t.name == data.tariff_name), None)
    if not tariff:
        return {"error": "Tariff not found"}

    # Stub: in production would create a payment link with YooKassa/Stripe
    return {
        "ok": True,
        "tariff": tariff.dict(),
        "message": f"Payment link for {tariff.display_name} would be generated here",
        "payment_url": f"/dashboard/plan?t={data.tariff_name}",
    }


@router.post("/billing/webhook")
async def billing_webhook():
    """Payment webhook (stub - receives payment confirmations)."""
    # In production: validate webhook signature, update user tariff
    return {"ok": True}
