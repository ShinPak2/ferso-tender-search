"""APScheduler service — h025ai-12.

Periodic jobs for TenderSearch:

  - HTML-sync (every 1-2 hours, default 90 min):
      Scrape zakupki.gov.ru search results, sync top N tenders, queue
      document download + AI analysis.

  - AI analysis pass (every 30 min):
      Find tenders without current analysis, run document_parser +
      ai_extraction.

  - Profile matcher (every 30 min):
      Recompute subscription matches using h025ai-10 matcher.

  - Smoke test (daily at 03:13):
      Re-parse 10 known golden-set tenders. If any fails, send Telegram
      alert (smoke test regression — indicates a layout change on
      zakupki.gov.ru).

REMOVED (h025ai-12 / research/zakupki-html-recon.md):
  - ~~FTP-sync раз в день~~ ❌
    FTP-сервер zakupki.gov.ru закрыт с 01.01.2025. Используем
    HTML-парсинг как primary.

Configuration (env vars):
  HTML_SYNC_INTERVAL_MINUTES (default 90)
  AI_ANALYSIS_INTERVAL_MINUTES (default 30)
  MATCHER_INTERVAL_MINUTES (default 30)
  SMOKE_TEST_HOUR (default 3)
  SMOKE_TEST_REG_NUMBERS (CSV; default empty → smoke test disabled)
  HTML_SYNC_QUERY (default ''  = all)
  HTML_SYNC_MAX_TENDERS (default 50)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (for smoke-test alerts)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# ── Telemetry (in-memory counters) ──────────────────────────────

_telemetry: dict[str, int] = {
    "tenders_parsed_today": 0,
    "documents_downloaded_today": 0,
    "parser_errors_today": 0,
    "varnish_empty_responses": 0,
    "rate_limit_429s": 0,
    "bridge_aliases_resolved": 0,
    "last_html_sync": 0,  # unix timestamp
    "smoke_test_passes": 0,
    "smoke_test_failures": 0,
    "smoke_test_last_run": 0,
}


def get_telemetry() -> dict[str, int]:
    return dict(_telemetry)


def reset_daily_counters() -> None:
    for k in (
        "tenders_parsed_today",
        "documents_downloaded_today",
        "parser_errors_today",
    ):
        _telemetry[k] = 0


# ── Helpers ──────────────────────────────────────────────────────


def _safe_str(value: Any) -> str | None:
    """Convert list to comma-separated string, or return as-is."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value) if value else None


