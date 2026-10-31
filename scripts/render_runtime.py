import json
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def normalize_database_url(value):
    value = value.strip()
    if value.startswith("DATABASE_URL="):
        value = value.removeprefix("DATABASE_URL=").strip()
    if len(value) >= 2 and value[0] in {"'", '"', "`"} and value[-1] == value[0]:
        value = value[1:-1]
    match = re.fullmatch(r"(postgres(?:ql)?://rca_engine_user:)(.*)@(127\.0\.0\.1|localhost|host\.docker\.internal)(?::5432)?(/rca_engine_db(?:\?[^\s#]*)?)", value, flags=re.DOTALL)
    if match:
        password = quote(match.group(2), safe="%")
        password = re.sub(r"%(?![0-9a-fA-F]{2})", "%25", password)
        value = f"{match.group(1)}{password}@127.0.0.1:5432{match.group(4)}"
    return value


def main():
    defaults = {
        "APP_ENV": "development", "APP_BIND_HOST": "127.0.0.1",
        "CACHE_TTL_SECONDS": "86400", "CACHE_MAX_ITEMS": "1000",
        "ENABLE_DEMO_RULES": "true", "SMTP_PORT": "587", "EMAIL_STARTTLS": "true",
        "AZURE_ENABLED": "false", "AWS_ENABLED": "false",
    }
    names = list(defaults) + ["WEBHOOK_TOKEN", "DATABASE_URL", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO", "OPERATOR_TOKEN", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_IDS", "AZURE_RESOURCE_GROUPS", "AWS_ACCOUNT_IDS", "AWS_REGIONS", "AWS_INSTANCE_IDS", "RCA_PUBLIC_DOMAIN", "RCA_TLS_EMAIL"]
    values = {name: os.getenv(name) or defaults.get(name, "") for name in names}
    domain = values["RCA_PUBLIC_DOMAIN"]
    if domain:
        hostname = r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
        if not re.fullmatch(hostname, domain):
            raise SystemExit("RCA_PUBLIC_DOMAIN must be a single DNS hostname without scheme, path or wildcard")
        email_parts = values["RCA_TLS_EMAIL"].split("@")
        if len(email_parts) != 2 or not re.fullmatch(r"[a-zA-Z0-9._+-]{1,64}", email_parts[0]) or not re.fullmatch(hostname, email_parts[1]):
            raise SystemExit("Public HTTPS requires a valid RCA_TLS_EMAIL contact")
        if len(values["OPERATOR_TOKEN"]) < 32 or values["OPERATOR_TOKEN"] == values["WEBHOOK_TOKEN"]:
            raise SystemExit("Public HTTPS requires a separate OPERATOR_TOKEN of at least 32 characters")
        values["APP_BIND_HOST"] = "127.0.0.1"
    if values["APP_ENV"] == "production" and not domain:
        raise SystemExit("Production rollout requires a configured HTTPS domain and operator identity")
    for required in ("WEBHOOK_TOKEN", "DATABASE_URL"):
        if not values[required]:
            raise SystemExit(f"Missing Dev environment secret: {required}")
    if len(values["WEBHOOK_TOKEN"]) < 32 or values["WEBHOOK_TOKEN"] == "replace-with-a-long-random-secret":
        raise SystemExit("WEBHOOK_TOKEN must be a random secret of at least 32 characters")
    try:
        values["DATABASE_URL"] = normalize_database_url(values["DATABASE_URL"])
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
    if os.getenv("GITHUB_ACTIONS") == "true":
        for sensitive in (values["DATABASE_URL"], parsed.password, unquote(parsed.password)):
            escaped = sensitive.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::add-mask::" + escaped)
    for name in ("CACHE_TTL_SECONDS", "CACHE_MAX_ITEMS", "SMTP_PORT"):
        if not values[name].isdigit() or int(values[name]) < 1:
            raise SystemExit(f"Invalid positive integer: {name}")
    target = Path("runtime.json")
    target.write_text(json.dumps(values), encoding="utf-8")
    target.chmod(0o600)


if __name__ == "__main__":
    main()
