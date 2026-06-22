"""Authentication router: register, login, me."""
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import User, UserRole

router = APIRouter()

security = HTTPBearer()

# ── Schemas ────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    company: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    company: str | None
    role: str
    tariff: str
    monthly_limit: int
    tenders_viewed_this_month: int
    created_at: str | None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ── Helpers ────────────────────────────────────────────────────────


def create_jwt(user_id: str) -> str:
    """Create a JWT token for user."""
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: extract and validate current user from JWT."""
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency: ensure current user is admin."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def serialize_user(user: User) -> dict[str, Any]:
    """Convert user model to dict without password."""
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "company": user.company,
        "role": user.role,
        "tariff": user.tariff,
        "monthly_limit": user.monthly_limit,
        "tenders_viewed_this_month": user.tenders_viewed_this_month,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ── Routes ─────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user."""
    # Check if email exists
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        hashed_password=bcrypt.hashpw(data.password.encode(), bcrypt.gensalt()).decode(),
        name=data.name,
        company=data.company,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = create_jwt(str(user.id))
    return TokenResponse(access_token=token, user=UserResponse(**serialize_user(user)))


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get JWT token."""
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not bcrypt.checkpw(data.password.encode(), user.hashed_password.encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_jwt(str(user.id))
    return TokenResponse(access_token=token, user=UserResponse(**serialize_user(user)))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserResponse(**serialize_user(current_user))
