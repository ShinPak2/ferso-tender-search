"""Tenders router: CRUD, search, AI analysis."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select, or_, update
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
from ..services.matcher import match_tender_for_user

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


# ── h025ai-14: AI analysis + match endpoints ─────────────────


class TenderAnalysisResponse(BaseModel):
    id: str | None = None
    subject: str | None = None
    okpd2_extracted: list[str] | None = None
    regions_extracted: list[str] | None = None
    requirements: dict | None = None
    financial: dict | None = None
    deadlines: dict | None = None
    criteria: list[dict] | None = None
    confidence_score: int | None = None
    citations: dict | None = None
    nmck_outlier_warning: str | None = None
    analyzed_at: str | None = None


@router.get("/{tender_id}/analysis", response_model=TenderAnalysisResponse)
async def get_tender_analysis(
    tender_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the latest TenderAnalysis row for a tender (h025ai-6/14)."""
    from ..models.tender_analysis import TenderAnalysis

    result = await db.execute(
        select(TenderAnalysis)
        .where(TenderAnalysis.tender_id == tender_id)
        .where(TenderAnalysis.is_current == 1)
        .order_by(TenderAnalysis.analyzed_at.desc())
        .limit(1)
    )
    a = result.scalar_one_or_none()
    if a is None:
        return TenderAnalysisResponse()
    return TenderAnalysisResponse(
        id=str(a.id),
        subject=a.subject,
        okpd2_extracted=a.okpd2_extracted,
        regions_extracted=a.regions_extracted,
        requirements=a.requirements,
        financial=a.financial,
        deadlines=a.deadlines,
        criteria=a.criteria,
        confidence_score=a.confidence_score,
        citations=a.citations,
        nmck_outlier_warning=a.nmck_outlier_warning,
        analyzed_at=a.analyzed_at.isoformat() if a.analyzed_at else None,
    )


