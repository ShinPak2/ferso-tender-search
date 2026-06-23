"""Supplier profile model — h025ai-4.

Stores supplier data used for tender matching:
- INN, OGRN, KPP
- OKPD2/OKVED2 codes (arrays)
- Regions (array)
- Licenses (JSONB)
- Financial limits

Also includes bridge table `customer_id_aliases` for resolving 3 customer
identifiers used in EIS (zakupki.gov.ru) to a single canonical INN:
  - alias_type: 'inn' | 'organizationId' | 'organizationCode'
  - alias_value: identifier from EIS
  - canonical_inn: canonical INN (foreign-keyed to User.company if set)
  - ttl: 30 days from cache (resolved by DaMIA / manual)
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


class SupplierProfile(Base):
    """Supplier profile attached to a user (one-to-one).

    Fields chosen per SPEC.md §6 (Supplier Profile) and CJM.md §2 (Онбординг).
    """

    __tablename__ = "supplier_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Legal entity data
    inn = Column(String(12), nullable=True, index=True)
    ogrn = Column(String(15), nullable=True)
    kpp = Column(String(9), nullable=True)
    legal_name = Column(String(1000), nullable=True)
    legal_address = Column(String(1000), nullable=True)

    # OKPD2 (what supplier provides) + OKVED2 (what supplier does)
    okpd2_codes = Column(ARRAY(String(20)), nullable=True)
    okved2_codes = Column(ARRAY(String(20)), nullable=True)

    # Regions (names — free text or canonical codes like "77", "50")
    regions = Column(ARRAY(String(100)), nullable=True)

    # Licenses and certificates:
    #   [
    #     {"type": "ФСТЭК", "level": "TKE-3", "number": "...",
    #      "issued_at": "2023-01-15", "valid_until": "2027-01-15"},
    #     {"type": "СРО", "level": null, "number": "...",
    #      "issued_at": "2022-05-01", "valid_until": "2025-05-01"},
    #     ...
    #   ]
    licenses = Column(JSONB, nullable=True)

    # Financial limits (rubles)
    min_contract_sum = Column(Float, nullable=True)
    max_contract_sum = Column(Float, nullable=True)
    max_guarantee_sum = Column(Float, nullable=True)

    # Allowed procedure types:
    #   ["auction", "tender", "request_for_quotes", "single_supplier"]
    allowed_procedure_types = Column(ARRAY(String(50)), nullable=True)

    # Profile sources and validation
    egrul_data = Column(JSONB, nullable=True)  # full DaMIA response
    egrul_cached_at = Column(DateTime, nullable=True)
    manually_edited = Column(Integer, default=0)  # flag: 1 = user overrode EGRUL

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    user = relationship("User", backref="supplier_profile", lazy="selectin")


class CustomerIdAlias(Base):
    """Bridge table for resolving EIS customer identifiers to canonical INN.

    EIS uses 3 different identifiers for the same customer (research
    zakupki-html-recon.md §1):
      - inn (10 or 12 digits)
      - organizationId (5-8 digit, internal EIS ID)
      - organizationCode (11 digit, register code)

    This table allows the matcher to deduplicate the same legal entity
    referenced through different identifiers. Populated:
      - Automatically by DaMIA API-ФНС (canonical_inn resolution)
      - Manually by admin on customer request
    TTL: 30 days (re-resolve after expiry).
    """

    __tablename__ = "customer_id_aliases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    alias_type = Column(String(30), nullable=False)  # 'inn' | 'organizationId' | 'organizationCode'
    alias_value = Column(String(50), nullable=False, index=True)
    canonical_inn = Column(String(12), nullable=False, index=True)
    canonical_name = Column(String(1000), nullable=True)

    # Provenance
    source = Column(String(50), nullable=True)  # 'damia_fns', 'manual', 'auto_match'
    confidence = Column(Float, default=1.0)  # 0..1

    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("alias_type", "alias_value", name="uq_alias_type_value"),
    )