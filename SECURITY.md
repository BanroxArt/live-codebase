# Security policy

## Reporting a problem

Please report security problems privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for anything that would let someone read data they should not, reach the admin surface, or take the service down.

Include the request you sent, what you expected and what happened. Synthetic data only, please.

You should get an acknowledgement within five working days. Fixes land on `main` with a regression test and a CHANGELOG entry that credits you unless you ask not to be named.

## Supported versions

This is a 0.x reference project. Only the latest commit on `main` gets fixes.

## In scope

- Reading profile fields, or reading for a purpose, that the caller's role does not allow, or an access that leaves no audit row.
- Reaching `/metrics`, `/version` or `/admin/*` through the public listener.
- Request input that grows metrics series, memory or threads without bound.
- Secrets or personal data reaching logs despite the redaction rules.

## Out of scope

- `auth.py` is a stub: no revocation, no key rotation, tokens are bearer tokens. Replacing it with a real identity provider is expected, so its missing features are not vulnerabilities. A flaw in how it verifies a signature or an expiry is in scope.
- Deployments that publish the admin port or set `TRACE_TRUST_PARENT=true` on a public edge.
