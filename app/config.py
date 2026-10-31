from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Multi-Cloud RCA Engine")
    environment: str = os.getenv("APP_ENV", "development")
    webhook_token: str = os.getenv("WEBHOOK_TOKEN", "")
    operator_token: str = os.getenv("OPERATOR_TOKEN", "")
    database_url: str = os.getenv("DATABASE_URL", "")
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
    cache_max_items: int = int(os.getenv("CACHE_MAX_ITEMS", "1000"))
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_to: str = os.getenv("EMAIL_TO", "")
    email_starttls: bool = _bool("EMAIL_STARTTLS", True)
    enable_demo_rules: bool = _bool("ENABLE_DEMO_RULES", True)

    def validate_runtime(self):
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        if not 1 <= self.cache_ttl_seconds <= 86400 or not 1 <= self.cache_max_items <= 10000:
            raise ValueError("Retention must be bounded to at most 24 hours and 10000 reports")
        if self.environment == "production":
            if len(self.webhook_token) < 32 or len(self.operator_token) < 32:
                raise ValueError("Production requires separate webhook and operator tokens of at least 32 characters")
            if self.webhook_token == self.operator_token:
                raise ValueError("Production webhook and operator tokens must differ")
            if not self.database_url:
                raise ValueError("Production deployment requires the explicitly approved PostgreSQL store")
            if self.smtp_username and not self.email_starttls:
                raise ValueError("SMTP credentials require TLS in production")


settings = Settings()
