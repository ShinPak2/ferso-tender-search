"""SQLAlchemy ORM models for TenderSearch."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base

# ── Enums ──────────────────────────────────────────────────────────


class UserRole:
    USER = "user"
    ADMIN = "admin"


class TariffName:
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"


class LawType:
    FZ_44 = "44-ФЗ"
    FZ_223 = "223-ФЗ"


class SubscriptionStatus:
    ACTIVE = "active"
    PAUSED = "paused"


# ── User ───────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    role = Column(String(50), default=UserRole.USER)
    tariff = Column(String(50), default=TariffName.FREE)
    monthly_limit = Column(Integer, default=10)  # tenders per month
    tenders_viewed_this_month = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Relations
    subscriptions = relationship("Subscription", back_populates="user", lazy="selectin")


# ── Tender ─────────────────────────────────────────────────────────


class Tender(Base):
    __tablename__ = "tenders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id = Column(String(100), unique=True, nullable=True, index=True)
    title = Column(String(1000), nullable=False)
    description = Column(Text, nullable=True)
    customer = Column(String(500), nullable=True)
    price = Column(Float, nullable=True)
    currency = Column(String(10), default="RUB")
    deadline = Column(DateTime, nullable=True)
    law_type = Column(String(50), nullable=True)  # 44-ФЗ / 223-ФЗ
    region = Column(String(255), nullable=True)
    status = Column(String(50), default="active")
    source_url = Column(String(1000), nullable=True)
    published_at = Column(DateTime, nullable=True)
    parsed_at = Column(DateTime, default=datetime.utcnow)
    ai_analysis = Column(Text, nullable=True)  # JSON string
    ai_relevance = Column(Integer, nullable=True)  # 1-10
    ai_risks = Column(Text, nullable=True)
    ai_recommendation = Column(Text, nullable=True)
    ai_analyzed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    documents = relationship("Document", back_populates="tender", lazy="selectin")
    matches = relationship("SubscriptionMatch", back_populates="tender", lazy="selectin")


# ── Document ───────────────────────────────────────────────────────


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tender_id = Column(UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(500), nullable=False)
    file_url = Column(String(1000), nullable=True)
    file_type = Column(String(50), nullable=True)
    file_size = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    tender = relationship("Tender", back_populates="documents")


# ── Subscription ───────────────────────────────────────────────────


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    keywords = Column(Text, nullable=True)  # JSON list of keywords
    law_type = Column(String(50), nullable=True)  # 44-ФЗ / 223-ФЗ / all
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    region = Column(String(255), nullable=True)
    status = Column(String(50), default=SubscriptionStatus.ACTIVE)
    notify_email = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    user = relationship("User", back_populates="subscriptions")
    matches = relationship("SubscriptionMatch", back_populates="subscription", lazy="selectin")


# ── SubscriptionMatch ──────────────────────────────────────────────


class SubscriptionMatch(Base):
    __tablename__ = "subscription_matches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    tender_id = Column(UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False)
    relevance_score = Column(Float, nullable=True)  # 0-100
    is_viewed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    subscription = relationship("Subscription", back_populates="matches")
    tender = relationship("Tender", back_populates="matches")

    __table_args__ = (
        UniqueConstraint("subscription_id", "tender_id", name="uq_subscription_tender"),
    )


# ── Tariff ─────────────────────────────────────────────────────────


class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), unique=True, nullable=False)  # free / pro / business
    display_name = Column(String(255), nullable=False)
    price_monthly = Column(Integer, nullable=False)  # in rubles
    tender_limit = Column(Integer, nullable=False)
    subscription_limit = Column(Integer, nullable=False)
    ai_analysis_type = Column(String(50), nullable=False)  # basic / full / priority
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── AdminSettings ───────────────────────────────────────────────


class AdminSettings(Base):
    __tablename__ = "admin_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
