import os
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://zakupki:zakupki_secret@zakupki-postgres:5432/zakupki_cache",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    law_type: Mapped[str] = mapped_column(String(20), index=True)
    reg_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)

    source_url: Mapped[str] = mapped_column(Text)
    common_info_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    print_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    xml_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    purchase_object: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_inn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_kpp: Mapped[str | None] = mapped_column(String(32), nullable=True)

    max_price: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)

    placing_way: Mapped[str | None] = mapped_column(Text, nullable=True)
    purchase_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    publish_date_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_date_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submission_end_date_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contract_end_date_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    okpd2: Mapped[str | None] = mapped_column(Text, nullable=True)

    parse_status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    parse_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse_attempts: Mapped[int] = mapped_column(Integer, default=0)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    xml_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    xml_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents_count: Mapped[int] = mapped_column(Integer, default=0)
    files_count: Mapped[int] = mapped_column(Integer, default=0)

    raw_common_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_print_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_xml: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    documents: Mapped[list["PurchaseDocument"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list["ParseTask"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
    )


class PurchaseDocument(Base):
    __tablename__ = "purchase_documents"
    __table_args__ = (
        UniqueConstraint("reg_number", "uid", name="uq_purchase_documents_reg_uid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    purchase_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchases.id", ondelete="CASCADE"),
        index=True,
    )
    reg_number: Mapped[str] = mapped_column(String(40), index=True)

    uid: Mapped[str] = mapped_column(String(100), index=True)
    download_url: Mapped[str] = mapped_column(Text)

    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_name: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text)

    extension: Mapped[str] = mapped_column(String(30))
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    download_status: Mapped[str] = mapped_column(String(40), default="downloaded")
    download_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    purchase: Mapped[Purchase] = relationship(back_populates="documents")
    markdown: Mapped["DocumentMarkdown | None"] = relationship(
        "DocumentMarkdown",
        primaryjoin="PurchaseDocument.id == DocumentMarkdown.document_id",
        cascade="all, delete-orphan",
        uselist=False,
    )

    markdown_tasks: Mapped[list["MarkdownTask"]] = relationship(
        "MarkdownTask",
        primaryjoin="PurchaseDocument.id == MarkdownTask.document_id",
        cascade="all, delete-orphan",
    )


class ParseTask(Base):
    __tablename__ = "parse_tasks"
    __table_args__ = (
        UniqueConstraint("purchase_id", name="uq_parse_tasks_purchase_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    purchase_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchases.id", ondelete="CASCADE"),
        index=True,
    )

    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    purchase: Mapped[Purchase] = relationship(back_populates="tasks")



class DocumentMarkdown(Base):
    __tablename__ = "document_markdowns"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_document_markdowns_document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchase_documents.id", ondelete="CASCADE"),
        index=True,
    )

    purchase_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchases.id", ondelete="CASCADE"),
        index=True,
    )

    reg_number: Mapped[str] = mapped_column(String(40), index=True)
    uid: Mapped[str] = mapped_column(String(100), index=True)

    source_local_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)

    markdown_name: Mapped[str] = mapped_column(Text)
    markdown_path: Mapped[str] = mapped_column(Text)
    markdown_sha256: Mapped[str] = mapped_column(String(64), index=True)
    markdown_size_bytes: Mapped[int] = mapped_column(BigInteger)

    converter: Mapped[str] = mapped_column(String(80), default="markitdown")
    converter_version: Mapped[str | None] = mapped_column(String(80), nullable=True)

    status: Mapped[str] = mapped_column(String(40), default="converted", index=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MarkdownTask(Base):
    __tablename__ = "markdown_tasks"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_markdown_tasks_document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("purchase_documents.id", ondelete="CASCADE"),
        index=True,
    )

    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)



class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    kind: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)

    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_purchase_by_reg_number(db: Session, reg_number: str) -> Purchase | None:
    return db.scalar(select(Purchase).where(Purchase.reg_number == reg_number))
