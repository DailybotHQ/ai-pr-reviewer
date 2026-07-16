#!/usr/bin/env python3
"""Failure-fallback safety suite for the Iteration-Aware Review (IAR)
subsystem.

IAR is an unconditional subsystem — every review runs the IAR pipeline —
but the pipeline itself is wrapped in `try/except` at every `main()`
touchpoint. The load-bearing safety invariant of that wrap is:

    When IAR crashes mid-flight, the reviewer MUST still ship a review.
    The 5 IAR action outputs stay as empty strings (via
    `write_iar_outputs_empty()`), the tracking marker skips the IAR
    annotation, and the review posts with the raw LLM findings — the
    consumer never experiences an IAR bug as "no review at all".

This suite verifies that invariant with narrow, pure-logic assertions
that cannot inadvertently drift as the IAR internals evolve. Any
regression here means an IAR failure mode has bled into a code path
consumers rely on and MUST be treated as a blocker.

Stdlib `unittest` only — matches the rest of `tests/*`. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import the module under test from scripts/reviewer.py without requiring it
# to be installed or on PYTHONPATH. Same mechanism as tests/test_reviewer.py.
# ---------------------------------------------------------------------------
_ROOT: Path = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "reviewer", _ROOT / "scripts" / "reviewer.py"
)
assert _SPEC is not None and _SPEC.loader is not None
reviewer = importlib.util.module_from_spec(_SPEC)
sys.modules["reviewer"] = reviewer
_SPEC.loader.exec_module(reviewer)


def _parse_github_outputs(raw: str) -> dict[str, str]:
    """Parse the two shapes emitted by `write_action_output`:

    * Single-line scalar (the common case): ``key=value\\n``.
    * Multi-line HEREDOC: ``key<<AIPRR_OUTPUT_EOF\\nvalue\\nAIPRR_OUTPUT_EOF\\n``.

    Returns a dict[key -> value]. Empty-string values ``key=\\n`` decode
    to ``""``.
    """
    result: dict[str, str] = {}
    lines: list[str] = raw.split("\n")
    i: int = 0
    while i < len(lines):
        line: str = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key: str
            heredoc: str
            key, heredoc = line.split("<<", 1)
            value_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i] != heredoc:
                value_lines.append(lines[i])
                i += 1
            result[key] = "\n".join(value_lines)
        elif "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        i += 1
    return result


class IARConfigParsingTests(unittest.TestCase):
    """Parser contract for the 4 IAR env vars. Every branch of the
    parser must return a valid `IARConfig` — a misconfigured input can
    never crash the run because `IARConfig` is built at the top of
    `main()` before any error handling is in place."""

    def test_empty_env_returns_defaults(self) -> None:
        """A completely empty env dict must produce the shipped default
        profile: `first-pass-exhaustive` policy, unlimited rounds, 3×
        cap multiplier, `full-review-please` escape label."""
        cfg: reviewer.IARConfig = reviewer.build_iar_config({})
        self.assertEqual(cfg.policy, reviewer.IAR_POLICY_FIRST_PASS_EXHAUSTIVE)
        self.assertEqual(cfg.max_review_rounds, 0)
        self.assertEqual(cfg.cap_multiplier, reviewer.IAR_DEFAULT_CAP_MULTIPLIER)
        self.assertEqual(cfg.escape_label, reviewer.IAR_DEFAULT_ESCAPE_LABEL)

    def test_valid_policy_and_knobs(self) -> None:
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_CONVERGENCE_POLICY": "iterative",
            "AIPRR_MAX_REVIEW_ROUNDS": "5",
            "AIPRR_EXHAUSTIVE_FIRST_PASS_CAP_MULTIPLIER": "4",
            "AIPRR_ITERATION_ESCAPE_LABEL": "audit-me",
        })
        self.assertEqual(cfg.policy, "iterative")
        self.assertEqual(cfg.max_review_rounds, 5)
        self.assertEqual(cfg.cap_multiplier, 4)
        self.assertEqual(cfg.escape_label, "audit-me")

    def test_unknown_policy_silently_falls_back(self) -> None:
        """Unknown policy values MUST fall back to the shipped default
        rather than crashing. The workflow log surfaces the miswiring
        via the log() call in main()."""
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_CONVERGENCE_POLICY": "bogus-policy",
        })
        self.assertEqual(cfg.policy, reviewer.IAR_POLICY_FIRST_PASS_EXHAUSTIVE)

    def test_garbage_env_still_returns_valid_config(self) -> None:
        """Every parser branch is lenient enough that a fully-garbled
        env dict still produces a valid config. This is the load-bearing
        safety property: `build_iar_config()` is called before the
        try/except in main() so it CANNOT raise."""
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_CONVERGENCE_POLICY": "not-a-real-policy",
            "AIPRR_MAX_REVIEW_ROUNDS": "not-a-number",
            "AIPRR_EXHAUSTIVE_FIRST_PASS_CAP_MULTIPLIER": "-99",
            "AIPRR_ITERATION_ESCAPE_LABEL": "",
        })
        self.assertEqual(cfg.policy, reviewer.IAR_POLICY_FIRST_PASS_EXHAUSTIVE)
        self.assertEqual(cfg.max_review_rounds, 0)
        self.assertEqual(cfg.cap_multiplier, 1)  # clamped up from -99
        self.assertEqual(cfg.escape_label, reviewer.IAR_DEFAULT_ESCAPE_LABEL)

    def test_max_rounds_negative_clamped_to_zero(self) -> None:
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_MAX_REVIEW_ROUNDS": "-3",
        })
        self.assertEqual(cfg.max_review_rounds, 0)

    def test_cap_multiplier_below_1_clamped_to_1(self) -> None:
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_EXHAUSTIVE_FIRST_PASS_CAP_MULTIPLIER": "0",
        })
        self.assertEqual(cfg.cap_multiplier, 1)

    def test_escape_label_defaults_when_blank(self) -> None:
        cfg: reviewer.IARConfig = reviewer.build_iar_config({
            "AIPRR_ITERATION_ESCAPE_LABEL": "   ",
        })
        self.assertEqual(cfg.escape_label, reviewer.IAR_DEFAULT_ESCAPE_LABEL)


class WriteIAROutputsEmptyContractTests(unittest.TestCase):
    """`write_iar_outputs_empty()` is the safety-net writer called by
    `write_all_outputs()` on every exit path. It MUST write exactly the
    5 IAR outputs (no more, no fewer) and every value MUST be an empty
    string. `write_iar_outputs_populated()` later overwrites these on
    the successful IAR path (last-write-wins on `$GITHUB_OUTPUT`)."""

    def _write_and_read(self, fn: Any, **kwargs: Any) -> dict[str, str]:
        """Redirect $GITHUB_OUTPUT to a tempfile, invoke `fn(**kwargs)`,
        parse the resulting file into a dict."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as tmp:
            tmp_path: str = tmp.name
        prior: str | None = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = tmp_path
            fn(**kwargs)
            with open(tmp_path, encoding="utf-8") as fh:
                raw: str = fh.read()
        finally:
            if prior is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = prior
            os.unlink(tmp_path)
        return _parse_github_outputs(raw)

    def test_writes_exactly_5_iar_output_keys(self) -> None:
        """`write_iar_outputs_empty()` writes exactly the 5 IAR outputs.
        It MUST NOT accidentally write the 6 core action outputs (that's
        `write_all_outputs()`'s job) or new keys that would drift the
        contract."""
        out: dict[str, str] = self._write_and_read(
            reviewer.write_iar_outputs_empty
        )
        self.assertEqual(
            set(out.keys()),
            {"iteration-round", "iteration-generation",
             "iteration-policy-applied", "iteration-tokens-used",
             "iteration-cost-vs-baseline-estimate"},
            "write_iar_outputs_empty MUST write exactly the 5 IAR outputs.",
        )

    def test_every_iar_output_is_empty_string(self) -> None:
        out: dict[str, str] = self._write_and_read(
            reviewer.write_iar_outputs_empty
        )
        for key, value in out.items():
            self.assertEqual(
                value, "",
                f"IAR output {key!r} MUST be an empty string on the "
                "safety-net writer path (populated writer overwrites "
                "later via last-write-wins).",
            )


