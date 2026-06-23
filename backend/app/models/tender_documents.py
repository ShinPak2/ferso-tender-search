"""Tender documents model — h025ai-5.

Extends the existing Document table with:
  - content_hash (SHA256, for caching)
  - parsed_at (timestamp of last parser run)
  - ai_extraction (JSONB result of AI analysis)
  - download status (pending, downloaded, failed)

Backwards compatible: Document table was already created by existing models.py.
This adds columns via Alembic migration (no FK or table rename needed).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..database import Base


class TenderDocument(Base):
    """A document attached to a tender (PDF, DOCX, XLSX, ZIP, etc.).

    Tracks:
      - file metadata (filename, type, size)
      - download state (status, error)
      - text extraction (content_hash, parsed_at)
      - AI analysis result (ai_extraction JSONB with verdict, citations, etc.)

    Linked to Tender via FK; cascade delete.

    Note: there is already a Document model in models.py. We keep that one
    as a thin alias / compatibility layer and add this richer model
    alongside. The migrations script will create tender_documents table.
    """

    __tablename__ = "tender_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # EIS documentId from downloadDocument.html?id={documentId}
    eis_document_id = Column(String(50), nullable=True, index=True)

    filename = Column(String(500), nullable=False)
    file_type = Column(String(50), nullable=True)  # extension, lower-case
    file_size = Column(Integer, nullable=True)
    file_url = Column(String(1000), nullable=True)  # EIS download URL

    # SHA256 of file content (for caching and dedup)
    content_hash = Column(String(64), nullable=True, index=True)

    # Extracted plain text (truncated for storage; full text in cache/Redis)
    extracted_text = Column(Text, nullable=True)
    text_length = Column(Integer, nullable=True)

    # Parsing status
    download_status = Column(String(20), default="pending")  # pending | downloaded | failed
    parse_status = Column(String(20), default="pending")  # pending | parsed | failed
    parse_error = Column(String(1000), nullable=True)

    # Timestamps
    parsed_at = Column(DateTime, nullable=True)
    ai_extracted_at = Column(DateTime, nullable=True)

    # AI extraction result (h025ai-9)
    # Schema: see SPEC.md §8.2
    #   { "subject": "...", "okpd2_codes": [...], "requirements": {...},
    #     "financial": {...}, "deadlines": {...}, "evaluation_criteria": [...],
    #     "source_pages": [...], "source_quotes": [...], "confidence": 0.92 }
    ai_extraction = Column(JSONB, nullable=True)
    confidence_score = Column(Integer, nullable=True)  # 0..100

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tender_id", "eis_document_id", name="uq_tender_doc_eisid"),
    )

    tender = relationship("Tender", backref="tender_documents", lazy="selectin")