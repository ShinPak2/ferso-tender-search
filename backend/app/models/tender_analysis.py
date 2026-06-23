"""Tender analysis model — h025ai-6.

Separate table (instead of JSON-in-Tender.ai_analysis) for:
  - Structured query (find tenders by OKPD2 / region / price band)
  - Indexing (GIN on okpd2_extracted, regions_extracted, financial->>'nmck')
  - Independent versioning (re-run analysis, compare old/new)
  - Cleaner relations to AI-extraction per document

Schema derived from SPEC.md §8.2 (AI-extraction JSON shape) and
research/zakupki-html-recon.md (NMMCK coverage only 4-5% — must be nullable).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..database import Base


class TenderAnalysis(Base):
    """Structured AI analysis of a tender.

    One Tender → many TenderAnalysis (history: reruns create new rows,
    the most recent is the active one). Latest selected by ordered
    `analyzed_at DESC` LIMIT 1.

    Replaces the previous `Tender.ai_analysis TEXT` JSON blob.
    """

    __tablename__ = "tender_analysis"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Source: which pipeline produced this analysis
    source = Column(String(50), default="deepseek")  # 'deepseek' | 'manual' | 'rule'

    # Versioning
    version = Column(Integer, default=1)
    is_current = Column(Integer, default=1, index=True)  # 1 = latest active

    # ── Extracted fields ─────────────────────────────────────
    subject = Column(String(2000), nullable=True)  # brief subject line

    # OKPD2/OKVED2 codes extracted from docs
    okpd2_extracted = Column(ARRAY(String(20)), nullable=True)
    okved2_extracted = Column(ARRAY(String(20)), nullable=True)

    # Regions where work/supply is performed
    regions_extracted = Column(ARRAY(String(200)), nullable=True)

    # Requirements (licenses, certificates, experience)
    # Schema:
    #   {
    #     "licenses": [{"type": "ФСТЭК", "level": "TKE-3"}],
    #     "sro": true,
    #     "experience_years": 3,
    #     "iso_certifications": ["ISO 9001"],
    #     "other": [...]
    #   }
    requirements = Column(JSONB, nullable=True)

    # Financial block (STRICT MODE — see document_parser.py)
    # Schema:
    #   {
    #     "nmck_rub": 12450000.0,             // nullable if not found
    #     "nmck_source": "card|doc|unknown",  // provenance
    #     "application_guarantee_rub": 622500.0,
    #     "application_guarantee_pct": 5.0,
    #     "contract_guarantee_rub": 1245000.0,
    #     "contract_guarantee_pct": 10.0,
    #     "discount_pct": null,               // anti-outlier guard
    #     "outlier_warning": null             // "discount > 80% — verify"
    #   }
    financial = Column(JSONB, nullable=True)

    # Deadlines
    # Schema:
    #   {
    #     "submission": "2026-06-28T10:00:00",
    #     "execution_days": 90,
    #     "execution_until": "2026-12-15"
    #   }
    deadlines = Column(JSONB, nullable=True)

    # Evaluation criteria (for tenders/concurs)
    # Schema:
    #   [
    #     {"name": "Цена контракта", "weight_pct": 60},
    #     {"name": "Квалификация", "weight_pct": 30},
    #     {"name": "Срок", "weight_pct": 10}
    #   ]
    criteria = Column(JSONB, nullable=True)

    # Confidence (overall, 0..100)
    confidence_score = Column(Integer, nullable=True)

    # Citation pointers (pages of source documents, exact quotes)
    # Schema:
    #   {
    #     "source_documents": ["doc-uuid-1", "doc-uuid-2"],
    #     "source_pages": [3, 12, 25],
    #     "source_quotes": ["стр.3: «Начальная (максимальная) цена контракта: 12 450 000,00 ₽»"]
    #   }
    citations = Column(JSONB, nullable=True)

    # Raw AI response (for debugging / re-extraction)
    raw_ai_response = Column(JSONB, nullable=True)

    # Anti-outlier guard: if NMMCK looks anomalous (discount > 80%),
    # the matcher will downweight price scoring and the UI shows warning
    nmck_outlier_warning = Column(String(200), nullable=True)

    # Timing
    analyzed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processing_seconds = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Only one current analysis per tender
    __table_args__ = (
        UniqueConstraint("tender_id", "version", name="uq_tender_analysis_version"),
    )

    tender = relationship("Tender", backref="analyses", lazy="selectin")