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
        valid = parsed.scheme in {"postgresql", "postgres"} and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path == "/rca_engine_db" and parsed.port in {None, 5432} and parsed.username == "rca_engine_user" and bool(parsed.password)
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit("DATABASE_URL must target localhost:5432/rca_engine_db for this host-network deployment")
    for name in ("CACHE_TTL_SECONDS", "CACHE_MAX_ITEMS", "SMTP_PORT"):
        if not values[name].isdigit() or int(values[name]) < 1:
            raise SystemExit(f"Invalid positive integer: {name}")
    target = Path("runtime.json")
    target.write_text(json.dumps(values), encoding="utf-8")
    target.chmod(0o600)


if __name__ == "__main__":
    main()
