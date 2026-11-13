# live-codebase

A small reference service, `livesvc`, that shows what "live" means for one service in concrete code, plus an agent skill (`SKILL.md`) that walks a team through getting their own service there in two weeks.

"Live" here means four loops work: you can **ship** a change on a known path and roll it back, **see** what production is doing, **steer** behaviour without a deploy, and use **real, permissioned data** without leaking it into places it should not be. Most teams have one or two of these. The service is kept small enough to read in an afternoon, so you can copy the parts you need.

Author: Art Akopian ([Banrox](https://banrox.com))

## What's in it

Standard library only; `opentelemetry-api` is optional.

| File | Loop | What it shows |
| --- | --- | --- |
| `logs.py` | see | JSON logs with `request_id` and `trace_id` on every line; key-based and pattern-based redaction (bearer tokens, emails, Luhn-valid card numbers) |
| `tracing.py` | see | W3C `traceparent` parse/emit, nested spans, sampling, optional OTel mirror |
| `metrics.py` | see | Counters and histograms in Prometheus text format; request rate, errors and latency per route template |
| `health.py` | ship | `/healthz` that touches nothing, `/readyz` with timed dependency checks that fails first when draining |
| `server.py` | ship | SIGTERM drain: readiness off, wait, then stop |
| `flags.py` | steer | Percentage rollouts with deterministic bucketing, allow/deny lists, three kinds of kill switch, hot reload, stale-flag report |
| `data.py` | data | Purpose-based field access, consent checks, masking, an audit row per read, and a database stamped with its environment |
| `canary.py` | ship | Canary vs baseline comparison on error rate and p95, returns promote, hold or rollback |
| `app.py` | all | The WSGI app wiring it together |

Also: `Dockerfile` (multi-stage, non-root, gunicorn), `deploy/flags.json`, and `deploy/github-actions-deploy.yml`, an example test-build-canary-promote pipeline.

## Run it

```sh
python -m pip install -e '.[dev]'
make check      # ruff, content lint, 71 tests
make run        # http://127.0.0.1:8080
```

```sh
curl -s localhost:8080/readyz
curl -s -H 'X-User-Id: u9' -i localhost:8080/api/recommendations   # see X-Flag-Reason
curl -s localhost:8080/metrics | grep http_requests_total
```

Configuration is environment only: `APP_ENV` (dev, test, staging, prod), `PORT`, `LOG_LEVEL`, `RELEASE` or `GIT_SHA`, `DATABASE_PATH`, `FLAGS_PATH`, `KILL_SWITCHES`, `ADMIN_TOKEN`, `DRAIN_SECONDS`, `TRACE_SAMPLE_RATE`. Admin endpoints are disabled unless `ADMIN_TOKEN` is set, and in prod it must be at least 32 characters.

## Design notes

**Deterministic bucketing.** `sha256(flag + ":" + user_id)` into 10,000 buckets; a user is in if `bucket < pct * 100`. Same user, same answer, on every process, with no shared state. Raising 5% to 20% only adds people. Hashing the flag name means each flag samples a different 5%, so one unlucky group of users does not get every experiment. Without a stable id the flag is on only at 100%, because a random answer per request is worse than either.

**Kill switches, three ways.** `"killed": true` in the flag file reaches every worker within `check_every_s` (5 s by default) because each evaluation does a cheap `stat` at most that often. `KILL_SWITCHES` works at boot, including `*` for everything. The admin endpoint is instant but only affects the process that served it, which is fine for one process and misleading for twelve.

**Liveness is not readiness.** If `/healthz` checked the database, a database blip would make the orchestrator restart every replica at the same time and turn a blip into an outage. Readiness checks dependencies with a timeout and reports only the exception class, since exception text tends to contain hostnames and connection strings.

**Logs carry ids, not data.** Redaction is a backstop. The real rule is that handlers log route templates, status and timing, and the data layer writes who read what to its own audit table instead of the log stream.

**Purpose-based access.** Code asks `store.read(user, purpose="analytics", fields=[...])`. The purpose decides which fields are reachable, consent is checked for purposes that need it, support sees masked emails, and every read (allowed, partial or denied) is audited with the request id. Field names are validated against a fixed set before they reach SQL.

**Environment stamp.** The first process to open a database writes its `APP_ENV` into a `meta` table. Anything else refuses to open it. A dev box with a prod connection string fails on boot instead of quietly working.

**Canary vs a fresh baseline.** Counters are cumulative, so the comparison is between a canary and a baseline started at the same time from the current release, scraped after the same soak. Too little traffic returns `hold`, never `promote`.

More in [docs/rollout.md](docs/rollout.md).

## Limitations

- `wsgiref` is for local runs and tests. The container runs gunicorn, which handles SIGTERM itself; the drain in `server.py` does not run there. On Kubernetes use a `preStop` sleep of a few seconds plus a `terminationGracePeriodSeconds` longer than gunicorn's graceful timeout.
- SQLite with one connection and a lock. The access pattern transfers to Postgres; the code does not.
- The OTel mirror creates spans under OTel's own context and does not inject the incoming `traceparent` into it. If you use OTel properly, use its propagator and drop the built-in tracer.
- Metrics are per process. Prometheus aggregates across replicas; the canary script expects one scrape per side.
- No auth for the user-facing endpoints. `X-User-Id` and `X-Actor` stand in for whatever your gateway sets after authentication. Never trust them from the open internet.
- Consent rules are an example of the mechanism. Which purposes need consent where you operate is a legal question.

## The agent skill

Copy `SKILL.md` to `.cursor/skills/live-codebase/SKILL.md` or your agent's equivalent. It reads the team's repo first, then plans the two weeks using this service as the reference for what "done" looks like. [examples/two-week-plan.md](examples/two-week-plan.md) shows the output for a made-up team.

## License

MIT. See [LICENSE](LICENSE). Copyright 2026 Banrox Inc.
