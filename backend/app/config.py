"""Application configuration from environment variables."""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    """Application settings loaded from environment."""

    # Database
    DB_USER: str = field(default_factory=lambda: os.getenv("DB_USER", "tender"))
    DB_PASS: str = field(default_factory=lambda: os.getenv("DB_PASS", "tender_secret"))
    DB_HOST: str = field(default_factory=lambda: os.getenv("DB_HOST", "postgres"))
    DB_PORT: str = field(default_factory=lambda: os.getenv("DB_PORT", "5432"))
    DB_NAME: str = field(default_factory=lambda: os.getenv("DB_NAME", "tendersearch"))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # JWT
    JWT_SECRET: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET", "tendersearch-jwt-secret-change-in-prod")
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # DeepSeek AI
    DEEPSEEK_API_KEY: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    DEEPSEEK_BASE_URL: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    )
    DEEPSEEK_MODEL: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    )

    # Parser
    PARSER_INTERVAL_MINUTES: int = 60
    ZAKUPKI_BASE_URL: str = "https://zakupki.gov.ru"

    # App
    APP_TITLE: str = "TenderSearch API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")

    # Admin
    ADMIN_EMAIL: str = field(
        default_factory=lambda: os.getenv("ADMIN_EMAIL", "admin@tendersearch.ru")
    )
    ADMIN_PASSWORD: str = field(
        default_factory=lambda: os.getenv("ADMIN_PASSWORD", "admin123")
    )

    # CORS
    CORS_ORIGINS: list = field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "*").split(",")
    )


settings = Settings()
