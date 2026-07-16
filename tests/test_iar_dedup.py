#!/usr/bin/env python3
"""Unit tests for the Iteration-Aware Review (IAR) content-anchored
fingerprinting + dedup engine.

The `TestCriticalAlwaysSurfaces` class is the load-bearing safety gate
for the whole subsystem: `severity == "critical"` findings MUST surface
unconditionally, regardless of prior state shape, policy, generation, or
fingerprint match. Any test in that class that fails is a critical bug
in the safety rail described in docs/ITERATION_AWARENESS.md § 7.1.

Stdlib `unittest` only.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_ROOT: Path = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "reviewer", _ROOT / "scripts" / "reviewer.py"
)
assert _SPEC is not None and _SPEC.loader is not None
reviewer = importlib.util.module_from_spec(_SPEC)
sys.modules["reviewer"] = reviewer
_SPEC.loader.exec_module(reviewer)


def _finding(
    *, path: str = "src/x.py", line: int = 10,
    body: str = "Missing null check.",
    severity: str = "info",
) -> "reviewer.Finding":
    return reviewer.Finding(
        path=path, line=line, body=body, severity=severity,
        start_line=None, side="RIGHT",
    )


def _ctx(*lines: str, path: str = "src/x.py") -> "reviewer.CodeContext":
    return reviewer.CodeContext(path=path, lines=tuple(lines))


def _state(**overrides: Any) -> "reviewer.IterationState":
    base: dict[str, Any] = {
        "version": reviewer.IAR_STATE_SCHEMA_VERSION,
        "generation": 2,
        "generation_range_hash": "hash-abc",
        "round_in_generation": 2,
        "policy_applied": reviewer.IAR_POLICY_ITERATIVE,
        "resolved_fingerprints": [],
        "open_fingerprints_this_gen": [],
        "history": [],
        "base_sha": "base-sha-1",
    }
    base.update(overrides)
    return reviewer.IterationState(**base)


class CodeContextTests(unittest.TestCase):
    def test_lines_around_center(self) -> None:
        ctx: reviewer.CodeContext = _ctx(
            *[f"line {i}" for i in range(1, 21)]
        )
        got: list[str] = ctx.lines_around(10, 3)
        self.assertEqual(got, ["line 7", "line 8", "line 9",
                               "line 10", "line 11", "line 12",
                               "line 13"])

    def test_lines_around_near_start(self) -> None:
        ctx: reviewer.CodeContext = _ctx("a", "b", "c", "d")
        self.assertEqual(ctx.lines_around(1, 3), ["a", "b", "c", "d"])

    def test_lines_around_near_end(self) -> None:
        ctx: reviewer.CodeContext = _ctx("a", "b", "c", "d")
        self.assertEqual(ctx.lines_around(4, 3), ["a", "b", "c", "d"])

    def test_lines_around_empty_file(self) -> None:
        ctx: reviewer.CodeContext = _ctx()
        self.assertEqual(ctx.lines_around(1, 3), [])

    def test_dataclass_is_frozen(self) -> None:
        ctx: reviewer.CodeContext = _ctx("a")
        with self.assertRaises((AttributeError, Exception)):
            ctx.path = "other"  # type: ignore[misc]


class FingerprintDeterminismTests(unittest.TestCase):
    def test_same_finding_same_context_same_fingerprint(self) -> None:
        f: reviewer.Finding = _finding()
        ctx: reviewer.CodeContext = _ctx(
            *[f"line {i}" for i in range(1, 30)]
        )
        a: str = reviewer.finding_fingerprint(
            finding=f, code_context=ctx
        )
        b: str = reviewer.finding_fingerprint(
            finding=f, code_context=ctx
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_different_anchor_different_fingerprint(self) -> None:
        f1: reviewer.Finding = _finding(line=5)
        f2: reviewer.Finding = _finding(line=6)
        ctx: reviewer.CodeContext = _ctx(*[f"L{i}" for i in range(1, 30)])
        self.assertNotEqual(
            reviewer.finding_fingerprint(finding=f1, code_context=ctx),
            reviewer.finding_fingerprint(finding=f2, code_context=ctx),
        )

    def test_same_anchor_different_context_different_fingerprint(self) -> None:
        """Load-bearing behavior: when code around a warning changes,
        the fingerprint changes, so the warning re-surfaces."""
        f: reviewer.Finding = _finding(line=10)
        ctx_a: reviewer.CodeContext = _ctx(*[f"A{i}" for i in range(1, 30)])
        ctx_b: reviewer.CodeContext = _ctx(*[f"B{i}" for i in range(1, 30)])
        self.assertNotEqual(
            reviewer.finding_fingerprint(finding=f, code_context=ctx_a),
            reviewer.finding_fingerprint(finding=f, code_context=ctx_b),
        )

    def test_different_path_different_fingerprint(self) -> None:
        f1: reviewer.Finding = _finding(path="src/a.py")
        f2: reviewer.Finding = _finding(path="src/b.py")
        ctx_a: reviewer.CodeContext = _ctx(path="src/a.py", *[f"a{i}" for i in range(20)])
        ctx_b: reviewer.CodeContext = _ctx(path="src/b.py", *[f"a{i}" for i in range(20)])
        self.assertNotEqual(
            reviewer.finding_fingerprint(finding=f1, code_context=ctx_a),
            reviewer.finding_fingerprint(finding=f2, code_context=ctx_b),
        )

    def test_different_severity_different_fingerprint(self) -> None:
        f_info: reviewer.Finding = _finding(severity="info")
        f_warn: reviewer.Finding = _finding(severity="warning")
        ctx: reviewer.CodeContext = _ctx(*[f"L{i}" for i in range(20)])
        self.assertNotEqual(
            reviewer.finding_fingerprint(finding=f_info, code_context=ctx),
            reviewer.finding_fingerprint(finding=f_warn, code_context=ctx),
        )

    def test_missing_code_context_fallback(self) -> None:
        """When the file didn't exist at review SHA, fingerprint MUST
        still be deterministic (not raise, not random)."""
        f: reviewer.Finding = _finding()
        a: str = reviewer.finding_fingerprint(finding=f, code_context=None)
        b: str = reviewer.finding_fingerprint(finding=f, code_context=None)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_body_truncation_at_200_chars(self) -> None:
        """Two findings that share the first 200 chars of body should
        share the fingerprint — dedupes reworded restatements of the
        same finding at the same anchor."""
        long_a: str = "A" * 200 + " tail one"
        long_b: str = "A" * 200 + " tail two"
        f_a: reviewer.Finding = _finding(body=long_a)
        f_b: reviewer.Finding = _finding(body=long_b)
        self.assertEqual(
            reviewer.finding_fingerprint(finding=f_a, code_context=None),
            reviewer.finding_fingerprint(finding=f_b, code_context=None),
        )


class DedupEngineTests(unittest.TestCase):
    """Every branch of `dedupe_findings_against_prior` except the
    critical-bypass path (that path has its own high-priority test
    class below)."""

    def test_first_review_surfaces_all(self) -> None:
        findings: list[reviewer.Finding] = [
            _finding(line=1), _finding(line=5), _finding(line=10),
        ]
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=findings,
                prior_state=None,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, findings)
        self.assertEqual(got.silenced, [])
        self.assertEqual(len(got.fingerprints_by_finding), 3)

    def test_known_open_silenced(self) -> None:
        f: reviewer.Finding = _finding()
        fp: str = reviewer.finding_fingerprint(
            finding=f, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [])
        self.assertEqual(len(got.silenced), 1)
        self.assertIn("already reported", got.silenced[0].reason)

    def test_known_resolved_surfaces_as_regression(self) -> None:
        """A finding whose fingerprint matches `resolved_fingerprints`
        surfaces — the finding was fixed, then re-appeared. This is a
        regression signal the developer needs to see."""
        f: reviewer.Finding = _finding()
        fp: str = reviewer.finding_fingerprint(
            finding=f, code_context=None
        )
        state: reviewer.IterationState = _state(
            resolved_fingerprints=[fp],
            open_fingerprints_this_gen=[],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f])
        self.assertEqual(got.silenced, [])

    def test_new_finding_surfaces(self) -> None:
        f_new: reviewer.Finding = _finding(line=99)
        f_old: reviewer.Finding = _finding(line=5)
        fp_old: str = reviewer.finding_fingerprint(
            finding=f_old, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp_old],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f_new],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f_new])
        self.assertEqual(got.silenced, [])

    def test_mixed_batch_surfaces_and_silences(self) -> None:
        f_dup: reviewer.Finding = _finding(line=10)
        f_new: reviewer.Finding = _finding(line=25)
        fp_dup: str = reviewer.finding_fingerprint(
            finding=f_dup, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp_dup],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f_dup, f_new],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f_new])
        self.assertEqual(len(got.silenced), 1)


class TestCriticalAlwaysSurfaces(unittest.TestCase):
    """docs/ITERATION_AWARENESS.md § 7.1 SAFETY RAIL.

    Every test in this class is a load-bearing correctness contract
    for the entire IAR subsystem. If any test here fails, DO NOT MERGE.

    The invariant is: `severity == "critical"` findings surface
    unconditionally, regardless of prior state, prior fingerprints,
    resolved-status, generation, or policy. The rail is HARDCODED
    inside `dedupe_findings_against_prior` — every convergence policy
    in Tasks 6/7 flows through that function precisely to guarantee
    this invariant cannot be bypassed at the policy layer.
    """

    def test_critical_in_known_open_still_surfaces(self) -> None:
        f: reviewer.Finding = _finding(severity="critical")
        fp: str = reviewer.finding_fingerprint(
            finding=f, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(
            got.surfaced, [f],
            "CRITICAL SAFETY RAIL VIOLATION: a critical finding matching "
            "a known-open fingerprint was silenced by dedup. See "
            "docs/ITERATION_AWARENESS.md § 7.1.",
        )
        self.assertEqual(got.silenced, [])

    def test_critical_in_resolved_still_surfaces(self) -> None:
        f: reviewer.Finding = _finding(severity="critical")
        fp: str = reviewer.finding_fingerprint(
            finding=f, code_context=None
        )
        state: reviewer.IterationState = _state(
            resolved_fingerprints=[fp],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f])

    def test_critical_in_both_lists_still_surfaces(self) -> None:
        """Overlap in both lists is pathological but must not silence."""
        f: reviewer.Finding = _finding(severity="critical")
        fp: str = reviewer.finding_fingerprint(
            finding=f, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp],
            resolved_fingerprints=[fp],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f])

    def test_multiple_criticals_all_surface(self) -> None:
        f1: reviewer.Finding = _finding(
            severity="critical", line=5, body="null deref A"
        )
        f2: reviewer.Finding = _finding(
            severity="critical", line=15, body="null deref B"
        )
        f3: reviewer.Finding = _finding(
            severity="critical", line=25, body="null deref C"
        )
        fps: list[str] = [
            reviewer.finding_fingerprint(finding=f, code_context=None)
            for f in (f1, f2, f3)
        ]
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=fps,
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f1, f2, f3],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f1, f2, f3])
        self.assertEqual(got.silenced, [])

    def test_critical_surfaces_even_when_infos_are_silenced(self) -> None:
        """Mixed batch: infos matching prior fingerprints are silenced,
        critical is surfaced. This is the realistic prod scenario."""
        f_info: reviewer.Finding = _finding(
            severity="info", line=5, body="lint nit"
        )
        f_crit: reviewer.Finding = _finding(
            severity="critical", line=15, body="deref of None"
        )
        fp_info: str = reviewer.finding_fingerprint(
            finding=f_info, code_context=None
        )
        fp_crit: str = reviewer.finding_fingerprint(
            finding=f_crit, code_context=None
        )
        state: reviewer.IterationState = _state(
            open_fingerprints_this_gen=[fp_info, fp_crit],
        )
        got: reviewer.DedupResult = (
            reviewer.dedupe_findings_against_prior(
                new_findings=[f_info, f_crit],
                prior_state=state,
                code_contexts={},
            )
        )
        self.assertEqual(got.surfaced, [f_crit])
        self.assertEqual(len(got.silenced), 1)
        self.assertEqual(got.silenced[0].finding, f_info)

    def test_critical_bypasses_across_all_prior_state_shapes(self) -> None:
        """Property-esque: 5 different prior state shapes, each
        containing the critical's fingerprint in various positions. In
        all 5 the critical must surface."""
        f_crit: reviewer.Finding = _finding(
            severity="critical", body="crit"
        )
        fp: str = reviewer.finding_fingerprint(
            finding=f_crit, code_context=None
        )
        shapes: list[reviewer.IterationState] = [
            _state(open_fingerprints_this_gen=[fp]),
            _state(resolved_fingerprints=[fp]),
            _state(
                open_fingerprints_this_gen=[fp, "other1"],
                resolved_fingerprints=["other2", fp],
            ),
            _state(
                open_fingerprints_this_gen=[fp] * 10,
            ),
            _state(
                resolved_fingerprints=[fp] * 10,
            ),
        ]
        for i, state in enumerate(shapes):
            with self.subTest(shape=i):
                got: reviewer.DedupResult = (
                    reviewer.dedupe_findings_against_prior(
                        new_findings=[f_crit],
                        prior_state=state,
                        code_contexts={},
                    )
                )
                self.assertEqual(
                    got.surfaced, [f_crit],
                    f"CRITICAL SAFETY RAIL VIOLATION at shape {i}. See "
                    "docs/ITERATION_AWARENESS.md § 7.1.",
                )


class ResolveFindingStatusTests(unittest.TestCase):
    """`resolve_finding_status` partitions prior open fingerprints into
    still-open vs newly-resolved based on current-run fingerprints."""

    def test_matching_fingerprints_marked_still_open(self) -> None:
        current: dict[int, str] = {0: "fp-a", 1: "fp-b"}
        still, resolved = reviewer.resolve_finding_status(
            prior_open_fingerprints=["fp-a", "fp-b"],
            current_fps=current,
        )
        self.assertEqual(sorted(still), ["fp-a", "fp-b"])
        self.assertEqual(resolved, [])

    def test_missing_fingerprints_marked_resolved(self) -> None:
        still, resolved = reviewer.resolve_finding_status(
            prior_open_fingerprints=["fp-a", "fp-b"],
            current_fps={0: "fp-c"},
        )
        self.assertEqual(still, [])
        self.assertEqual(sorted(resolved), ["fp-a", "fp-b"])

    def test_partial_overlap(self) -> None:
        still, resolved = reviewer.resolve_finding_status(
            prior_open_fingerprints=["fp-a", "fp-b", "fp-c"],
            current_fps={0: "fp-a", 1: "fp-c"},
        )
        self.assertEqual(sorted(still), ["fp-a", "fp-c"])
        self.assertEqual(sorted(resolved), ["fp-b"])

    def test_deterministic_ordering(self) -> None:
        """Byte-identical inputs → byte-identical outputs (so marker
        embed is deterministic)."""
        a: tuple[list[str], list[str]] = (
            reviewer.resolve_finding_status(
                prior_open_fingerprints=["z", "a", "m"],
                current_fps={},
            )
        )
        b: tuple[list[str], list[str]] = (
            reviewer.resolve_finding_status(
                prior_open_fingerprints=["z", "a", "m"],
                current_fps={},
            )
        )
        self.assertEqual(a, b)
        self.assertEqual(a[1], ["a", "m", "z"])


class LoadCodeContextTests(unittest.TestCase):
    """`load_code_context` shells out via safe_repo_path + git show.
    Tests use mocking to stay hermetic."""

    def test_returns_code_context_on_success(self) -> None:
        completed: subprocess.CompletedProcess[str] = (
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="line1\nline2\nline3",
            )
        )
        with patch.object(subprocess, "run", return_value=completed):
            got: reviewer.CodeContext | None = reviewer.load_code_context(
                path="scripts/reviewer.py",
                review_sha="deadbeef",
            )
        self.assertIsNotNone(got)
        self.assertEqual(got.lines, ("line1", "line2", "line3"))

    def test_empty_path_returns_none(self) -> None:
        self.assertIsNone(
            reviewer.load_code_context(path="", review_sha="x")
        )

    def test_empty_review_sha_returns_none(self) -> None:
        self.assertIsNone(
            reviewer.load_code_context(
                path="scripts/reviewer.py", review_sha=""
            )
        )

    def test_git_error_returns_none(self) -> None:
        """File missing at that SHA is a normal case — return None
        rather than crash the reviewer."""
        with patch.object(
            subprocess, "run",
            side_effect=subprocess.CalledProcessError(
                returncode=128, cmd=["git", "show"],
            ),
        ):
            got: reviewer.CodeContext | None = reviewer.load_code_context(
                path="scripts/reviewer.py", review_sha="deadbeef",
            )
        self.assertIsNone(got)

    def test_path_escape_returns_none(self) -> None:
        """`safe_repo_path` rejects paths escaping the workspace →
        load_code_context returns None, never raises."""
        got: reviewer.CodeContext | None = reviewer.load_code_context(
            path="../../../etc/passwd", review_sha="deadbeef",
        )
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
