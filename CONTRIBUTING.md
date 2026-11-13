# Contributing

Bug reports and patches are welcome. For anything larger than a small fix, open an issue first so we can agree on the shape before you write it.

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]'
make check
```

`make check` runs ruff (lint and format), mypy in strict mode over `src/`, the content lint in `scripts/check.py`, and pytest including the Hypothesis property tests. CI runs the same steps on Python 3.11 to 3.13.

## What a good patch looks like

- One change per pull request, with a test that fails before it and passes after.
- Standard library only in `src/`. Optional integrations go behind an extra, like `otel`.
- Anything shared between request threads takes the lock that guards it. Say which in a comment.
- New routes go on the right listener: user-facing on the public table, anything operational on the admin table.
- Metric labels come from fixed sets (route templates, status codes), never from request data.
- Update `CHANGELOG.md` under "Unreleased".

## Things we will not merge

- Real personal data, real phone numbers, emails or keys in tests or examples. Use RFC 2606 domains and obviously fake values. `scripts/check.py` enforces this.
- A framework dependency for the core service. The point is that it can be read in an afternoon.

## Commit messages

Short imperative subject in lower case (`health: one run per check at a time`), a blank line, then why.
