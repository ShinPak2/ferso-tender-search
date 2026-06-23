"""Shared pytest fixtures for TenderSearch backend tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_in_memory_caches():
    """Reset module-level in-memory caches between tests.

    Several services keep an in-memory dict cache for graceful fallback
    when Redis is unavailable. Without this fixture, cache hits leak
    between tests (one test sees the response cached by a previous one).
    """
    from app.services import ai_extraction, damia_client, dedup

    # DaMIA in-memory cache
    damia_client._cache._mem.clear()

    # AI extraction in-memory cache
    ai_extraction._cache._mem.clear()

    # Dedup embedding cache
    if hasattr(dedup, "_cache") and hasattr(dedup._cache, "_mem"):
        dedup._cache._mem.clear()

    yield

    # No teardown needed
