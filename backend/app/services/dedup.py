"""Smart deduplication service — h025ai-11.

Goal: TenderSearch must NEVER deliver the same tender twice.

Three layers:
  1. Primary key — `external_id` (= regNumber from EIS). Already handled by
     unique constraint in DB.
  2. Customer ID alias bridge — different EIS identifiers (ИНН, organizationId,
     organizationCode) can refer to the same legal entity. Resolved via
     `customer_id_aliases` table populated from DaMIA API-ФНС.
  3. Embedding-based similarity — for cases where the same tender is re-published
     under a slightly different regNumber (rare but happens after re-tendering).

Similarity algorithm:
  - Sentence embeddings via sentence-transformers (default model:
    paraphrase-multilingual-MiniLM-L12-v2 — Russian + English).
  - Fallback: OpenAI embeddings API (text-embedding-3-small) if local model
    unavailable.
  - Cosine similarity threshold: 0.85 (per SPEC.md task description).

Performance:
  - Embeddings cached in Redis by (regNumber, model_version).
  - Only NEW tenders need embedding computation; comparisons go against
    existing tenders' cached vectors.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import async_session
from ..models import Tender
from ..models.supplier_profile import CustomerIdAlias

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────

DEFAULT_SIMILARITY_THRESHOLD = 0.85  # cosine similarity

# Local model. ~470MB, Russian-aware. Good quality/speed tradeoff.
DEFAULT_LOCAL_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

EMBEDDING_CACHE_TTL_DAYS = 30


# ── Embedding backend abstraction ────────────────────────────────


class EmbeddingBackend:
    """Base class — subclasses implement `embed(texts) -> np.ndarray`."""

    model_name: str = "unknown"
    dim: int = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Pairwise cosine similarity (a: [n, d], b: [m, d]) → [n, m]."""
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
        b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
        return a_norm @ b_norm.T


class SentenceTransformerBackend(EmbeddingBackend):
    """Local sentence-transformers backend (preferred)."""

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


class OpenAIEmbeddingBackend(EmbeddingBackend):
    """OpenAI fallback (uses DEEPSEEK_BASE_URL or OPENAI_BASE_URL)."""

    def __init__(self):
        import httpx

        self.api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.openai.com/v1")
        )
        self.model_name = "text-embedding-3-small"
        self.dim = 1536
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30.0,
        )

    async def embed_async(self, texts: list[str]) -> np.ndarray:
        resp = await self._client.post(
            "/embeddings",
            json={"model": self.model_name, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        vectors = [item["embedding"] for item in data["data"]]
        return np.array(vectors, dtype=np.float32)

    def embed(self, texts: list[str]) -> np.ndarray:
        # Sync wrapper for legacy call sites — runs the async one.
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.embed_async(texts))


class SimpleHashBackend(EmbeddingBackend):
    """Last-resort fallback: deterministic char n-gram hash → dense vector.

    Quality is poor (no semantic understanding), but it gives the system
    *something* if sentence-transformers is not installed.
    """

    model_name = "char-ngram-hash"
    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            t = text.lower()
            for n in (3, 4):
                for j in range(len(t) - n + 1):
                    h = int(hashlib.md5(t[j : j + n].encode("utf-8")).hexdigest(), 16)
                    out[i, h % self.dim] += 1.0
        # Normalize rows
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


# ── Backend factory ──────────────────────────────────────────────


_backend: EmbeddingBackend | None = None


def get_backend() -> EmbeddingBackend:
    """Lazy-loaded embedding backend (prefers local, then OpenAI, then hash)."""
    global _backend
    if _backend is not None:
        return _backend

    if os.getenv("USE_LOCAL_EMBEDDINGS", "1") == "1":
        try:
            _backend = SentenceTransformerBackend()
            logger.info("Loaded local sentence-transformers backend")
            return _backend
        except Exception as e:
            logger.warning("Local sentence-transformers unavailable: %s", e)

    if os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"):
        try:
            _backend = OpenAIEmbeddingBackend()
            logger.info("Loaded OpenAI embedding backend")
            return _backend
        except Exception as e:
            logger.warning("OpenAI embedding backend init failed: %s", e)

    logger.warning("Falling back to simple char-ngram hash backend (poor quality)")
    _backend = SimpleHashBackend()
    return _backend


# ── Redis cache (optional) ────────────────────────────────────────


class _EmbeddingCache:
    """Tiny Redis-or-in-memory cache for embeddings."""

    def __init__(self):
        self._mem: dict[str, np.ndarray] = {}
        try:
            import redis.asyncio as redis_async

            url = os.getenv("REDIS_URL")
            if url:
                self._redis = redis_async.from_url(url, decode_responses=False)
                logger.info("Redis cache enabled")
            else:
                self._redis = None
        except Exception as e:
            logger.warning("Redis unavailable, using in-memory cache: %s", e)
            self._redis = None

    def _key(self, text: str, model: str) -> str:
        return f"emb:{model}:{hashlib.md5(text.encode('utf-8')).hexdigest()}"

    async def get(self, text: str, model: str) -> np.ndarray | None:
        key = self._key(text, model)
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return np.frombuffer(raw, dtype=np.float32)
            except Exception as e:
                logger.debug("Redis GET failed: %s", e)
        return self._mem.get(key)

    async def set(self, text: str, model: str, vec: np.ndarray, ttl: int = 86400 * 30):
        key = self._key(text, model)
        if self._redis:
            try:
                await self._redis.set(key, vec.tobytes(), ex=ttl)
                return
            except Exception as e:
                logger.debug("Redis SET failed: %s", e)
        self._mem[key] = vec


_cache = _EmbeddingCache()


# ── Public API ───────────────────────────────────────────────────


@dataclass
class DedupResult:
    """Result of dedup check for one tender."""

    is_duplicate: bool
    canonical_reg_number: str | None = None
    similar_reg_numbers: list[str] = field(default_factory=list)
    max_similarity: float = 0.0
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_duplicate": self.is_duplicate,
            "canonical_reg_number": self.canonical_reg_number,
            "similar_reg_numbers": self.similar_reg_numbers,
            "max_similarity": self.max_similarity,
            "threshold": self.threshold,
        }


