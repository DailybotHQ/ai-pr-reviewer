#!/usr/bin/env python3
"""Validate the `action.yml` public contract in CI.

This is CI-only tooling (it may use third-party packages such as PyYAML); it is
NOT part of the stdlib-only runtime. It guards the contract that consumers and
the README depend on, catching accidental drift before it ships:

  1. `action.yml` parses as YAML.
  2. Required top-level keys exist (`name`, `description`, `runs`).
  3. The action is a composite action (`runs.using == 'composite'`).
  4. The required inputs (`api-key`, `github-token`) are declared.
  5. Every declared input is actually wired into the composite step's `env:`
     block (the `AIPRR_*` mapping) — a declared-but-unread input is dead
     contract surface.
  6. Every declared output has a `value:` expression.

Exit code 0 = contract intact; 1 = a problem was found (prints all problems).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml  # CI-only dependency; see module docstring.

REQUIRED_TOP_LEVEL: tuple[str, ...] = ("name", "description", "runs")
REQUIRED_INPUTS: tuple[str, ...] = ("api-key", "github-token")


def load_action(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} did not parse to a mapping.")
    return data


def collect_env_blob(runs: dict[str, Any]) -> str:
    """Concatenate every composite step's `env:` values into one string.

    We only need a substring search ("is input X referenced anywhere in the
    env wiring?"), so flattening to text is sufficient and robust to how the
    expressions are written.
    """
    parts: list[str] = []
    for step in runs.get("steps", []) or []:
        env: dict[str, Any] = step.get("env", {}) or {}
        for key, value in env.items():
            parts.append(f"{key}={value}")
    return "\n".join(parts)


def main() -> int:
    action_path: Path = Path("action.yml")
    if not action_path.is_file():
        print("ERROR: action.yml not found at repo root.")
        return 1

    action: dict[str, Any] = load_action(action_path)
    problems: list[str] = []

    for key in REQUIRED_TOP_LEVEL:
        if key not in action:
            problems.append(f"missing top-level key: {key!r}")

    runs: dict[str, Any] = action.get("runs", {}) or {}
    if runs.get("using") != "composite":
        problems.append(
            f"runs.using must be 'composite', got {runs.get('using')!r}"
        )

    inputs: dict[str, Any] = action.get("inputs", {}) or {}
    for required in REQUIRED_INPUTS:
        if required not in inputs:
            problems.append(f"missing required input: {required!r}")

    env_blob: str = collect_env_blob(runs)
    for input_name in inputs:
        # Composite actions reference an input via `${{ inputs.<name> }}`.
        if f"inputs.{input_name}" not in env_blob:
            problems.append(
                f"input {input_name!r} is declared but never wired into the "
                "composite step env: (dead contract surface)"
            )

    outputs: dict[str, Any] = action.get("outputs", {}) or {}
    for output_name, spec in outputs.items():
        if not isinstance(spec, dict) or not spec.get("value"):
            problems.append(f"output {output_name!r} has no value: expression")

    if problems:
        print("action.yml contract validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        "action.yml contract OK: "
        f"{len(inputs)} inputs, {len(outputs)} outputs, composite runtime."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
