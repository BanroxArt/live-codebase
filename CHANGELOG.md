# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/) once it reaches 1.0.

## [Unreleased]

### Security

- The profile endpoint took `purpose` from the query string and the audited actor from an `X-Actor` header, so any caller could ask for `support` access under any name. Purpose, fields and actor now come from a verified bearer token (`auth.py`); requests that still send `purpose` or `X-Actor` get a 400, and `user` tokens can read only their own record.
- `/metrics`, `/version` and `/admin/*` moved to a separate admin listener (`ADMIN_HOST:ADMIN_PORT`, loopback by default). They are 404 on the public port.
- The container image no longer sets `APP_ENV=prod`. Prod now refuses to start without `AUTH_SIGNING_KEY`.
- An inbound `traceparent` no longer decides sampling unless `TRACE_TRUST_PARENT=true`.

### Fixed

- 405 responses were labelled with the raw request path, so `POST /api/users/<anything>/profile` created a new metrics series per id. They now use the route template; unknown methods are labelled `other`.
- Only `Store.read` held the database lock. Every statement on the shared SQLite connection now runs under one re-entrant lock.
- A readiness check that hung was resubmitted on every probe until the pool was full of stuck copies, after which `/readyz` failed for good. Each check now runs at most once at a time, concurrent probes share the run, and checks run in parallel.
- `Flags.killed()`, `snapshot()` and `stale()` read shared state without the lock.
- Log redaction matched key substrings, so `discard_count` and `bypass_cache` were redacted. It now matches whole words of the key (`card_number`, `apiKey` still redacted).
- Rejected reads (unknown purpose or fields) now leave an audit row.
- Under gunicorn, SIGTERM now drops readiness for `DRAIN_SECONDS` before the graceful stop, via `deploy/gunicorn.conf.py`.
- The canary comparison ignores admin-listener traffic.

### Added

- Visible author attribution, a `CITATION.cff` citation file, and `llms.txt`.

- Tamper-evident access log (`audit.py`). Rows are hash-chained (HMAC-SHA256 with `AUDIT_CHAIN_KEY`, plain SHA-256 without), `UPDATE` and `DELETE` are refused by triggers, `python -m livesvc.audit verify|head` checks the chain read-only, `/admin/audit/head` serves the head for outside checkpoints, and `Store.prune_audit` removes old rows behind a recorded anchor. Existing databases are migrated in place; their older rows are reported as unchained.
- `auth.py` with role policies, `python -m livesvc.auth SUBJECT ROLE` for dev tokens.
- `ADMIN_HOST`, `ADMIN_PORT`, `AUTH_SIGNING_KEY`, `TRACE_TRUST_PARENT` settings; `listener` label on request metrics.
- mypy (strict) in CI, a `py.typed` marker, and Hypothesis property tests for bucketing, `traceparent` parsing and redaction.
- SECURITY.md, CONTRIBUTING.md and this file.

### Changed

- Package description says "purpose-scoped data access with consent checks" instead of "consent-aware"; the data layer enforces a purpose it is given and cannot judge whether that purpose is honest.

## [0.1.0] - 2026-11-13

First public version: JSON logs with redaction, `traceparent` tracing, Prometheus-style metrics, liveness and readiness with drain, feature flags with kill switches, purpose-based data access with an audit log, canary comparison, Dockerfile and example deploy pipeline.
