---
name: live-codebase
description: Help a team make one software service "live" rather than static, meaning it can be shipped and rolled back on a known path, observed in production, steered with feature flags and kill switches, and connected to real permissioned data safely. Use when the user says the codebase is a snapshot, releases are rare or manual, they cannot tell what production is doing, they want flags, gradual rollouts or canaries, or they want to move off fixtures onto real data. Uses the livesvc reference service in this repo as the definition of done. Do not use this to copy production data off-platform or to skip consent and privacy rules.
---

# Make one service live

A static codebase builds, maybe demos, then goes quiet. A live one can be changed safely while real users are on it. The test is four loops:

1. Ship: a merged change reaches production on a known path, and can be rolled back in minutes.
2. See: logs, metrics and traces tell you what it is doing and whether it is broken, and a named person is paged when it is.
3. Steer: behaviour can be changed without a deploy, and a bad feature can be turned off in seconds.
4. Real data: production uses current, permissioned data; other environments do not hold copies of it.

This repo's `livesvc` package implements each loop in a few hundred lines. Use it as the reference for what good looks like, not as something the team must adopt.

## 1. Read the repo before suggesting anything

Boot it from its own README. Note what fails. Then record, from the tree and from asking:

- Language, framework, how it is hosted, how deploys happen today, who can deploy
- CI: what runs, on which branches, how long it takes
- Logs: format, where they go, whether a request can be followed end to end
- Health endpoints, uptime checks, who gets paged
- Flags or config switches, if any
- Where production data lives, and whether any non-prod environment has a copy
- Third-party integrations and which credentials point at live accounts

Do not invent architecture that is not in the tree. If something cannot be determined, list it as unknown.

## 2. Pick the thinnest path through all four loops

Prefer tools they already pay for. For each loop, name the smallest change and the file it lands in. Typical first moves, with the livesvc file that shows the pattern:

| Gap | First move | Reference |
| --- | --- | --- |
| No request correlation | Request id middleware, JSON logs with request and trace ids | `app.py`, `logs.py` |
| Cannot see errors or latency | Per-route request counter and latency histogram on route templates | `metrics.py` |
| Deploys drop requests | Separate liveness and readiness; drain on SIGTERM | `health.py`, `server.py` |
| No way to turn things off | Server-side flags with deterministic bucketing and a file-based kill switch | `flags.py` |
| Big-bang releases | Canary next to a fresh baseline, compare, then step up | `canary.py`, `docs/rollout.md` |
| Prod data in staging | Delete it; synthetic or masked data; environment stamp on databases | `data.py` |
| Code reads any field for any reason | Purpose-based access with consent checks and an audit row per read | `data.py` |
| Client chooses its own purpose or identity | Role from a verified token decides purpose and fields; reject client-supplied ones | `auth.py`, `app.py` |
| Metrics and admin reachable from the internet | Separate admin listener on an internal address | `server.py`, `app.py` |

## 3. Rules

- Never recommend copying production data to laptops, shared dev databases or prompts. If realistic data is needed off-prod, use synthetic or masked data under a written rule.
- Live credentials for bureaus, processors, email or SMS do not belong in dev. Each integration needs a sandbox mode and an obvious live mode.
- Every flag has an owner and a removal date.
- The server decides who is asking and for what. A purpose, role or actor taken from the request is a suggestion, not a control.
- Database changes are backward compatible with the previous release, so an image rollback is a real rollback.
- Consent requirements are a legal question. Build the mechanism; let counsel set the rules.

## 4. Two-week plan for one service

Week 1: boot and map the repo, CI on every change, request ids and JSON logs, golden-signal metrics, liveness and readiness, remove production data from non-prod and rotate any live key that sat on a laptop.

Week 2: one documented deploy path from `main` with a practiced rollback, server-side flags with a kill switch the on-call person can use without a deploy, a first canary or at least a staged rollout, and three alerts that page a named person.

Then a 90-day list: tracing across services, an error budget or weekly reliability review, preview environments if cost allows, a changelog support can read, load tests on paths that move money or identity, data-quality checks on live feeds.

## Output format

1. Current-state snapshot: what is already live, what is static, what is unknown
2. Gaps against the four loops
3. Two-week plan, day by day, with owners and the tool or file for each item
4. Risks, especially production data in the wrong place and live third-party credentials
5. Definition of done the team can reuse for the next service
6. 90-day list

See `examples/two-week-plan.md` for the shape.