async def _send_telegram_alert(text: str) -> None:
    """Send a Telegram message (best-effort, never raises)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.info("Telegram not configured, alert skipped: %s", text[:200])
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as e:
        logger.warning("Telegram alert failed: %s", e)


# ── HTML sync job (h025ai-8 + h025ai-12) ───────────────────────


async def html_sync_job() -> dict[str, int]:
    """One HTML-sync cycle: search → fetch cards → download docs → parse.

    Returns counters for telemetry.
    """
    from ..database import async_session
    from ..models import Tender
    from . import dedup, tender_parser
    from sqlalchemy import select

    query = os.getenv("HTML_SYNC_QUERY", "")
    max_tenders = int(os.getenv("HTML_SYNC_MAX_TENDERS", "50"))

    logger.info("HTML-sync: starting (query=%r, max=%d)", query, max_tenders)
    counters = {
        "searched": 0,
        "synced": 0,
        "downloaded": 0,
        "errors": 0,
        "duplicates": 0,
    }

    try:
        page = await tender_parser.search_tenders_44fz(
            query=query, page=1, records_per_page=max_tenders
        )
        counters["searched"] = len(page.tenders)
    except Exception as e:
        logger.exception("HTML-sync: search failed: %s", e)
        _telemetry["parser_errors_today"] += 1
        return counters

    for stub in page.tenders:
        reg = stub.get("reg_number")
        if not reg:
            continue
        try:
            # Dedup check before doing expensive work
            async with async_session() as db:
                existing = await db.execute(
                    select(Tender).where(Tender.external_id == reg)
                )
                existing_t = existing.scalar_one_or_none()
                if existing_t:
                    counters["duplicates"] += 1
                    continue

                # Sync the tender
                tender = await tender_parser.sync_tender(reg, db=db)
                if tender:
                    counters["synced"] += 1
                    docs = tender.tender_documents or []
                    counters["downloaded"] += len(docs)
        except Exception as e:
            logger.exception("HTML-sync: failed to sync %s: %s", reg, e)
            counters["errors"] += 1
            _telemetry["parser_errors_today"] += 1

    _telemetry["tenders_parsed_today"] += counters["synced"]
    _telemetry["documents_downloaded_today"] += counters["downloaded"]
    _telemetry["last_html_sync"] = int(datetime.utcnow().timestamp())
    logger.info("HTML-sync: done %s", counters)
    return counters


# ── AI analysis job (h025ai-9) ──────────────────────────────────


async def ai_analysis_job() -> int:
    """Pick tenders with parsed documents but no current analysis, run
    ai_extraction, write TenderAnalysis row.
    """
    from sqlalchemy import select

    from ..database import async_session
    from ..models import Tender
    from ..models.tender_analysis import TenderAnalysis
    from ..models.tender_documents import TenderDocument
    from . import ai_extraction

    analyzed = 0
    async with async_session() as db:
        # Tenders with at least one parsed document and no current analysis
        q = (
            select(Tender)
            .where(Tender.ai_analyzed_at.is_(None))
            .where(Tender.id.in_(
                select(TenderDocument.tender_id).where(
                    TenderDocument.parse_status == "parsed"
                )
            ))
            .order_by(Tender.published_at.desc())
            .limit(5)
        )
        result = await db.execute(q)
        tenders = result.scalars().all()

        for tender in tenders:
            # Concatenate all parsed document texts (truncated)
            docs_q = await db.execute(
                select(TenderDocument).where(
                    TenderDocument.tender_id == tender.id,
                    TenderDocument.parse_status == "parsed",
                )
            )
            docs = docs_q.scalars().all()
            full_text = "\n\n---\n\n".join(
                (d.extracted_text or "") for d in docs if d.extracted_text
            )[:50_000]

            if not full_text.strip():
                continue

            try:
                ex = await ai_extraction.extract_structured(full_text)
                if ex is None:
                    continue

                # Mark previous as not current
                prev_q = await db.execute(
                    select(TenderAnalysis).where(
                        TenderAnalysis.tender_id == tender.id,
                        TenderAnalysis.is_current == 1,
                    )
                )
                for prev in prev_q.scalars().all():
                    prev.is_current = 0
                    db.add(prev)

                # Get next version
                ver = (
                    await db.execute(
                        select(TenderAnalysis)
                        .where(TenderAnalysis.tender_id == tender.id)
                        .order_by(TenderAnalysis.version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                next_version = (ver.version + 1) if ver else 1

                analysis = TenderAnalysis(
                    tender_id=tender.id,
                    source="deepseek",
                    version=next_version,
                    is_current=1,
                    subject=ex.subject,
                    okpd2_extracted=ex.okpd2_codes,
                    okved2_extracted=ex.okved2_codes,
                    regions_extracted=(tender.region and [tender.region]) or [],
                    requirements=ex.requirements,
                    financial=ex.financial,
                    deadlines=ex.deadlines,
                    criteria=ex.evaluation_criteria,
                    confidence_score=int(ex.confidence * 100),
                    raw_ai_response=ex.raw_ai_response,
                    citations={
                        "source_documents": [str(d.id) for d in docs],
                        "source_pages": ex.source_pages,
                        "source_quotes": ex.source_quotes,
                    },
                    processing_seconds=0,
                )
                db.add(analysis)

                # Update tender.ai_analyzed_at
                from sqlalchemy import func as sa_func

                tender.ai_analyzed_at = sa_func.now()
                db.add(tender)

                analyzed += 1
            except Exception as e:
                logger.exception("AI analysis failed for %s: %s", tender.id, e)
                _telemetry["parser_errors_today"] += 1

        if analyzed:
            await db.commit()
    logger.info("AI analysis: %d tenders analyzed", analyzed)
    return analyzed


# ── Matcher job (h025ai-10) ─────────────────────────────────────


async def matcher_job() -> int:
    """Recompute subscription matches (legacy keyword-based) and
    profile matches (h025ai-10) on demand.
    """
    from .matcher import match_all_subscriptions

    return await match_all_subscriptions()


# ── Smoke test (golden-set) ────────────────────────────────────


def _get_golden_set() -> list[str]:
    """Return list of regNumbers for the smoke test."""
    raw = os.getenv("SMOKE_TEST_REG_NUMBERS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


async def smoke_test_job() -> dict[str, Any]:
    """Daily golden-set re-parse. Alert on Telegram if any fails.

    A failure here is the canary for layout changes on zakupki.gov.ru
    that would otherwise silently break our parser.
    """
    from .tender_parser import fetch_tender_card, fetch_tender_documents

    golden = _get_golden_set()
    if not golden:
        logger.debug("Smoke test: SMOKE_TEST_REG_NUMBERS not set, skipping")
        return {
            "enabled": False,
            "passes": 0,
            "failures": 0,
            "details": [],
        }

    passes: list[str] = []
    failures: list[tuple[str, str]] = []
    details: list[dict[str, Any]] = []

    for reg in golden[:10]:  # cap at 10 per spec
        try:
            card = await fetch_tender_card(reg)
            if card is None:
                failures.append((reg, "card is None"))
                details.append({"reg": reg, "ok": False, "error": "card is None"})
                continue
            docs = await fetch_tender_documents(reg)
            passes.append(reg)
            details.append(
                {
                    "reg": reg,
                    "ok": True,
                    "title": card.title[:80] if card.title else None,
                    "docs_count": len(docs),
                }
            )
        except Exception as e:
            failures.append((reg, str(e)[:200]))
            details.append({"reg": reg, "ok": False, "error": str(e)[:200]})

    _telemetry["smoke_test_passes"] += len(passes)
    _telemetry["smoke_test_failures"] += len(failures)
    _telemetry["smoke_test_last_run"] = int(datetime.utcnow().timestamp())

    if failures:
        msg = (
            f"🚨 <b>TenderSearch smoke test regression</b>\n\n"
            f"Passed: {len(passes)}/{len(golden)}\n"
            f"Failed ({len(failures)}):\n"
            + "\n".join(f"  • {r}: {e}" for r, e in failures[:5])
            + f"\n\nЭто означает изменение вёрстки zakupki.gov.ru. "
            f"Проверьте парсер."
        )
        await _send_telegram_alert(msg)
        logger.error("Smoke test FAILED: %d failures", len(failures))
    else:
        logger.info("Smoke test OK: %d passed", len(passes))

    return {
        "enabled": True,
        "passes": len(passes),
        "failures": len(failures),
        "details": details,
    }


# ── Admin user (legacy) ────────────────────────────────────────


async def ensure_admin_user() -> None:
    """Create admin user from environment variables if it doesn't exist."""
    import bcrypt

    from sqlalchemy import select

    from ..database import async_session
    from ..models import User, UserRole

    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.email == settings.ADMIN_EMAIL)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.role != UserRole.ADMIN:
                existing.role = UserRole.ADMIN
                db.add(existing)
                await db.commit()
                logger.info("Updated '%s' to admin role", settings.ADMIN_EMAIL)
            else:
                logger.info("Admin '%s' exists", settings.ADMIN_EMAIL)
            return

        hashed = bcrypt.hashpw(
            settings.ADMIN_PASSWORD.encode(), bcrypt.gensalt()
        ).decode()
        admin = User(
            email=settings.ADMIN_EMAIL,
            hashed_password=hashed,
            name="Admin",
            role=UserRole.ADMIN,
            tariff="business",
            monthly_limit=999999,
        )
        db.add(admin)
        await db.commit()
        logger.info("Created admin user: %s", settings.ADMIN_EMAIL)