async def _embed_with_cache(text: str) -> np.ndarray:
    backend = get_backend()
    cached = await _cache.get(text, backend.model_name)
    if cached is not None:
        return cached
    vec = backend.embed([text])[0]
    await _cache.set(text, backend.model_name, vec)
    return vec


def _tender_text(tender: Tender) -> str:
    """Build the text used for embedding comparison."""
    parts = [
        tender.title or "",
        tender.description or "",
        tender.customer or "",
        tender.region or "",
    ]
    return " | ".join(p.strip() for p in parts if p and p.strip())[:2000]


async def check_duplicate(
    tender: Tender,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    lookback_days: int = 90,
    db: AsyncSession | None = None,
) -> DedupResult:
    """Check if a tender is a duplicate of an existing one.

    Returns DedupResult with is_duplicate=True if a similar tender is found
    in the recent DB (lookback_days).
    """
    backend = get_backend()
    new_text = _tender_text(tender)
    if not new_text:
        return DedupResult(is_duplicate=False, threshold=threshold)

    # Get candidate tenders
    async def _run(session: AsyncSession) -> DedupResult:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        result = await session.execute(
            select(Tender.external_id, Tender.title, Tender.description, Tender.customer, Tender.region)
            .where(Tender.created_at >= cutoff)
            .where(Tender.id != tender.id)
        )
        rows = result.all()
        if not rows:
            return DedupResult(is_duplicate=False, threshold=threshold)

        # Build texts and embed
        existing_texts: list[str] = []
        reg_numbers: list[str] = []
        for ext_id, title, desc, cust, region in rows:
            if not ext_id:
                continue
            t = " | ".join(
                p.strip() for p in (title, desc, cust, region) if p and p.strip()
            )[:2000]
            if not t:
                continue
            existing_texts.append(t)
            reg_numbers.append(ext_id)

        if not existing_texts:
            return DedupResult(is_duplicate=False, threshold=threshold)

        new_vec = await _embed_with_cache(new_text)
        existing_vecs = await _embed_batch_with_cache(existing_texts)
        sims = backend.cosine_similarity(
            new_vec.reshape(1, -1), existing_vecs
        )[0]

        # Find max and candidates above threshold
        max_idx = int(np.argmax(sims))
        max_sim = float(sims[max_idx])
        similar = [
            reg_numbers[i] for i, s in enumerate(sims) if s >= threshold
        ]
        is_dup = max_sim >= threshold
        return DedupResult(
            is_duplicate=is_dup,
            canonical_reg_number=reg_numbers[max_idx] if is_dup else None,
            similar_reg_numbers=similar,
            max_similarity=max_sim,
            threshold=threshold,
        )

    if db is not None:
        return await _run(db)
    async with async_session() as session:
        return await _run(session)


