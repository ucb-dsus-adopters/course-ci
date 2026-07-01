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
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Set, Tuple

# Staged pairs live under the course content tree (written by fetch_test_notebooks.py),
# NOT next to this script — the tooling is checked out separately in CI. COURSE_ROOT
# defaults to CWD for local runs from a course checkout.
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
TEST_FILES_DIR = COURSE_ROOT / "tests" / "test_files"

# grade_fn(work_dir, notebook_name, exclude) -> (earned, possible)
GradeFn = Callable[[Path, str, "Set[str]"], Tuple[float, float]]


def manual_question_names(assignment: str) -> Set[str]:
    """Names of manually-graded questions for an assignment, read from its raw notebook.

    `otter grade` cannot auto-score `manual: true` questions — they await human grading
    and score 0 for everyone, including the reference solution. Counting them would make
    the solution fall short of 100%, so both tiers exclude them and check only the
    auto-graded points. Returns an empty set if the raw notebook can't be found/parsed.
    """
    raw = None
    for t in ("lab", "hw", "project"):
        cand = COURSE_ROOT / "raw_notebooks" / t / assignment / f"{assignment}.ipynb"
        if cand.exists():
            raw = cand
            break
    if raw is None:
        return set()
    try:
        nb = json.loads(raw.read_text())
    except Exception:
        return set()
    manual: Set[str] = set()
    for c in nb.get("cells", []):
        src = c.get("source", "")
        text = "".join(src) if isinstance(src, list) else src
        if re.search(r"(?mi)^\s*manual:\s*true\b", text):
            m = re.search(r"(?mi)^\s*name:\s*(\S+)", text)
            if m:
                manual.add(m.group(1).strip().strip("\"'"))
    return manual


def _test_name(t: dict) -> str:
    name = str(t.get("name", "")).strip()
    return name[:-3] if name.endswith(".py") else name


def score_results(results_path: Path, exclude: "Set[str]" = frozenset()) -> Tuple[float, float]:
    """Sum (earned, possible) across scored tests, skipping excluded (manual) questions."""
    data = json.loads(results_path.read_text())
    tests = [t for t in data.get("tests", []) if _test_name(t) not in exclude]
    earned = sum(t["score"] for t in tests if "score" in t)
    possible = sum(t["max_score"] for t in tests if "max_score" in t)
    if possible == 0:
        raise RuntimeError(f"no auto-scored tests in results.json (exclude={sorted(exclude)}); tests: {tests}")
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

    manual = manual_question_names(assignment)
    if manual:
        print(f"  (excluding manual-graded question(s) from score: {', '.join(sorted(manual))})")

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
                earned, possible = grade_fn(work, nb_name, manual)
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
