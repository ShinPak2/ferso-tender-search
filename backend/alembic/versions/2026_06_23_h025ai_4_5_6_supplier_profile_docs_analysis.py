"""h025ai-4/5/6: supplier_profiles, customer_id_aliases, tender_documents, tender_analysis.

Revision ID: h025ai_4_5_6_supplier_profile_docs_analysis
Revises:
Create Date: 2026-06-23 21:50:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "h025ai_4_5_6_supplier_profile_docs_analysis"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── h025ai-4: supplier_profiles ────────────────────────────
    op.create_table(
        "supplier_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("inn", sa.String(12), nullable=True, index=True),
        sa.Column("ogrn", sa.String(15), nullable=True),
        sa.Column("kpp", sa.String(9), nullable=True),
        sa.Column("legal_name", sa.String(1000), nullable=True),
        sa.Column("legal_address", sa.String(1000), nullable=True),
        sa.Column("okpd2_codes", postgresql.ARRAY(sa.String(20)), nullable=True),
        sa.Column("okved2_codes", postgresql.ARRAY(sa.String(20)), nullable=True),
        sa.Column("regions", postgresql.ARRAY(sa.String(100)), nullable=True),
        sa.Column("licenses", postgresql.JSONB, nullable=True),
        sa.Column("min_contract_sum", sa.Float, nullable=True),
        sa.Column("max_contract_sum", sa.Float, nullable=True),
        sa.Column("max_guarantee_sum", sa.Float, nullable=True),
        sa.Column(
            "allowed_procedure_types",
            postgresql.ARRAY(sa.String(50)),
            nullable=True,
        ),
        sa.Column("egrul_data", postgresql.JSONB, nullable=True),
        sa.Column("egrul_cached_at", sa.DateTime, nullable=True),
        sa.Column("manually_edited", sa.Integer, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # ── h025ai-4: customer_id_aliases (bridge table) ───────────
    op.create_table(
        "customer_id_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alias_type", sa.String(30), nullable=False),
        sa.Column("alias_value", sa.String(50), nullable=False, index=True),
        sa.Column("canonical_inn", sa.String(12), nullable=False, index=True),
        sa.Column("canonical_name", sa.String(1000), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("alias_type", "alias_value", name="uq_alias_type_value"),
    )

    # ── h025ai-5: tender_documents ──────────────────────────────
    op.create_table(
        "tender_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("eis_document_id", sa.String(50), nullable=True, index=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=True),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("file_url", sa.String(1000), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True, index=True),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("text_length", sa.Integer, nullable=True),
        sa.Column(
            "download_status", sa.String(20), server_default="pending"
        ),
        sa.Column("parse_status", sa.String(20), server_default="pending"),
        sa.Column("parse_error", sa.String(1000), nullable=True),
        sa.Column("parsed_at", sa.DateTime, nullable=True),
        sa.Column("ai_extracted_at", sa.DateTime, nullable=True),
        sa.Column("ai_extraction", postgresql.JSONB, nullable=True),
        sa.Column("confidence_score", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tender_id", "eis_document_id", name="uq_tender_doc_eisid"
        ),
    )

    # ── h025ai-6: tender_analysis ──────────────────────────────
    op.create_table(
        "tender_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source", sa.String(50), server_default="deepseek"),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("is_current", sa.Integer, server_default="1", index=True),
        sa.Column("subject", sa.String(2000), nullable=True),
        sa.Column(
            "okpd2_extracted", postgresql.ARRAY(sa.String(20)), nullable=True
        ),
        sa.Column(
            "okved2_extracted", postgresql.ARRAY(sa.String(20)), nullable=True
        ),
        sa.Column(
            "regions_extracted", postgresql.ARRAY(sa.String(200)), nullable=True
        ),
        sa.Column("requirements", postgresql.JSONB, nullable=True),
        sa.Column("financial", postgresql.JSONB, nullable=True),
        sa.Column("deadlines", postgresql.JSONB, nullable=True),
        sa.Column("criteria", postgresql.JSONB, nullable=True),
        sa.Column("confidence_score", sa.Integer, nullable=True),
        sa.Column("citations", postgresql.JSONB, nullable=True),
        sa.Column("raw_ai_response", postgresql.JSONB, nullable=True),
        sa.Column("nmck_outlier_warning", sa.String(200), nullable=True),
        sa.Column(
            "analyzed_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processing_seconds", sa.Float, nullable=True),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "tender_id", "version", name="uq_tender_analysis_version"
        ),
    )

    # Indexes for fast queries used by matcher and feed
    # NOTE: pg_trgm_ops index is optional; skip if extension not enabled.
    # The LIKE queries used by matcher don't require trigram indexes for MVP.
    op.create_index(
        "ix_tender_documents_content_hash",
        "tender_documents",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_tender_documents_content_hash", table_name="tender_documents")
    op.drop_table("tender_analysis")
    op.drop_table("tender_documents")
    op.drop_table("customer_id_aliases")
    op.drop_table("supplier_profiles")