# ── Scheduler lifecycle (h025ai-12) ────────────────────────────


def start_scheduler() -> None:
    """Start APScheduler with all jobs (h025ai-12)."""
    html_minutes = int(os.getenv("HTML_SYNC_INTERVAL_MINUTES", "90"))
    ai_minutes = int(os.getenv("AI_ANALYSIS_INTERVAL_MINUTES", "30"))
    matcher_minutes = int(os.getenv("MATCHER_INTERVAL_MINUTES", "30"))
    smoke_hour = int(os.getenv("SMOKE_TEST_HOUR", "3"))
    smoke_minute = int(os.getenv("SMOKE_TEST_MINUTE", "13"))

    # HTML sync — every 1-2h
    scheduler.add_job(
        html_sync_job,
        trigger=IntervalTrigger(minutes=html_minutes),
        id="html_sync",
        name="HTML-sync zakupki.gov.ru (every %d min)" % html_minutes,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # AI analysis — every 30 min
    scheduler.add_job(
        ai_analysis_job,
        trigger=IntervalTrigger(minutes=ai_minutes),
        id="ai_analysis",
        name="AI analysis pass (every %d min)" % ai_minutes,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Matcher — every 30 min
    scheduler.add_job(
        matcher_job,
        trigger=IntervalTrigger(minutes=matcher_minutes),
        id="matcher",
        name="Subscription matcher (every %d min)" % matcher_minutes,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Smoke test — daily at 03:13
    scheduler.add_job(
        smoke_test_job,
        trigger=CronTrigger(hour=smoke_hour, minute=smoke_minute),
        id="smoke_test",
        name="Daily smoke test on golden set",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Reset daily counters at midnight
    scheduler.add_job(
        reset_daily_counters,
        trigger=CronTrigger(hour=0, minute=0),
        id="reset_daily",
        name="Reset daily counters",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: html_sync=%dm ai=%dm matcher=%dm smoke=%02d:%02d",
        html_minutes,
        ai_minutes,
        matcher_minutes,
        smoke_hour,
        smoke_minute,
    )


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


# ── Module exports ───────────────────────────────────────────────

__all__ = [
    "ai_analysis_job",
    "ensure_admin_user",
    "get_telemetry",
    "html_sync_job",
    "matcher_job",
    "reset_daily_counters",
    "scheduler",
    "smoke_test_job",
    "start_scheduler",
    "stop_scheduler",
]
