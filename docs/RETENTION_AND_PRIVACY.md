# Retention and privacy

The original [objective](../PROJECT_OBJECTIVE.md) specified no permanent database. The subsequent user-approved PostgreSQL scope stores compact reports with expiry, not a telemetry warehouse. This exception does not authorize durable support/audit archives. See [store](../app/storage.py), [settings](../app/config.py) and [cleanup lifecycle](../app/main.py).

| Data | Retention and bounds |
| --- | --- |
| Compact reports/deduplication hashes | `CACHE_TTL_SECONDS` defaults to 86400 (24 hours), permitted range 1–86400. `CACHE_MAX_ITEMS` defaults to 1000, permitted range 1–10000. Approved PostgreSQL survives application restarts; development memory fallback does not. |
| New/updated report JSON | Serialized UTF-8 JSON capped at 64 KiB in both storage modes; oversized saves/updates fail. Existing stored rows are not retroactively resized. Revision compare-and-swap preserves original expiry and incident identity. |
| Discovered resource selections | Process memory, 600-second TTL, at most 100 entries; lost on restart. [Cloud service](../app/cloud.py). |
| Assistance/proposals | Process memory, TTL at most 600 seconds, at most 100 incidents and 100 retained proposals globally. Each proposal retains at most 32 history entries. [Support service](../app/support.py). |
| Definitions and approvals | Definitions valid at most 300 seconds and no later than incident expiry. Approvals valid at most 300 seconds, further limited by definition expiry; approval does not extend retention. |

Cleanup runs every 60 seconds; report reads/writes also purge expired rows, and transient services purge during access. Revision reads/updates exclude expired reports. Database unavailability can delay physical deletion; expired access checks and eventual deletion do not guarantee immediate erasure from disk. Capacity eviction may remove reports before their TTL. Deduplication uses the exact normalized event hash, not semantic incident/time-bucket matching.

Normalized [events](../app/models.py) reject extra fields and do not accept raw provider payload blobs. Health observations retain bounded state summaries and references. Reports still contain resource identifiers, event summaries, labels and operator-entered reasons; operators must minimize sensitive content. The request 64 KiB cap is separate from the new stored-report cap. Operator tokens are held in [browser memory](../app/static/app.js), not browser storage; API/static responses use `Cache-Control: no-store`.

## External retention

Application TTL deletion does not erase PostgreSQL WAL, backups, replicas, host/container journals, deployment artifacts, retained runtime secrets, cloud-native logs/audit stores, SMTP relay records or recipient mailboxes. Caddy certificate/configuration volumes also persist independently. Configure access, encryption, rotation, backup expiry and secure release cleanup under each system's policy. Container log rotation is a size bound, not a privacy guarantee; avoid recording tokens, connection strings, event bodies or sensitive identifiers.

Support history is ephemeral, uses a shared `operator` actor and provides no durable or independently attributed audit. Production support is blocked with 503 until independent identity/RBAC exists. An external audit sink would require a separate retention/access design; none is implemented. Reliable mail retries, OIDC/RBAC and HA remain unsupported.

Support release `19d7fc4` deployed in successful [EC2 run 41198858245](https://github.com/sundarmohan/rca-engine/actions/runs/41198858245): 344 passed, 2 warnings in 1.49 seconds (fake providers, PostgreSQL and approval/API tests), with assistance/no-live-call, CAS revision and public TLS smoke checks. Live Azure/AWS identities and secrets remain absent. Latest deployed follow-up `be067fb`, [run 41198923970](https://github.com/sundarmohan/rca-engine/actions/runs/41198923970), succeeded: 344 passed, 2 warnings in 1.39 seconds. Public authenticated capabilities reported `production_ready=false`; UI/HSTS with trusted TLS, public docs 404 and unauthenticated reports 401 probes all passed. See the updated [deployment status](./DEVELOPMENT_STATUS.md) for latest evidence. No production acceptance or phase completion follows from this policy.
