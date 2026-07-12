<!--
Extension prompt for TypeScript / React repositories.

Consume via:

    with:
      prompt-extension-file: examples/prompts/typescript-strict.md

This APPENDS to the base prompt — do NOT use it with `prompt-file:`
(which replaces the base). See docs/PROMPTS.md for the full "base vs
extension vs replacement" decision guide.
-->

## Project-specific severity overrides (TypeScript / React)

The base severity rubric applies. On top of it:

- ALWAYS `critical`:
  - `any` type introduced on a public API boundary (props, return
    types, exported function signatures). `unknown` is fine; `any`
    silently opts out of type checking.
  - `dangerouslySetInnerHTML` fed by anything other than a static
    string constant.
  - `useEffect` with a missing or intentionally-elided dependency
    array (unless commented and justified in the diff).
  - `localStorage`/`sessionStorage` for auth tokens or secrets.
  - Direct DOM mutation inside a React component (`document.getElement…`
    to write, not just read).
- ALWAYS `warning`:
  - Non-null assertion (`!`) on values that could legitimately be
    `null` (form inputs, refs before mount, network responses).
  - `useState` initialized to a value that requires an expensive
    computation on every render (should be a lazy initialiser).
  - New `<img>` without an explicit `alt` attribute.
  - Client-side environment variable read (`process.env.NEXT_PUBLIC_*`
    or similar) that leaks a secret at build time.
- ALWAYS `info` (do not block):
  - Choice of `interface` vs `type` when both would work.
  - Absent `readonly` on immutable object types (nice-to-have, not
    blocking).

## Reviewer style additions

- Reference the exact React docs URL when suggesting a hook or
  concurrent-feature change.
- When flagging a hydration/SSR mismatch, cite which environment
  (server vs. client) is producing the mismatched output.
