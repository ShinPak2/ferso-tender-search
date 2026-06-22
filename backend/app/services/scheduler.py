"""APScheduler service: periodic parsing and AI analysis."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _parse_and_analyze():
    """Job: parse tenders, insert into DB, run AI analysis."""
    from sqlalchemy import select

    from ..database import async_session
    from ..models import Tender
    from .ai import analyze_tender
    from .matcher import match_all_subscriptions
    from .parser import parse_zakupki

    logger.info("Scheduler: starting parse cycle")

    try:
        # 1. Parse tenders
        tenders_data = await parse_zakupki()

        async with async_session() as db:
            inserted = 0
            for tdata in tenders_data:
                # Check duplicates by title
                existing = await db.execute(
                    select(Tender).where(Tender.title == tdata["title"])
                )
                if existing.scalar_one_or_none():
                    continue

                tender = Tender(**tdata)
                db.add(tender)
                inserted += 1

            if inserted > 0:
                await db.commit()
                logger.info(f"Scheduler: inserted {inserted} new tenders")

        # 2. Run AI analysis on unanalyzed tenders
        async with async_session() as db:
            result = await db.execute(
                select(Tender).where(Tender.ai_analyzed_at.is_(None)).limit(10)
            )
            unanalyzed = result.scalars().all()

            for tender in unanalyzed:
                if tender.description:
                    try:
                        analysis = await analyze_tender(tender.title, tender.description)
                        if analysis:
                            tender.ai_analysis = analysis.get("analysis")
                            tender.ai_relevance = analysis.get("relevance")
                            tender.ai_risks = analysis.get("risks")
                            tender.ai_recommendation = analysis.get("recommendation")
                            tender.ai_analyzed_at = __import__("datetime").datetime.utcnow()
                            db.add(tender)
                    except Exception as e:
                        logger.error(f"AI analysis failed for tender {tender.id}: {e}")

            await db.commit()
            logger.info(f"Scheduler: analyzed {len(unanalyzed)} tenders")

        # 3. Run matcher
        await match_all_subscriptions()

    except Exception as e:
        logger.error(f"Scheduler job error: {e}", exc_info=True)


async def ensure_admin_user():
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
            # Ensure admin role
            if existing.role != UserRole.ADMIN:
                existing.role = UserRole.ADMIN
                db.add(existing)
                await db.commit()
                logger.info(f"Updated existing user '{settings.ADMIN_EMAIL}' to admin role")
            else:
                logger.info(f"Admin user '{settings.ADMIN_EMAIL}' already exists")
            return

        # Create admin user
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
        logger.info(f"Created admin user: {settings.ADMIN_EMAIL}")


def start_scheduler():
    """Start the APScheduler with periodic jobs."""
    scheduler.add_job(
        _parse_and_analyze,
        trigger=IntervalTrigger(minutes=settings.PARSER_INTERVAL_MINUTES),
        id="parse_tenders",
        name="Parse tenders and run AI analysis",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started: every {settings.PARSER_INTERVAL_MINUTES} minutes")


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
