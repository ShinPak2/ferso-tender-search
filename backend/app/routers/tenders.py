"""Tenders router: CRUD, search, AI analysis."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession


def _safe_str(value):
    """Convert list to string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else None


from ..database import get_db
from ..models import Document, Tender, User
from ..routers.auth import get_current_user
from ..services.ai import analyze_tender

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────


class TenderListResponse(BaseModel):
    id: str
    title: str
    customer: str | None
    price: float | None
    deadline: str | None
    law_type: str | None
    region: str | None
    ai_relevance: int | None
    published_at: str | None

    class Config:
        from_attributes = True


class TenderDetailResponse(BaseModel):
    id: str
    external_id: str | None
    title: str
    description: str | None
    customer: str | None
    price: float | None
    currency: str
    deadline: str | None
    law_type: str | None
    region: str | None
    status: str
    source_url: str | None
    published_at: str | None
    ai_analysis: str | None
    ai_relevance: int | None
    ai_risks: str | None
    ai_recommendation: str | None
    documents: list[dict]

    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    id: str
    name: str
    file_url: str | None
    file_type: str | None

    class Config:
        from_attributes = True


class PaginatedTenders(BaseModel):
    items: list[TenderListResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ── Helpers ────────────────────────────────────────────────────────


def serialize_tender(tender: Tender) -> dict:
    return {
        "id": str(tender.id),
        "title": tender.title,
        "customer": tender.customer,
        "price": tender.price,
        "deadline": tender.deadline.isoformat() if tender.deadline else None,
        "law_type": tender.law_type,
        "region": tender.region,
        "ai_relevance": tender.ai_relevance,
        "published_at": tender.published_at.isoformat() if tender.published_at else None,
    }


def serialize_tender_detail(tender: Tender) -> dict:
    return {
        "id": str(tender.id),
        "external_id": tender.external_id,
        "title": tender.title,
        "description": tender.description,
        "customer": tender.customer,
        "price": tender.price,
        "currency": tender.currency or "RUB",
        "deadline": tender.deadline.isoformat() if tender.deadline else None,
        "law_type": tender.law_type,
        "region": tender.region,
        "status": tender.status or "active",
        "source_url": tender.source_url,
        "published_at": tender.published_at.isoformat() if tender.published_at else None,
        "ai_analysis": tender.ai_analysis,
        "ai_relevance": tender.ai_relevance,
        "ai_risks": tender.ai_risks,
        "ai_recommendation": tender.ai_recommendation,
        "documents": [
            {
                "id": str(d.id),
                "name": d.name,
                "file_url": d.file_url,
                "file_type": d.file_type,
            }
            for d in (tender.documents or [])
        ],
    }


# ── Routes ─────────────────────────────────────────────────────────


@router.get("")
async def list_tenders(
    q: str | None = Query(None, description="Search query"),
    law_type: str | None = Query(None, description="44-ФЗ or 223-ФЗ"),
    price_min: float | None = Query(None),
    price_max: float | None = Query(None),
    region: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search and list tenders with filters."""
    query = select(Tender)

    # Filters
    conditions = []
    if q:
        conditions.append(
            or_(
                Tender.title.ilike(f"%{q}%"),
                Tender.description.ilike(f"%{q}%"),
                Tender.customer.ilike(f"%{q}%"),
            )
        )
    if law_type:
        conditions.append(Tender.law_type == law_type)
    if price_min is not None:
        conditions.append(Tender.price >= price_min)
    if price_max is not None:
        conditions.append(Tender.price <= price_max)
    if region:
        conditions.append(Tender.region.ilike(f"%{region}%"))

    if conditions:
        query = query.where(*conditions)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(desc(Tender.published_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    tenders = result.scalars().all()

    pages = max(1, (total + page_size - 1) // page_size)

    return PaginatedTenders(
        items=[TenderListResponse(**serialize_tender(t)) for t in tenders],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/{tender_id}")
async def get_tender(
    tender_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tender detail with AI analysis. Runs analysis if not yet done."""
    result = await db.execute(select(Tender).where(Tender.id == tender_id))
    tender = result.scalar_one_or_none()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")

    # Run AI analysis if needed
    if not tender.ai_analysis and tender.description:
        try:
            analysis = await analyze_tender(tender.title, tender.description)
            if analysis:
                tender.ai_analysis = analysis.get("analysis")
                tender.ai_relevance = analysis.get("relevance")
                tender.ai_risks = _safe_str(analysis.get("risks"))
                tender.ai_recommendation = analysis.get("recommendation")
                tender.ai_analyzed_at = func.now()
                db.add(tender)
                await db.flush()
                await db.refresh(tender)
        except Exception:
            pass  # AI analysis is non-blocking

    # Track view for limits
    if current_user.tenders_viewed_this_month < current_user.monthly_limit:
        current_user.tenders_viewed_this_month += 1
        db.add(current_user)
        await db.flush()

    return serialize_tender_detail(tender)


@router.get("/{tender_id}/documents")
async def get_tender_documents(
    tender_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get list of documents for a tender."""
    result = await db.execute(select(Document).where(Document.tender_id == tender_id))
    docs = result.scalars().all()
    return [
        DocumentResponse(
            id=str(d.id),
            name=d.name,
            file_url=d.file_url,
            file_type=d.file_type,
        )
        for d in docs
    ]
