---
name: live-codebase
description: Help a team make a software product “live” rather than static — observability, feature flags, continuous deploy, and safe use of live data. Use when the user says the codebase is a snapshot, releases are rare or manual, they cannot tell what production is doing, they want flags or gradual rollouts, or they want to connect the product to real (permissioned) data instead of fixtures only. Do not use this to exfiltrate production data or to skip consent and privacy rules.
---

# Make the codebase live, not static

A static codebase is a repo that can be built, maybe demoed, and then goes quiet. A live codebase is one the team can ship, observe, and change safely while real users are on it.

## What “live” means here

Four loops, all of them required before you call the system live:

1. **Ship** — a change can go from a pull request to production on a known path, with a way to roll back.
2. **See** — the team can tell, from logs, metrics, and traces, what the app is doing and whether it is broken.
3. **Steer** — behavior can be changed with feature flags or config, not only with a new deploy, and a bad flag can be turned off quickly.
4. **Learn from real data** — the product can use current, permissioned user or market data in production and, where justified, in carefully isolated non-prod environments. Fixtures alone are not “live.”

## How to work

1. Start with a current-state read of the unfamiliar repo, then ask what they have today: language, host, CI, how they deploy, how they know production is down, whether flags exist, and where production data lives. Do not invent architecture that is not in the tree.
2. Pick the thinnest path that gets all four loops working. Prefer boring tools they already pay for.
3. Do not recommend copying production data to laptops or into prompts. If they need realistic data off-prod, prefer masked or synthetic data and a written rule.
4. Give a two-week plan and a 90-day plan. The two-week plan should end with one service that is continuously deployable, observable, and flagged.

## Two-week path (one service)

### Ship

- One CI pipeline that runs tests on every change to the default branch.
- A documented deploy (managed platform, container service, or equivalent) that is triggered from that branch or from a tagged release.
- A rollback: previous image or a revert plus redeploy, practiced once.
- Database changes are backward-compatible or gated; do not require heroics at 11pm as the default path.

### See

- Structured logs with a request or trace id.
- Three golden signals to start: error rate, latency, and traffic. Add saturation when relevant.
- A health endpoint and an uptime check that pages a real person, not a Slack channel no one owns.
- For user-facing money or identity actions, log the action and the outcome without logging secrets or full account numbers.

### Steer

- One feature-flag system (even a simple server-side flag table) with a default of “off.”
- Every new risky feature ships behind a flag.
- A named owner who can turn a flag off without a deploy.
- Flags are temporary. Each flag has a removal condition.

### Live data

- Production uses real, permissioned data only.
- Non-prod uses synthetic or masked data unless there is a written exception.
- If the product depends on third-party APIs (identity, credit, payments, email, SMS), those integrations have a staging mode and a clear “this is live” mode. Never point a shared dev environment at a production bureau, processor, or messaging account by accident.
- Retention and deletion are documented for personal data.

## 90-day extensions

- Tracing across services.
- Error budgets or at least a weekly reliability review.
- Preview environments for pull requests, if cost allows.
- A release changelog users or support can read.
- Load or soak testing on the paths that move money or identity.
- Data quality checks on the live feeds the product depends on (not just “the cron ran”).

## Output format

1. Current-state snapshot (what is static vs already live)
2. Gaps against the four loops
3. Two-week plan with owners and tools
4. Risks (especially around production data and third-party live credentials)
5. A short “definition of done” the team can reuse
