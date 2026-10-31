# EC2 Dev deployment

## What is deployed

The image serves the dashboard at `/`, the FastAPI documentation at `/docs`, and authenticated incident APIs at `/api/v1`. PostgreSQL 17 remains installed on the host. Linux host networking makes `127.0.0.1:5432` reachable inside the application container; no PostgreSQL listener or security-group change is necessary.

Live Azure/AWS discovery, monitoring, diagnostic query adapters, mature RCA rules and human query-approval workflows remain unimplemented. Reports explicitly identify these evidence gaps. This deployment does not upgrade PostgreSQL, change cloud IAM, or perform remediation.

## Dev environment configuration

Create these under GitHub repository Settings → Environments → **Dev** → Secrets:

| Secret | Value/default |
| --- | --- |
| `EC2_HOST` | EC2 public IP or DNS; SSH port 22 |
| `EC2_USER` | Existing SSH Linux username |
| `EC2_SSH_PRIVATE_KEY` | Private key authorized for that user |
| `EC2_APP_DIR` | Dedicated application directory, e.g. `/opt/rca-engine` |
| `DATABASE_URL` | `postgresql://rca_engine_user:URL_ENCODED_PASSWORD@127.0.0.1:5432/rca_engine_db` |
| `WEBHOOK_TOKEN` | Random secret, at least 32 characters; required even in Dev deployments |
| `APP_ENV` | `development` |
| `CACHE_TTL_SECONDS` | `86400` if absent |
| `CACHE_MAX_ITEMS` | `1000` if absent |
| `ENABLE_DEMO_RULES` | `true` if absent; only starter event classification, not live diagnostics |
| `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` | Existing email configuration; optional |
| `SMTP_PORT`, `EMAIL_STARTTLS` | `587`, `true` if absent |
| `EC2_KNOWN_HOSTS` | Optional verified OpenSSH known-hosts entry to pin the server identity; otherwise first-use `ssh-keyscan` is used |

Keep passwords and private keys out of chat and version control. URL-encode special password characters. SSL is not required for this loopback connection. If your existing secret uses the previously suggested `host.docker.internal` alias, runtime rendering converts it to `127.0.0.1:5432` for host networking without changing the GitHub secret.

An optional **Dev environment variable** (not a secret), `APP_BIND_HOST`, controls web exposure. The default `127.0.0.1` supports an SSH tunnel or an existing HTTPS reverse proxy. For direct access in a restricted Dev security group, set `APP_BIND_HOST=0.0.0.0` and allow port 8000 only from your client IP. Use HTTPS before sending credentials over an external network.

## EC2 prerequisites

- Docker Engine and a Compose v2 plugin (`docker compose version`); the SSH user must have access to `docker info`.
- Python 3 on the host for release orchestration.
- SSH reachable from the Actions runner. Hosted runner IPs vary; restricted networks may require a self-hosted runner inside your network.
- A writable dedicated `EC2_APP_DIR`. If absent, the workflow uses noninteractive sudo once to create it, owned by the SSH user.
- PostgreSQL accepts a password-authenticated TCP connection for `rca_engine_user` to `rca_engine_db` on 127.0.0.1:5432.
- The application user can create its tables and indexes in the database's default schema, and create/drop isolated test schemas in `rca_engine_db`. The integration test suite never drops the database or existing application tables.

If permissions are missing, the database administrator can grant only the required privileges:

```sql
GRANT CONNECT, CREATE ON DATABASE rca_engine_db TO rca_engine_user;
```

Then, connected to `rca_engine_db`:

```sql
GRANT USAGE, CREATE ON SCHEMA public TO rca_engine_user;
```

This is unnecessary if the database and relevant schema are already owned by the application user. No superuser credential is needed by the application.

## Build, test, migrate and deploy

The [deployment workflow](./.github/workflows/deploy-ec2.yml) runs on pushes to `main` or via **Actions → Deploy (EC2) → Run workflow**:

1. Build an immutable image tagged with the triggering commit SHA; transfer that exact image, not the latest remote branch.
2. Validate configuration and write a permission-restricted runtime JSON file. Secret values are passed as environment data, never interpolated into shell code.
3. Transfer the image and release files into `EC2_APP_DIR/releases/<SHA>` over SSH. The server does not require GitHub checkout credentials.
4. Run API, authentication, UI asset and PostgreSQL integration tests in a temporary container on EC2. PostgreSQL tests use uniquely named schemas and remove them afterward. No local or GitHub-runner tests are run.
5. Recreate the service. Startup applies transactional, idempotent schema version 1, creating only `rca_schema_versions`, `rca_reports` and their indexes. No database reinstall or destructive migration is performed.
6. Wait for API/database readiness, then verify UI assets, API schema, authentication, event ingestion, deduplication and database persistence. Synthetic smoke reports are deleted afterward. SMTP messages are not sent by deployment tests.
7. Record the successful release with a `current` symlink. If rollout or smoke checks fail, restore the prior managed image and its runtime configuration when one exists. Existing unmanaged containers cannot be fully restored by this release mechanism. Additive database migrations are not rolled back.

The single-container rollout has brief downtime. Reports survive application restarts when PostgreSQL is configured. Secrets are retained with release files for rollback; protect the application directory and remove obsolete releases according to your secret/backup policy. Schema test failures stop the deployment before replacing the running application.

## Access and verification

For the default loopback binding, run locally (replace the placeholders):

```bash
ssh -i /path/to/private-key -L 8000:127.0.0.1:8000 EC2_USER@EC2_HOST
```

Then open:

- UI: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`
- Liveness: `http://127.0.0.1:8000/health`
- Readiness: `http://127.0.0.1:8000/ready` (must show `storage: postgresql`)

Enter the webhook token in the dashboard to access reports. It is held only in browser memory, not local storage. For Swagger, send `Authorization: Bearer <WEBHOOK_TOKEN>` on protected API calls; health, readiness and static UI assets are public.

On EC2, view container state without printing runtime configuration:

```bash
docker ps --filter name=multicloud-rca-engine
docker logs --tail 50 multicloud-rca-engine
curl --fail http://127.0.0.1:8000/ready
```

## Retention scope decision

The user explicitly requested PostgreSQL integration after the original no-database baseline in [PROJECT_OBJECTIVE.md](./PROJECT_OBJECTIVE.md). This phase persists compact report JSON only, bounds report count, and expires reports after the configured TTL (24 hours by default). A background cleanup runs every 60 seconds, and reads/writes also clean expired rows. Provider payloads/raw telemetry are not archived. PostgreSQL WAL, backups, replicas, SMTP systems and host/container logs have separate retention and are not erased by application TTL deletion.
