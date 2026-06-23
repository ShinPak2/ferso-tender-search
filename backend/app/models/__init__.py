"""SQLAlchemy ORM models for TenderSearch.

This package consolidates all ORM models:
  - core models (User, Tender, Document, Subscription, Tariff, AdminSettings)
  - supplier_profile (h025ai-4) — SupplierProfile + CustomerIdAlias bridge
  - tender_documents (h025ai-5) — TenderDocument with AI extraction
  - tender_analysis (h025ai-6) — TenderAnalysis with structured data

All models inherit from `Base` (see app.database), and importing this package
is enough to register them with Base.metadata.

Backwards compatibility: existing `from app.models import Tender` style imports
keep working — every name is re-exported here.
"""
from .legacy import (  # noqa: F401  — re-export all original models
    AdminSettings,
    Document,
    Subscription,
    SubscriptionMatch,
    SubscriptionStatus,
    Tariff,
    TariffName,
    Tender,
    User,
    UserRole,
)
from .supplier_profile import CustomerIdAlias, SupplierProfile  # noqa: F401
from .tender_analysis import TenderAnalysis  # noqa: F401
from .tender_documents import TenderDocument  # noqa: F401
