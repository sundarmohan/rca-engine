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


settings = Settings()
