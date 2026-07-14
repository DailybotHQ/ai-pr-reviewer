<!--
Extension prompt for Python-heavy repositories.

Consume via:

    with:
      prompt-extension-file: examples/prompts/python-strict.md

This APPENDS to the base prompt — do NOT use it with `prompt-file:`
(which replaces the base). See docs/PROMPTS.md for the full "base vs
extension vs replacement" decision guide.
-->

## Project-specific severity overrides (Python)

The base severity rubric applies. On top of it:

- ALWAYS `critical`:
  - Use of `pickle.loads`, `yaml.load` (without `SafeLoader`), or
    `subprocess` with `shell=True` on untrusted input.
  - Bare `except:` in production code paths (silently swallows
    `KeyboardInterrupt` and `SystemExit`).
  - Any change to authentication, session, or crypto code without a
    corresponding test in the PR.
  - Financial/monetary computation on `float` rather than
    `decimal.Decimal`.
- ALWAYS `warning` (upgrade from the default rubric):
  - Missing type hints on new public API functions.
  - Mutable default arguments (`def f(x=[]):`).
  - Adding a synchronous blocking call inside an async function.
  - Missing tests for a new branch of business logic.
- ALWAYS `info` (downgrade — do not block on these):
  - Style choices covered by the project's formatter/linter (ruff/black
    catches them; the reviewer should not duplicate).
  - Docstring style if the codebase is otherwise undocstringed.

## Reviewer style additions

- Prefer citing the specific PEP or stdlib doc URL when suggesting an
  idiomatic Python fix.
- When suggesting `dataclass`, `NamedTuple`, or `Protocol`, name the
  alternative explicitly rather than saying "use a proper type."