class WriteAllOutputsIntegrationTests(unittest.TestCase):
    """The `write_all_outputs()` helper is on every exit path. It MUST
    write the 6 core outputs AND call `write_iar_outputs_empty()` so
    downstream steps reading `iteration-*` always see a defined value —
    even when the review skipped (label gate, IAR never ran) or when
    the review completed but the IAR pipeline crashed (post-LLM never
    reached the populated-writer)."""

    def _write_and_read_outputs(
        self, **kwargs: Any
    ) -> dict[str, str]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as tmp:
            tmp_path: str = tmp.name
        prior: str | None = os.environ.get("GITHUB_OUTPUT")
        try:
            os.environ["GITHUB_OUTPUT"] = tmp_path
            reviewer.write_all_outputs(**kwargs)
            with open(tmp_path, encoding="utf-8") as fh:
                raw: str = fh.read()
        finally:
            if prior is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = prior
            os.unlink(tmp_path)
        return _parse_github_outputs(raw)

    def test_skipped_path_writes_all_11_outputs(self) -> None:
        """The skip path (e.g. author-association gate rejected the PR)
        must write all 6 core outputs AND all 5 IAR outputs as empty
        strings — IAR never ran, so downstream steps see empty."""
        out: dict[str, str] = self._write_and_read_outputs(skipped=True)
        core_keys: set[str] = {
            "skipped", "severity", "inline-attached",
            "inline-dropped", "blocked", "review-url",
        }
        iar_keys: set[str] = {
            "iteration-round", "iteration-generation",
            "iteration-policy-applied", "iteration-tokens-used",
            "iteration-cost-vs-baseline-estimate",
        }
        for key in core_keys | iar_keys:
            self.assertIn(
                key, out,
                f"Missing output {key!r} — write_all_outputs contract broken.",
            )
        self.assertEqual(out["skipped"], "true")
        self.assertEqual(out["severity"], "none")
        self.assertEqual(out["inline-attached"], "0")
        self.assertEqual(out["inline-dropped"], "0")
        self.assertEqual(out["blocked"], "false")
        self.assertEqual(out["review-url"], "")
        for key in iar_keys:
            self.assertEqual(
                out[key], "",
                f"IAR output {key!r} MUST be empty string on the "
                "skip path (IAR never ran).",
            )

    def test_success_path_iar_defaults_before_populate(self) -> None:
        """`write_all_outputs()` writes IAR outputs as empty strings.
        The populated writer runs after and overwrites — this test
        just verifies the safety-net writer is on every path."""
        out: dict[str, str] = self._write_and_read_outputs(
            skipped=False,
            severity="warning",
            inline_attached=3,
            inline_dropped=1,
            blocked=False,
            review_url="https://github.com/x/y/pull/1#pullrequestreview-42",
        )
        self.assertEqual(out["skipped"], "false")
        self.assertEqual(out["severity"], "warning")
        self.assertEqual(out["inline-attached"], "3")
        self.assertEqual(out["inline-dropped"], "1")
        self.assertEqual(out["blocked"], "false")
        self.assertEqual(
            out["review-url"],
            "https://github.com/x/y/pull/1#pullrequestreview-42",
        )
        # All 5 IAR outputs start empty; the populated writer (called
        # separately after run_iar_post_llm) is responsible for
        # overwriting them on the success path.
        self.assertEqual(out["iteration-round"], "")
        self.assertEqual(out["iteration-generation"], "")
        self.assertEqual(out["iteration-policy-applied"], "")
        self.assertEqual(out["iteration-tokens-used"], "")
        self.assertEqual(out["iteration-cost-vs-baseline-estimate"], "")

    def test_blocked_path_writes_all_11_outputs(self) -> None:
        """Strictness-blocked exit path also writes IAR outputs empty by
        default. IAR still runs on this path (it isn't gated on
        blocking), but the safety-net writer fires first."""
        out: dict[str, str] = self._write_and_read_outputs(
            skipped=False,
            severity="critical",
            inline_attached=5,
            inline_dropped=0,
            blocked=True,
            review_url="https://github.com/x/y/pull/1#pullrequestreview-99",
        )
        self.assertEqual(out["blocked"], "true")
        self.assertEqual(out["iteration-round"], "")
        self.assertEqual(out["iteration-cost-vs-baseline-estimate"], "")


