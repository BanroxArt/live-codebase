# Example two-week plan (fictional repo)

This sample shows the skill's report shape. It is not a description of Banrox's private codebase. The product and team below are made up.

## 1. Current-state snapshot

Product: a two-person SaaS web app (one API, one web client, one Postgres database) hosted on a single cloud VM. Deploys are a manual `git pull` and process restart. The team can build and demo locally. Releases happen when someone has time, usually every few weeks.

- Ship: no CI on the default branch. No documented rollback. Schema changes are applied by hand.
- See: application logs go to a file on the VM. No request IDs, no uptime check, no owner for alerts.
- Steer: no feature flags. Every change is on or off with the next deploy.
- Live data: production uses real customer records. A shared staging database was copied from production six months ago and still contains personal data. Third-party email is pointed at the live vendor account from the developers' laptops.

## 2. Gaps against the four loops

1. No automated test or deploy path, and no practiced rollback.
2. No golden signals and no page to a named person.
3. No flag system, so a bad change cannot be turned off without a deploy.
4. Production data in staging, and live vendor credentials on laptops.

## 3. Two-week plan

Owners below are fictional. Tools are boring defaults the team already has or can add without a new platform project.

### Week 1: read the repo and close the worst data risk

- Day 1: Boot the repo locally from the README and note anything that does not run. Map services, data stores, and outbound vendors.
- Day 2: Stand up CI that runs the existing tests on every change to the default branch.
- Day 3: Replace the production-data staging copy with a masked or synthetic subset. Rotate any vendor key that lived on a laptop and was also used in production.
- Day 4: Add structured logs with a request ID, plus error rate, latency, and traffic on the API.
- Day 5: Document the current deploy and a rollback (redeploy the previous build). Practice the rollback once on staging.

### Week 2: make one service live

- Days 6–7: Trigger deploys from the default branch or a tagged release, for the API only.
- Day 8: Add a health endpoint and an uptime check that pages a named person.
- Day 9: Add one server-side feature-flag table, default off, with a named owner who can flip a flag without a deploy.
- Day 10: Put the next risky change behind that flag. Write a short definition of done (below) and a 90-day follow-up list.

## 4. Risks

- Production data in staging is the highest immediate risk. Do not copy it again, and do not paste it into prompts.
- Live email credentials on laptops can send real mail from a dev machine. Give staging its own vendor account or test mode.
- A CI deploy to production without a practiced rollback can turn a small change into an outage.

## 5. Definition of done

The API is live, for this skill's purposes, when all four are true:

1. A change can go from a pull request to production on a documented path, and a rollback has been practiced once.
2. The team can see error rate, latency, and traffic, and a health check pages a real person.
3. One feature flag can turn off a risky behavior without a deploy.
4. Production uses permissioned real data; non-prod uses masked or synthetic data; third-party integrations have a non-prod mode.

Do not invent architecture that is not in the tree. If you cannot run the repo, say so.