async def _embed_batch_with_cache(texts: list[str]) -> np.ndarray:
    """Embed a batch with cache hits/misses."""
    backend = get_backend()
    out = np.zeros((len(texts), backend.dim), dtype=np.float32)
    miss_idx: list[int] = []
    miss_texts: list[str] = []
    for i, t in enumerate(texts):
        cached = await _cache.get(t, backend.model_name)
        if cached is not None:
            out[i] = cached
        else:
            miss_idx.append(i)
            miss_texts.append(t)
    if miss_texts:
        new_vecs = backend.embed(miss_texts)
        for j, idx in enumerate(miss_idx):
            out[idx] = new_vecs[j]
            await _cache.set(miss_texts[j], backend.model_name, new_vecs[j])
    return out


# ── Customer ID alias resolution ──────────────────────────────────


async def resolve_customer_inn(
    alias_type: str,
    alias_value: str,
    db: AsyncSession,
    *,
    ttl_days: int = 30,
) -> str | None:
    """Look up canonical INN for a customer identifier.

    Checks customer_id_aliases bridge table; returns None if not cached.
    Caller is expected to populate the table via DaMIA API-ФНС or manually.

    alias_type: 'inn' | 'organizationId' | 'organizationCode'
    """
    if alias_type == "inn" and alias_value:
        # INN is already canonical; store it if not present.
        existing = await db.execute(
            select(CustomerIdAlias).where(
                CustomerIdAlias.alias_type == "inn",
                CustomerIdAlias.alias_value == alias_value,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(
                CustomerIdAlias(
                    alias_type="inn",
                    alias_value=alias_value,
                    canonical_inn=alias_value,
                    source="passthrough",
                    expires_at=datetime.utcnow() + timedelta(days=ttl_days),
                )
            )
            await db.flush()
        return alias_value

    # Other types → look up bridge
    result = await db.execute(
        select(CustomerIdAlias.canonical_inn, CustomerIdAlias.expires_at).where(
            CustomerIdAlias.alias_type == alias_type,
            CustomerIdAlias.alias_value == alias_value,
        )
    )
    row = result.first()
    if not row:
        return None
    canonical_inn, expires_at = row
    if expires_at and expires_at < datetime.utcnow():
        return None  # cache expired
    return canonical_inn


async def cache_customer_alias(
    alias_type: str,
    alias_value: str,
    canonical_inn: str,
    canonical_name: str | None = None,
    *,
    source: str = "damia_fns",
    confidence: float = 1.0,
    ttl_days: int = 30,
    db: AsyncSession | None = None,
) -> CustomerIdAlias:
    """Insert or refresh a customer alias mapping."""
    async def _run(session: AsyncSession) -> CustomerIdAlias:
        result = await session.execute(
            select(CustomerIdAlias).where(
                CustomerIdAlias.alias_type == alias_type,
                CustomerIdAlias.alias_value == alias_value,
            )
        )
        alias = result.scalar_one_or_none()
        if alias is None:
            alias = CustomerIdAlias(
                alias_type=alias_type,
                alias_value=alias_value,
                canonical_inn=canonical_inn,
                canonical_name=canonical_name,
                source=source,
                confidence=confidence,
                expires_at=datetime.utcnow() + timedelta(days=ttl_days),
            )
            session.add(alias)
        else:
            alias.canonical_inn = canonical_inn
            alias.canonical_name = canonical_name or alias.canonical_name
            alias.source = source
            alias.confidence = confidence
            alias.expires_at = datetime.utcnow() + timedelta(days=ttl_days)
            session.add(alias)
        await session.flush()
        return alias

    if db is not None:
        return await _run(db)
    async with async_session() as session:
        result = await _run(session)
        await session.commit()
        return result


async def is_duplicate_customer(
    inn_a: str | None,
    inn_b: str | None,
    org_id_a: str | None,
    org_id_b: str | None,
    org_code_a: str | None,
    org_code_b: str | None,
) -> bool:
    """Decide if two customer records refer to the same legal entity.

    Strategy: any matching identifier wins.
    """
    if inn_a and inn_b and inn_a == inn_b:
        return True
    if org_id_a and org_id_b and org_id_a == org_id_b:
        return True
    if org_code_a and org_code_b and org_code_a == org_code_b:
        return True
    return False


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "DedupResult",
    "cache_customer_alias",
    "check_duplicate",
    "get_backend",
    "is_duplicate_customer",
    "resolve_customer_inn",
]