from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


APP_RULE = "host rca_engine_db rca_engine_user 127.0.0.1/32 scram-sha-256"


def configured_hba(contents):
    lines = [line for line in contents.splitlines() if line.strip() != APP_RULE]
    return APP_RULE + "\n" + "\n".join(lines) + "\n"


def query(statement, database="postgres"):
    result = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-tAc", statement, "-d", database], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def main():
    if query("SELECT current_user") != "postgres":
        raise SystemExit("Local database setup must run as the PostgreSQL administrator")
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
