# Rollout, canary and rollback

How a change gets from a merged PR to all users with `livesvc`'s pieces, and where people usually get hurt.

## The path

1. Merge to `main`. CI runs lint and tests (`.github/workflows/ci.yml`).
2. Build one image, tagged with the commit SHA. The same image goes to every environment; only env vars differ.
3. Deploy to staging. Smoke test `/readyz` and one real request.
4. Canary in prod: one replica of the new image and one fresh replica of the current image, each taking an equal small slice of traffic.
5. Soak for long enough to get a few hundred real requests on each. `livesvc-canary baseline_url canary_url` compares error rate and p95. Point it at the admin listener (`ADMIN_PORT`, default 9090), where `/metrics` lives.
6. Promote in steps (5%, 25%, 100%) with a soak and a comparison between steps, or roll back.

`deploy/github-actions-deploy.yml` sketches steps 2 to 6. The platform-specific commands are left as placeholders because they are the part every team does differently.

## Flags versus deploys

A deploy changes code. A flag changes behaviour of code that is already deployed. Ship risky features dark (flag at 0% in prod), then roll out with the flag, not with the deploy. That way a rollback of a bad feature is a file change that reaches every worker in seconds, not a redeploy that takes minutes and also rolls back everything else in that release.

Every flag has an `owner` and should have a `remove_by` date. `Flags.stale()` lists the ones past their date. A flag older than a quarter is usually dead code with a config file attached.

## Rollback

- Image rollback: redeploy the previous SHA. Practice it once before you need it; time it.
- Flag rollback: set `"killed": true` or drop the percentage. Faster, and the right tool when the problem is one feature.
- Database changes must be backward compatible with the previous release (expand, migrate, contract over separate deploys). Otherwise an image rollback is not a rollback.

## Draining

On SIGTERM the service fails readiness, waits `DRAIN_SECONDS`, then stops. The wait has to be longer than the load balancer's health check interval times its failure threshold, or new requests still arrive after you stopped accepting them. Under gunicorn and Kubernetes, put that wait in a `preStop` hook instead.

## What to alert on

Start with three, each paging a named person:

- 5xx rate per route above a threshold for 5 minutes (`http_requests_total{status=~"5.."}`)
- p95 latency per route above a threshold for 10 minutes (`http_request_duration_seconds_bucket`)
- readiness failing on more than half the replicas

Add saturation (CPU, memory, connection pool) once those are quiet.
