"""TenderSearch — FastAPI application entry point."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import admin, auth, billing, profile, subscriptions, suggestions, tenders
from .services.scheduler import ensure_admin_user, start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown lifecycle."""
    # Startup: init DB, create admin, start parser scheduler
    await init_db()
    await ensure_admin_user()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(tenders.router, prefix="/api/tenders", tags=["Tenders"])
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["Subscriptions"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(billing.router, prefix="/api", tags=["Billing"])
app.include_router(profile.router, prefix="/api/profile", tags=["Profile"])
app.include_router(suggestions.router, tags=["Suggestions"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": settings.APP_VERSION}
