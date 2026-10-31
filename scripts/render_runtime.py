import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def main():
    defaults = {
        "APP_ENV": "development", "APP_BIND_HOST": "127.0.0.1",
        "CACHE_TTL_SECONDS": "86400", "CACHE_MAX_ITEMS": "1000",
        "ENABLE_DEMO_RULES": "true", "SMTP_PORT": "587", "EMAIL_STARTTLS": "true",
    }
    names = list(defaults) + ["WEBHOOK_TOKEN", "DATABASE_URL", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    values = {name: os.getenv(name) or defaults.get(name, "") for name in names}
    for required in ("WEBHOOK_TOKEN", "DATABASE_URL"):
        if not values[required]:
            raise SystemExit(f"Missing Dev environment secret: {required}")
    if len(values["WEBHOOK_TOKEN"]) < 32 or values["WEBHOOK_TOKEN"] == "replace-with-a-long-random-secret":
        raise SystemExit("WEBHOOK_TOKEN must be a random secret of at least 32 characters")
    try:
        parsed = urlsplit(values["DATABASE_URL"])
        if parsed.hostname == "host.docker.internal":
            credentials = parsed.netloc.rsplit("@", 1)[0]
            parsed = parsed._replace(netloc=f"{credentials}@127.0.0.1:5432")
            values["DATABASE_URL"] = urlunsplit(parsed)
        checks = {
            "scheme (use postgresql://)": parsed.scheme in {"postgresql", "postgres"},
            "host (use 127.0.0.1)": parsed.hostname in {"127.0.0.1", "localhost"},
            "database (use rca_engine_db)": parsed.path == "/rca_engine_db",
            "port (use 5432)": parsed.port in {None, 5432},
            "username (use rca_engine_user)": parsed.username == "rca_engine_user",
            "password (provide a URL-encoded password)": bool(parsed.password),
        }
    except ValueError:
        raise SystemExit("DATABASE_URL cannot be parsed; URL-encode password special characters and use port 5432") from None
    invalid = [name for name, valid in checks.items() if not valid]
    if invalid:
        raise SystemExit("Invalid DATABASE_URL fields: " + "; ".join(invalid))
    for name in ("CACHE_TTL_SECONDS", "CACHE_MAX_ITEMS", "SMTP_PORT"):
        if not values[name].isdigit() or int(values[name]) < 1:
            raise SystemExit(f"Invalid positive integer: {name}")
    target = Path("runtime.json")
    target.write_text(json.dumps(values), encoding="utf-8")
    target.chmod(0o600)


if __name__ == "__main__":
    main()
