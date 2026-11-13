# live-codebase

A two-week plan to take one service from a static repository to a live one: ship on a known path, observe what production is doing, steer with feature flags, and use permissioned real data. It starts with a current-state read of the unfamiliar codebase. It is not a rewrite proposal.

Author: Art Akopian, CEO of [Banrox](https://banrox.com)

## What it does

This repository is an agent skill. After you install it, ask your coding agent to help make a product "live" rather than static. The agent should:

- Read the current repo and record what is already live versus still static
- Close gaps across four loops: Ship, See, Steer, and Learn from real data
- Prefer boring tools the team already pays for
- Refuse to copy production data onto laptops or into prompts
- Return a report: current-state snapshot, gaps, a two-week plan with owners and tools, risks, and a short definition of done, plus a 90-day extension list

A static codebase can be built and maybe demoed, then goes quiet. A live codebase is one the team can ship, observe, and change safely while real users are on it.

## Install

Copy `SKILL.md` into your agent's skills folder, for example:

```text
.cursor/skills/live-codebase/SKILL.md
```

or the equivalent path your agent documents. Restart or reload skills if your agent requires it.

## Use

Point the agent at a repository you own or are authorized to work in. Say how you deploy today, how you know production is down, whether feature flags exist, and where production data lives. If you cannot run the repo yet, say so; the skill should not invent architecture that is not in the tree.

## Example

See [examples/two-week-plan.md](examples/two-week-plan.md) for a fictional worked example of the expected report shape. It is not a description of Banrox's private codebase.

## Limitations

- Not a rewrite, a platform migration, or a promise that any specific vendor is required.
- Not permission to exfiltrate production data, skip privacy rules, or point shared dev environments at live bureaus, processors, or messaging accounts.
- The two-week plan covers one service. Broader rollout is a 90-day follow-up, not a week-one rewrite.
- If the repo cannot be built from what is in the tree, the skill should say so.

## License

The `LICENSE` file currently contains the MIT License text, with copyright holder Banrox Inc. and year 2026. This is a placeholder. The public licence is pending a final pick by Banrox and may change before the repository is published. Do not treat MIT as final until that decision is recorded.

## Checks

From the repository root:

```text
python3 scripts/check.py
```

This validates skill frontmatter, required sections, example files, local links, and a lint that public files do not include internal names or unsupported certification or traction claims.

## Contributing

Issues and patches are welcome after a public repository exists.

## Related skills

Sibling skills from the same author, each in its own repository:

- `fraud-prevention` — defensive fraud-program checklist
- `cybersecurity-baseline` — small-company security baseline (not a certification)
- `calling-bot-twilio` — consent-gated Twilio calling pattern (not a dialer)
