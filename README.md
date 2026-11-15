# live-codebase

A small reference service, `livesvc`, that shows what "live" means for one service in concrete code, plus an agent skill (`SKILL.md`) that walks a team through getting their own service there in two weeks.

"Live" here means four loops work: you can **ship** a change on a known path and roll it back, **see** what production is doing, **steer** behaviour without a deploy, and use **real, permissioned data** without leaking it into places it should not be. Most teams have one or two of these. The service is kept small enough to read in an afternoon, so you can copy the parts you need.

Built and maintained by Art Akopian, CEO of [Banrox](https://banrox.com). Banrox builds consumer credit and fraud-prevention software and publishes the general-purpose pieces.

## What's in it

Standard library only; `opentelemetry-api` is optional.

| File | Loop | What it shows |
| --- | --- | --- |
| `logs.py` | see | JSON logs with `request_id` and `trace_id` on every line; key-based and pattern-based redaction (bearer tokens, emails, Luhn-valid card numbers) |
| `tracing.py` | see | W3C `traceparent` parse/emit, nested spans, sampling, optional OTel mirror |
| `metrics.py` | see | Counters and histograms in Prometheus text format; request rate, errors and latency per route template |
| `health.py` | ship | `/healthz` that touches nothing, `/readyz` with parallel, time-boxed dependency checks that never pile up behind a hung one and fail first when draining |
| `server.py` | ship | Public and admin listeners, SIGTERM drain: readiness off, wait, then stop |
| `flags.py` | steer | Percentage rollouts with deterministic bucketing, allow/deny lists, three kinds of kill switch, hot reload, stale-flag report |
| `auth.py` | data | Bearer token stub; the caller's role, not the request, decides purpose and fields |
| `data.py` | data | Purpose-based field access, consent checks, masking, an audit row per read, and a database stamped with its environment |
| `audit.py` | data | Append-only, hash-chained access log with a verifier, outside checkpoints and prefix pruning for retention |
| `canary.py` | ship | Canary vs baseline comparison on error rate and p95, returns promote, hold or rollback |
| `app.py` | all | The WSGI app wiring it together |

Also: `Dockerfile` (multi-stage, non-root, gunicorn), `deploy/gunicorn.conf.py`, `deploy/flags.json`, and `deploy/github-actions-deploy.yml`, an example test-build-canary-promote pipeline.

## Run it

```sh
python -m pip install -e '.[dev]'
make check      # ruff, mypy, content lint, tests
make run        # public API on :8080, admin on 127.0.0.1:9090
```

```sh
curl -s localhost:8080/readyz
curl -s -H 'X-User-Id: u9' -i localhost:8080/api/recommendations   # see X-Flag-Reason
curl -s localhost:9090/metrics | grep http_requests_total          # admin listener only
```

Reading a profile needs a token. With `AUTH_SIGNING_KEY` set (32+ characters), mint a short-lived dev token and use it:

```sh
export AUTH_SIGNING_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
TOKEN=$(python -m livesvc.auth u1 user)
curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/users/u1/profile   # 404 on an empty database, 401 without the token
```

Configuration is environment only: `APP_ENV` (dev, test, staging, prod), `HOST`, `PORT`, `ADMIN_HOST`, `ADMIN_PORT`, `LOG_LEVEL`, `RELEASE` or `GIT_SHA`, `DATABASE_PATH`, `FLAGS_PATH`, `KILL_SWITCHES`, `ADMIN_TOKEN`, `AUTH_SIGNING_KEY`, `AUDIT_CHAIN_KEY`, `DRAIN_SECONDS`, `TRACE_SAMPLE_RATE`, `TRACE_TRUST_PARENT`.

- Without `AUTH_SIGNING_KEY` the profile endpoint answers 401 to everyone. Prod refuses to start without it.
- `AUDIT_CHAIN_KEY` (32+ characters) keys the access log hash chain. Without it the chain is plain SHA-256, which catches edits but can be recomputed by anyone with write access; prod logs a warning at start. The mode and a key id are stamped into the database, so changing or dropping the key later is refused rather than reported as tampering.
- Admin endpoints are disabled unless `ADMIN_TOKEN` is set, and in prod it must be at least 32 characters.
- The container image does not set `APP_ENV`, so it starts as dev. Set `APP_ENV=prod` in the deployment together with the two keys above.

## Two listeners

| Listener | Default bind | Routes |
| --- | --- | --- |
| public | `HOST:PORT` (`127.0.0.1:8080`, `0.0.0.0:8080` in the image) | `/healthz`, `/readyz`, `/api/*` |
| admin | `ADMIN_HOST:ADMIN_PORT` (`127.0.0.1:9090`) | `/healthz`, `/readyz`, `/metrics`, `/version`, `/admin/*` (flags, `/admin/audit/head`) |

Metrics, the build version and the kill switches are not routed on the public listener at all, so a proxy rule that forwards everything still cannot reach them. In a cluster, set `ADMIN_HOST=0.0.0.0` and expose the admin port to Prometheus and operators through a cluster-internal service, never through the ingress. The admin listener runs inside the gunicorn worker, which is why the image runs one worker with threads; scale with replicas.

## Design notes

**Deterministic bucketing.** `sha256(flag + ":" + user_id)` into 10,000 buckets; a user is in if `bucket < pct * 100`. Same user, same answer, on every process, with no shared state. Raising 5% to 20% only adds people. Hashing the flag name means each flag samples a different 5%, so one unlucky group of users does not get every experiment. Without a stable id the flag is on only at 100%, because a random answer per request is worse than either.

**Kill switches, three ways.** `"killed": true` in the flag file reaches every worker within `check_every_s` (5 s by default) because each evaluation does a cheap `stat` at most that often. `KILL_SWITCHES` works at boot, including `*` for everything. The admin endpoint is instant but only affects the process that served it, which is fine for one process and misleading for twelve.

**Liveness is not readiness.** If `/healthz` checked the database, a database blip would make the orchestrator restart every replica at the same time and turn a blip into an outage. Readiness checks dependencies with a timeout and reports only the exception class, since exception text tends to contain hostnames and connection strings.

**Logs carry ids, not data.** Redaction is a backstop. The real rule is that handlers log route templates, status and timing, and the data layer writes who read what to its own audit table instead of the log stream.

**Purpose-based access.** Code asks `store.read(user, purpose="analytics", fields=[...])`. The purpose decides which fields are reachable, consent is checked for purposes that need it, support sees masked emails, and every read (allowed, partial, denied or rejected) is audited with the request id. Field names are validated against a fixed set before they reach SQL.

**The server picks the purpose.** A purpose the client can choose is not a control: anyone would ask for `support`. The HTTP layer takes the caller's role from a verified token and maps it to a purpose and field list (`auth.ROLES`): a `user` reads only their own record for `service`, `support` gets masked email, `analytics` needs consent and never sees email. `?fields=` can narrow the list, never widen it. A request that still sends `purpose` or `X-Actor` gets a 400, so an old client fails loudly instead of silently getting a different answer. The audit row records `role:subject` from the token.

**Bounded metric labels.** Every request is labelled with a route template or `unmatched`, never the raw path, including 405s, and unknown HTTP methods collapse to `other`. Without that, one scanner can create a time series per URL it tries.

**Readiness that cannot wedge.** A thread running a hung check cannot be cancelled. Each check therefore runs at most once at a time: a probe that finds a run in flight waits for it, up to that run's deadline, rather than starting another copy. Checks run in parallel, so a probe takes about one timeout no matter how many checks there are.

**Sampling is ours.** The trace id from an inbound `traceparent` is always continued, so logs line up with the caller. The sampled bit is honoured only with `TRACE_TRUST_PARENT=true`; otherwise any client could force every request to be recorded.

**An audit log that shows when it was edited.** An audit table anyone with database access can quietly rewrite is evidence of nothing. Each `access_log` row carries `seq`, the previous row's hash and its own HMAC-SHA256 over a canonical JSON encoding of the row, so changing, inserting or removing a row breaks the chain from that row on, and `python -m livesvc.audit verify DATABASE_PATH` names the first bad `seq` (exit 1). SQLite triggers refuse `UPDATE` and `DELETE` on the table; that stops bugs and casual edits, and the chain catches anyone who drops the triggers first. Appends run under `BEGIN IMMEDIATE` and `prev_hash` is unique, so two workers on one file cannot fork the chain.

Two things a chain alone cannot do. It cannot detect rows cut off the end, because a shorter chain is still valid: record `GET /admin/audit/head` (or `python -m livesvc.audit head`) on a schedule somewhere the database owner cannot write, and pass it back with `verify --expect SEQ:HASH`. And it cannot stop someone holding the key; keep `AUDIT_CHAIN_KEY` in the secret store, not next to the database. Retention uses `Store.prune_audit(before_ts)`, which deletes the oldest prefix and records the last deleted row as the new anchor, so the rest still verifies. Rows written before the chain existed are kept and counted as unchained.

**Environment stamp.** The first process to open a database writes its `APP_ENV` into a `meta` table. Anything else refuses to open it. A dev box with a prod connection string fails on boot instead of quietly working.

**Canary vs a fresh baseline.** Counters are cumulative, so the comparison is between a canary and a baseline started at the same time from the current release, scraped after the same soak. Too little traffic returns `hold`, never `promote`.

More in [docs/rollout.md](docs/rollout.md).

## Limitations

- `wsgiref` is for local runs and tests. Under gunicorn, `deploy/gunicorn.conf.py` wraps the worker's SIGTERM handler so readiness drops `DRAIN_SECONDS` before gunicorn starts its graceful stop. Keep `terminationGracePeriodSeconds` longer than drain plus gunicorn's graceful timeout.
- SQLite with one connection and one lock around every statement. Throughput is one query at a time per process, which is fine for a reference. The access pattern transfers to Postgres with a pool; the code does not.
- The OTel mirror creates spans under OTel's own context and does not inject the incoming `traceparent` into it. If you use OTel properly, use its propagator and drop the built-in tracer.
- Metrics are per process. Prometheus aggregates across replicas; the canary script expects one scrape per side.
- `auth.py` is a stub with the right shape, not an identity provider: HMAC-signed tokens with a role and an expiry, no revocation, no key rotation. Replace `authenticate()` with your OIDC or gateway verification and keep the role-to-purpose mapping. `X-User-Id` on `/api/recommendations` is only a flag bucketing id for anonymous callers and grants nothing.
- The audit chain is per database. With Postgres the same design works with a sequence, a unique `prev_hash` and `SERIALIZABLE` or an advisory lock around the append; the triggers become `REVOKE UPDATE, DELETE` for the application role. Verification is a full scan, fine up to millions of rows; past that, verify from the last checkpoint.
- Consent rules are an example of the mechanism. Which purposes need consent where you operate is a legal question.

## The agent skill

Copy `SKILL.md` to `.cursor/skills/live-codebase/SKILL.md` or your agent's equivalent. It reads the team's repo first, then plans the two weeks using this service as the reference for what "done" looks like. [examples/two-week-plan.md](examples/two-week-plan.md) shows the output for a made-up team.

## Benchmarks

Per-operation timings and how to reproduce them: [docs/benchmarks.md](docs/benchmarks.md).

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Banrox Inc.
