from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


APP_RULE = "host rca_engine_db rca_engine_user 127.0.0.1/32 scram-sha-256"


def configured_hba(contents):
    lines = [line for line in contents.splitlines() if line.strip() != APP_RULE]
    return APP_RULE + "\n" + "\n".join(lines) + "\n"


def query(statement, database="postgres"):
    result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-tA", "-d", database], input=statement + ";\n", check=True, capture_output=True, text=True)
    return result.stdout.strip()


def password_verifier(password):
    if not password or not password.isascii() or "\x00" in password:
        raise SystemExit("Automatic development password alignment requires a nonempty ASCII password; configure other passwords directly in PostgreSQL")
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    client = hmac.digest(salted, b"Client Key", "sha256")
    stored = hashlib.sha256(client).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    encoded_salt = base64.b64encode(salt).decode("ascii")
    encoded_stored = base64.b64encode(stored).decode("ascii")
    encoded_server = base64.b64encode(server).decode("ascii")
    return f"SCRAM-SHA-256$4096:{encoded_salt}${encoded_stored}:{encoded_server}"


def main():
    if query("SELECT current_user") != "postgres":
        raise SystemExit("Local database setup must run as the PostgreSQL administrator")
    if "--sync-password" in sys.argv:
        password = json.load(sys.stdin)["password"]
        verifier = password_verifier(password)
        query("SET log_statement = 'none'; SET log_min_error_statement = 'panic'; SET log_min_duration_statement = -1; ALTER ROLE rca_engine_user PASSWORD '" + verifier + "'")
        print("Aligned only the dedicated development database user password using a SCRAM verifier; plaintext was not sent to SQL or logs")
        return
    hba = Path(query("SHOW hba_file")).resolve()
    contents = hba.read_text()
    updated = configured_hba(contents)
    if contents != updated:
        backup = hba.with_name(hba.name + ".rca-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
        shutil.copy2(hba, backup)
        descriptor, temporary = tempfile.mkstemp(prefix=".rca-hba-", dir=hba.parent)
        try:
            with os.fdopen(descriptor, "w") as target:
                os.fchmod(target.fileno(), stat.S_IMODE(hba.stat().st_mode))
                target.write(updated)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, hba)
            if query("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL") != "0":
                raise RuntimeError("PostgreSQL authentication configuration contains errors")
            if query("SELECT pg_reload_conf()") != "t":
                raise RuntimeError("PostgreSQL configuration reload failed")
        except Exception:
            shutil.copy2(backup, hba)
            query("SELECT pg_reload_conf()")
            raise SystemExit("Database authentication setup failed; original configuration restored") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
    query("GRANT CONNECT, CREATE ON DATABASE rca_engine_db TO rca_engine_user")
    query("GRANT USAGE, CREATE ON SCHEMA public TO rca_engine_user", database="rca_engine_db")
    print("Configured localhost-only SCRAM authentication and application schema privileges; existing databases and other authentication rules preserved")


if __name__ == "__main__":
    main()
