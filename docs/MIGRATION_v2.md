# AI Diff Reviewer v2

**Default pin:** `uses: DailybotHQ/ai-diff-reviewer@v2`  
**Skill:** `npx skills add DailybotHQ/ai-diff-reviewer@v2 --skill ai-diff-reviewer`  
(or `npx skills update ai-diff-reviewer`)

Exact pin when you want a frozen tag: `@v2.0.0` (skill frontmatter `version: "2.0.0"`).

## Contract

- No `action.yml` inputs renamed or removed for this major.
- Env-var prefix stays `AIPRR_`.
- Repo path: `DailybotHQ/ai-diff-reviewer`.

## Platform behaviour (v2)

1. **Iteration-Aware Review** runs on every CI review (default `first-pass-exhaustive`). Round 2+ of a generation may dedupe non-critical findings already seen.
2. **Local skill reviews** stay a full pass — they do not run IAR dedup.
3. Escape / reset / emergency-bypass:
   - Escape once: label `full-review-please` (default `iteration-escape-label`)
   - Clean slate: remove `applied-label` after a successful stamp (five-condition `USER_FORCED_RESET` — [ITERATION_AWARENESS.md § 8.5](ITERATION_AWARENESS.md))
   - Emergency bypass: opt-in `skip-review-label: skip-ai-review` (+ ruleset) — [TRIGGER_MODES.md](TRIGGER_MODES.md)

## Further reading

- [CHANGELOG.md](../CHANGELOG.md)
- [ITERATION_AWARENESS.md](ITERATION_AWARENESS.md)
- [examples/iteration-aware.yml](../examples/iteration-aware.yml)
- [examples/skip-review-label.yml](../examples/skip-review-label.yml)
