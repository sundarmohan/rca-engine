import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen


def main():
    release = Path(__file__).resolve().parent.parent
    revision = release.name
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SystemExit("Release directory must be named with a full Git commit SHA")
    app_directory = release.parent.parent
    runtime = release / "runtime.json"
    runtime.chmod(0o600)
    environment = os.environ.copy()
    environment.update(json.loads(runtime.read_text(encoding="utf-8")))
    image = f"rca-engine:{revision}"
    environment["RCA_IMAGE"] = image
    compose = ["docker", "compose", "--project-name", "rca-engine", "--env-file", "/dev/null", "-f", str(release / "compose.yaml")]

    def run(arguments, **kwargs):
        return subprocess.run(arguments, env=environment, cwd=release, check=True, **kwargs)

    run(["docker", "load", "--input", str(release / "image.tar.gz")])
    test_environment = environment.copy()
    test_environment["TEST_DATABASE_URL"] = environment["DATABASE_URL"]
    test_container = ["docker", "run", "--rm", "--network", "host", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "-e", "TEST_DATABASE_URL", image, "python", "-m"]
    database_check = subprocess.run(test_container + ["scripts.check_database"], env=test_environment, cwd=release)
    setup_script = (release / "scripts/configure_postgres.py").read_text(encoding="utf-8")
    if database_check.returncode == 42:
        subprocess.run(["sudo", "-n", "-u", "postgres", "python3", "-"], input=setup_script, text=True, cwd="/tmp", check=True)
        database_check = subprocess.run(test_container + ["scripts.check_database"], env=test_environment, cwd=release)
    if database_check.returncode == 43 and environment.get("APP_ENV") == "development":
        password = unquote(urlsplit(environment["DATABASE_URL"]).password)
        subprocess.run(["sudo", "-n", "-u", "postgres", "python3", "-c", setup_script, "--sync-password"], input=json.dumps({"password": password}), text=True, cwd="/tmp", check=True)
        database_check = subprocess.run(test_container + ["scripts.check_database"], env=test_environment, cwd=release)
    if database_check.returncode != 0:
        raise SystemExit("Database connection preflight failed; running application was not changed")
    subprocess.run(test_container + ["pytest", "-q", "-p", "no:cacheprovider"], env=test_environment, cwd=release, check=True)
    ingress = ["docker", "compose", "--project-name", "rca-ingress", "--env-file", "/dev/null", "-f", str(release / "compose.ingress.yaml")]
    validation_environment = environment.copy()
    validation_environment["RCA_PUBLIC_DOMAIN"] = environment.get("RCA_PUBLIC_DOMAIN") or "rca.example.invalid"
    validation_environment["RCA_TLS_EMAIL"] = environment.get("RCA_TLS_EMAIL") or "ops@example.invalid"
    subprocess.run(ingress + ["run", "--rm", "--no-deps", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"], env=validation_environment, cwd=release, check=True)
    result = subprocess.run(["docker", "inspect", "--format", "{{.Config.Image}}", "multicloud-rca-engine"], capture_output=True, text=True)
    previous_image = result.stdout.strip() if result.returncode == 0 else None
    failed_environment = environment.copy()
    ingress_started = False
    try:
        run(compose + ["up", "--detach", "--no-build", "--force-recreate"])
        for attempt in range(30):
            result = subprocess.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", "multicloud-rca-engine"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == "healthy":
                break
            time.sleep(3)
        else:
            raise RuntimeError("API/database readiness timed out")
        run(compose + ["exec", "-T", "rca-engine", "python", "-m", "scripts.smoke_test"])
        if environment.get("RCA_PUBLIC_DOMAIN"):
            ingress_started = True
            run(ingress + ["up", "--detach", "--force-recreate"])
            for attempt in range(20):
                try:
                    with urlopen("https://" + environment["RCA_PUBLIC_DOMAIN"] + "/ready", timeout=5) as response:
                        if json.loads(response.read(16384)).get("status") == "ready":
                            break
                except Exception:
                    pass
                time.sleep(3)
            else:
                raise RuntimeError("Verified public HTTPS readiness failed; check DNS and inbound TCP 80/443")
            print("Public HTTPS readiness verified")
    except Exception:
        if previous_image and (app_directory / "current").is_symlink():
            previous_release = (app_directory / "current").resolve()
            environment = os.environ.copy()
            environment.update(json.loads((previous_release / "runtime.json").read_text(encoding="utf-8")))
            environment["RCA_IMAGE"] = previous_image
            run(["docker", "compose", "--project-name", "rca-engine", "--env-file", "/dev/null", "-f", str(previous_release / "compose.yaml"), "up", "--detach", "--no-build", "--force-recreate"])
            if environment.get("RCA_PUBLIC_DOMAIN") and (previous_release / "compose.ingress.yaml").exists():
                run(["docker", "compose", "--project-name", "rca-ingress", "--env-file", "/dev/null", "-f", str(previous_release / "compose.ingress.yaml"), "up", "--detach", "--force-recreate"])
            elif failed_environment.get("RCA_PUBLIC_DOMAIN"):
                subprocess.run(["docker", "compose", "--project-name", "rca-ingress", "--env-file", "/dev/null", "-f", str(release / "compose.ingress.yaml"), "stop"], env=failed_environment, cwd=release, check=True)
        elif ingress_started:
            subprocess.run(["docker", "compose", "--project-name", "rca-ingress", "--env-file", "/dev/null", "-f", str(release / "compose.ingress.yaml"), "stop"], env=failed_environment, cwd=release, check=True)
        raise SystemExit("Deployment failed; previous managed release restored when available") from None
    temporary_link = app_directory / "current.next"
    temporary_link.unlink(missing_ok=True)
    temporary_link.symlink_to(release, target_is_directory=True)
    temporary_link.replace(app_directory / "current")
    (release / "image.tar.gz").unlink()
    print(f"Deployed {revision}: UI /, API /docs, PostgreSQL readiness verified")


if __name__ == "__main__":
    main()