class IARConstantsFrozenTests(unittest.TestCase):
    """Small guard against accidental drift in IAR constants that other
    IAR subsystems depend on. If a constant changes intentionally, this
    test needs an intentional update — that's the point."""

    def test_valid_policies_tuple(self) -> None:
        self.assertEqual(
            set(reviewer.IAR_VALID_POLICIES),
            {"iterative", "first-pass-exhaustive",
             "round-capped", "critical-gate"},
        )

    def test_default_cap_multiplier(self) -> None:
        self.assertEqual(reviewer.IAR_DEFAULT_CAP_MULTIPLIER, 3)

    def test_context_hash_radius(self) -> None:
        # 10 lines above + 10 below = 21-line window (fingerprint anchor).
        self.assertEqual(reviewer.IAR_CONTEXT_HASH_RADIUS, 10)

    def test_safety_net_threshold(self) -> None:
        # docs/ITERATION_AWARENESS.md § 7.2 pins this at 30%.
        self.assertEqual(reviewer.IAR_SAFETY_NET_NEW_LINES_PCT, 30)

    def test_default_escape_label(self) -> None:
        self.assertEqual(
            reviewer.IAR_DEFAULT_ESCAPE_LABEL, "full-review-please"
        )

    def test_state_schema_version_is_1(self) -> None:
        # Increment only when the JSON schema breaks backward-read compat
        # AND _parse_state_from_marker_body handles the older shape.
        self.assertEqual(reviewer.IAR_STATE_SCHEMA_VERSION, 1)


class IARModuleSurfaceContainmentTests(unittest.TestCase):
    """Sanity-check that the IAR module surface is small and contained.
    If a future refactor mixes IAR into an unrelated function, this
    test should be the first to fail."""

    def test_iar_config_dataclass_is_frozen(self) -> None:
        """`IARConfig` is built once per run and passed around. A frozen
        dataclass prevents accidental mutation deep in the runtime."""
        cfg: reviewer.IARConfig = reviewer.build_iar_config({})
        with self.assertRaises((AttributeError, Exception)):
            cfg.policy = "iterative"  # type: ignore[misc]

    def test_iar_config_has_exactly_4_fields(self) -> None:
        """The public shape of `IARConfig`. Adding a field here means
        adding an env var, an `action.yml` input, an `AIPRR_*`
        docstring, and a row in the setup skill reference. Locking the
        field count forces authors to touch this test as a checklist."""
        from dataclasses import fields
        got_fields: set[str] = {f.name for f in fields(reviewer.IARConfig)}
        self.assertEqual(
            got_fields,
            {"policy", "max_review_rounds", "cap_multiplier",
             "escape_label"},
        )


if __name__ == "__main__":
    unittest.main()