@router.post("/{tender_id}/analyze")
async def analyze_tender(
    tender_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI analysis of a tender's documents (h025ai-14).

    Looks up TenderDocument rows for the tender, runs AI extraction on each
    pending document, and persists a new TenderAnalysis row (marked is_current=1,
    older rows demoted to is_current=0).
    """
    from ..models.tender_analysis import TenderAnalysis
    from ..models.tender_documents import TenderDocument
    from ..services.ai_extraction import extract_from_document

    tender_result = await db.execute(select(Tender).where(Tender.id == tender_id))
    tender = tender_result.scalar_one_or_none()
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")

    docs_result = await db.execute(
        select(TenderDocument).where(TenderDocument.tender_id == tender_id)
    )
    docs = docs_result.scalars().all()
    if not docs:
        raise HTTPException(
            status_code=400,
            detail="No documents to analyze — wait for parser to download them",
        )

    # Aggregate parsed data across all docs
    aggregated = {
        "subject": None,
        "okpd2_extracted": [],
        "regions_extracted": [],
        "requirements": {"licenses": [], "sro": False, "experience_years": 0},
        "financial": {},
        "deadlines": {},
        "criteria": [],
        "source_pages": [],
        "source_quotes": [],
    }
    max_confidence = 0
    nmck_warning: str | None = None
    nmck_value: float | None = None

    from .document_parser import parse_financial_strict, apply_anti_outlier_guard

    for doc in docs:
        if not doc.extracted_text:
            continue
        try:
            extraction = await extract_from_document(
                doc.extracted_text, tender_id=tender_id
            )
            parsed = extraction.parsed
            max_confidence = max(max_confidence, extraction.confidence)
            # Merge subject (first non-null wins)
            if not aggregated["subject"] and parsed.get("subject"):
                aggregated["subject"] = parsed["subject"]
            # Union of ОКПД2
            for c in parsed.get("okpd2_codes", []) or []:
                if c and c not in aggregated["okpd2_extracted"]:
                    aggregated["okpd2_extracted"].append(c)
            # Citations
            for q in parsed.get("source_quotes", []) or []:
                aggregated["source_quotes"].append(q[:200])
            for p in parsed.get("source_pages", []) or []:
                if p and p not in aggregated["source_pages"]:
                    aggregated["source_pages"].append(p)
            # Merge requirements
            r = parsed.get("requirements") or {}
            for lic in r.get("licenses", []) or []:
                if lic and lic not in aggregated["requirements"]["licenses"]:
                    aggregated["requirements"]["licenses"].append(lic)
            if r.get("sro"):
                aggregated["requirements"]["sro"] = True
            aggregated["requirements"]["experience_years"] = max(
                aggregated["requirements"]["experience_years"],
                r.get("experience_years", 0) or 0,
            )
            # Deadlines
            if parsed.get("deadlines"):
                d = parsed["deadlines"]
                if d.get("submission") and not aggregated["deadlines"].get("submission"):
                    aggregated["deadlines"]["submission"] = d["submission"]
                if d.get("execution_days") and not aggregated["deadlines"].get("execution_days"):
                    aggregated["deadlines"]["execution_days"] = d["execution_days"]
            # Criteria
            for crit in parsed.get("evaluation_criteria", []) or []:
                if crit and crit not in aggregated["criteria"]:
                    aggregated["criteria"].append(crit)

            # Apply STRICT MODE financial from doc
            fin_strict = parse_financial_strict(doc.extracted_text)
            if fin_strict.get("nmck_rub") is not None and nmck_value is None:
                nmck_value = fin_strict["nmck_rub"]
            # Aggregate guarantees (max of all docs)
            for k in ("application_guarantee_rub", "contract_guarantee_rub",
                      "application_guarantee_pct", "contract_guarantee_pct"):
                v = fin_strict.get(k)
                if v is not None and aggregated["financial"].get(k) is None:
                    aggregated["financial"][k] = v

        except Exception as e:
            logger = __import__("logging").getLogger(__name__)
            logger.error("AI extract failed for doc %s: %s", doc.id, e)

    # Apply NMCCK from strict parser into financial block
    if nmck_value is not None:
        # Anti-outlier guard
        effective, warning = apply_anti_outlier_guard(
            nmck=nmck_value, contract_price=tender.price
        )
        if effective is not None:
            aggregated["financial"]["nmck_rub"] = effective
            aggregated["financial"]["nmck_source"] = "strict_parser"
        if warning:
            nmck_warning = warning
    elif not aggregated["financial"].get("nmck_rub"):
        # No NMCCK found — don't fabricate 0
        aggregated["financial"]["nmck_rub"] = None

    # Demote previous current analysis
    await db.execute(
        update(TenderAnalysis)
        .where(TenderAnalysis.tender_id == tender_id)
        .where(TenderAnalysis.is_current == 1)
        .values(is_current=0)
    )

    # Compute next version number
    version_result = await db.execute(
        select(TenderAnalysis.version)
        .where(TenderAnalysis.tender_id == tender_id)
        .order_by(TenderAnalysis.version.desc())
        .limit(1)
    )
    last_version = version_result.scalar() or 0

    a = TenderAnalysis(
        tender_id=tender.id,
        source="deepseek",
        version=last_version + 1,
        is_current=1,
        subject=aggregated["subject"],
        okpd2_extracted=aggregated["okpd2_extracted"] or None,
        regions_extracted=aggregated["regions_extracted"] or None,
        requirements=aggregated["requirements"],
        financial=aggregated["financial"],
        deadlines=aggregated["deadlines"],
        criteria=aggregated["criteria"] or None,
        confidence_score=max_confidence or None,
        citations={
            "source_pages": aggregated["source_pages"],
            "source_quotes": aggregated["source_quotes"],
        },
        nmck_outlier_warning=nmck_warning,
        processing_seconds=None,
    )
    db.add(a)
    await db.flush()
    await db.refresh(a)

    return {"status": "ok", "analysis_id": str(a.id), "version": a.version}


@router.get("/{tender_id}/match")
async def match_tender(
    tender_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compute match verdict + score for current user vs tender (h025ai-10/14).

    Uses h025ai-10 matcher. Returns:
      {
        verdict: 'match' | 'review' | 'no_match',
        score: 0-100,
        breakdown: { okpd2_score, ..., nmck_outlier_warning, discount, ... },
        reasons: [...]
      }
    or null if the user has no supplier profile yet.
    """
    result = await match_tender_for_user(
        user_id=str(current_user.id), tender_id=tender_id, db=db
    )
    if result is None:
        return None
    return result.to_dict()
