#!/usr/bin/env python3
"""
Shared grading helpers for the two grading tiers.

Both tiers grade the same staged pairs in tests/test_files/<assignment>/ and assert
the student notebook scores 0 and the solution scores full marks. They differ only in
WHERE otter runs:
  - run_otter_grade_tests.py  -> `docker run base-user-image ... otter run`
  - standalone-grade-check    -> `docker exec <otter-srv-stdalone container> otter grade`

This module owns results.json scoring and the pair-iteration/assertion loop so the two
entry points stay thin and consistent.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

# Staged pairs live under the course content tree (written by fetch_test_notebooks.py),
# NOT next to this script — the tooling is checked out separately in CI. COURSE_ROOT
# defaults to CWD for local runs from a course checkout.
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
TEST_FILES_DIR = COURSE_ROOT / "tests" / "test_files"

# grade_fn(work_dir, notebook_name) -> (earned, possible)
GradeFn = Callable[[Path, str], Tuple[float, float]]


def score_results(results_path: Path) -> Tuple[float, float]:
    """Sum (earned, possible) across all scored tests in an otter results.json."""
    data = json.loads(results_path.read_text())
    tests = data.get("tests", [])
    earned = sum(t["score"] for t in tests if "score" in t)
    possible = sum(t["max_score"] for t in tests if "max_score" in t)
    if possible == 0:
        raise RuntimeError(f"no scored tests in results.json; tests: {tests}")
    return earned, possible


def discover_pairs() -> List[str]:
    if not TEST_FILES_DIR.exists():
        return []
    return sorted(d.name for d in TEST_FILES_DIR.iterdir() if d.is_dir())


def run_pair(assignment: str, grade_fn: GradeFn) -> List[str]:
    pair_dir = TEST_FILES_DIR / assignment
    autograder = pair_dir / "autograder.zip"
    nb_name = f"{assignment}.ipynb"
    if not autograder.exists():
        print(f"  [skip] {assignment}: autograder.zip not staged")
        return []

    errors: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for role, expected_ratio in (("student", 0.0), ("solution", 1.0)):
            src = pair_dir / role
            if not (src / nb_name).exists():
                print(f"  [skip] {assignment} {role}: notebook not present")
                continue
            work = tmp_root / role
            shutil.copytree(src, work)
            shutil.copy2(autograder, work / "autograder.zip")
            print(f"  Grading {role} ({assignment})...")
            try:
                earned, possible = grade_fn(work, nb_name)
            except Exception as e:  # noqa: BLE001 - report and continue
                errors.append(f"{assignment} {role}: {e}")
                print(f"    [FAIL] {e}")
                continue
            ratio = earned / possible
            if abs(ratio - expected_ratio) < 1e-6:
                print(f"    [PASS] {earned}/{possible} ({ratio:.1%})")
            else:
                errors.append(
                    f"{assignment} {role}: expected {expected_ratio:.0%}, got {ratio:.1%} ({earned}/{possible})"
                )
                print(f"    [FAIL] expected {expected_ratio:.0%}, got {ratio:.1%}")
    return errors


def run_all(grade_fn: GradeFn) -> int:
    pairs = discover_pairs()
    if not pairs:
        print("No notebook pairs in tests/test_files/ — nothing to grade")
        return 0
    all_errors: List[str] = []
    for assignment in pairs:
        print(f"\n--- {assignment} ---")
        all_errors.extend(run_pair(assignment, grade_fn))
    if all_errors:
        print(f"\n{len(all_errors)} failure(s):")
        for e in all_errors:
            print(f"  ✗ {e}")
        return 1
    print(f"\nAll {len(pairs)} pair(s) graded as expected")
    return 0
