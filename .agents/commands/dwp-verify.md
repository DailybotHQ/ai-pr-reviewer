---
description: Objectively verify repository & plan conformance to the DWP spec (provided by the installed `deepworkplan` skill)
---

# /dwp-verify — provided by the `deepworkplan` skill

> Thin alias. The flow lives in the installed `deepworkplan` skill — this file
> only routes to it, so there is a single source of truth and no drift.

## What to do

Route this invocation to the **verify** sub-skill of the installed `deepworkplan`
skill and follow it: read `.agents/skills/deepworkplan/verify/SKILL.md` and
execute its flow. The sub-skill produces an objective CONFORMANT / NOT
CONFORMANT verdict against the specification's Conformance document, and its
report is emitted under this repo's gitignored `.dwp/` — never the legacy
`.agent_commands/agent_deep_work_plans/results/` path.

> Other agents: invoke the skill's `deepworkplan-verify` sub-skill directly
> (`/deepworkplan-verify` in Claude Code, `#deepworkplan-verify` elsewhere). This
> `dwp-verify` file is the shorter, conventional alias.
