#!/usr/bin/env python3
"""
Tier 2 grading check: grade each staged pair with the containerized `otter grade`
INSIDE a running otter-srv-stdalone container (the real GAR image) — the same path
the service itself uses (otter.grade.main with ext=ipynb, containers, summaries).

We bypass the Tornado/OAuth/Firestore web layer and just confirm the standalone
image's grading stack scores correctly: student == 0, solution == full marks.

Docker-out-of-docker note: `otter grade` (run via `docker exec`) tells the *host*
docker daemon to spawn grading containers that bind-mount the work dir. For those
mounts to resolve, the work dir must exist at the SAME path on the host and inside
the otter-srv-stdalone container. The workflow bind-mounts WORK_ROOT at an identical
path for exactly this reason; this script does all I/O under WORK_ROOT.

Usage:
    python tests/run_standalone_grade_check.py --container otter-srv-stdalone
"""
from __future__ import annotations

import argparse
import ast
import csv
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from otter_grade_common import manual_question_names

# Staged pairs live under the course content tree, not next to this script (tooling is
# checked out separately in CI). COURSE_ROOT defaults to CWD for local runs.
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
TEST_FILES_DIR = COURSE_ROOT / "tests" / "test_files"
# Must match the host:container bind mount in standalone-grade-check.yml.
WORK_ROOT = Path("/tmp/otterwork")

# Cap a single grade so a headless-hanging cell can't stall CI for hours (see
# run_otter_grade_tests.py). A legitimate grade takes ~1-2 min.
GRADE_TIMEOUT_SECONDS = 600


def autograder_total_and_tests(autograder_zip: Path):
    """Return (total_points, [test_names]) by reading the zip's tests/*.py.
    Mirrors otter's default: a test with points None counts as 1.0."""
    total, names = 0.0, []
    with zipfile.ZipFile(autograder_zip) as z:
        for n in z.namelist():
            if n.startswith("tests/") and n.endswith(".py"):
                name = Path(n).stem
                names.append(name)
                src = z.read(n).decode("utf-8", "replace")
                pts = _points_from_test_src(src)
                total += pts
    return total, names


def _points_from_test_src(src: str) -> float:
    """Extract test['points'] from an OK-format test file; None -> 1.0."""
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "test" for t in node.targets
            ):
                d = ast.literal_eval(node.value)
                pts = d.get("points")
                if pts is None:
                    return 1.0
                if isinstance(pts, (int, float)):
                    return float(pts)
                if isinstance(pts, list):
                    return float(sum(pts))
    except Exception:
        pass
    return 1.0


def grade_in_container(container: str, assignment: str, role: str, nb: Path, autograder: Path, exclude=frozenset()):
    """Run `otter grade` in the container; return (earned, possible)."""
    possible, _ = autograder_total_and_tests(autograder)  # fallback total; otter's CSV is authoritative

    work = WORK_ROOT / assignment / role
    subs = work / "submissions"
    out = work / "out"
    if work.exists():
        shutil.rmtree(work)
    subs.mkdir(parents=True)
    out.mkdir(parents=True)  # otter grade requires the output dir to pre-exist
    shutil.copy2(nb, subs / nb.name)
    shutil.copy2(autograder, work / "autograder.zip")

    image_name = f"{assignment}-{role}".lower()
    cmd = [
        "docker", "exec", container,
        "otter", "grade",
        "-n", image_name,
        "-a", str(work / "autograder.zip"),
        "--ext", "ipynb",
        "-o", str(out),
        str(subs),
    ]
    # Bound the grade: a headless-hanging cell (interactive widget, no-timeout network
    # call) would otherwise block otter grade indefinitely and stall CI for hours.
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=GRADE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "exec", container, "pkill", "-f", "otter grade"],
                       capture_output=True, text=True)
        raise RuntimeError(
            f"otter grade timed out after {GRADE_TIMEOUT_SECONDS}s grading {assignment} ({role}) "
            f"— a cell likely hangs headless (an interactive widget such as `interact(...)`, or a "
            f"network call with no timeout whose egress CI drops). Tag the offending cell "
            f"`skip-execution`."
        )
    if res.returncode != 0:
        raise RuntimeError(
            f"otter grade exit {res.returncode}\nstderr: {res.stderr[-1500:]}\nstdout: {res.stdout[-500:]}"
        )

    csv_path = out / "final_grades.csv"
    if not csv_path.exists():
        raise RuntimeError(f"final_grades.csv not produced; stdout: {res.stdout[-500:]}")
    text = csv_path.read_text()
    print(f"    final_grades.csv:\n{text.rstrip()}")
    rows = list(csv.DictReader(text.splitlines()))
    # otter prepends a 'points-per-question' metadata row; the real submission row
    # is the one whose 'file' is the notebook. Use otter's own totals.
    sub = next((r for r in rows if r.get("file", "").endswith(".ipynb")), None)
    if sub is None:
        raise RuntimeError(f"no submission row in final_grades.csv; rows={rows}")
    earned = float(sub["total_points_earned"])
    pts_row = next((r for r in rows if r.get("file") == "points-per-question"), None)
    if pts_row and pts_row.get("total_points_earned"):
        possible = float(pts_row["total_points_earned"])
    # Exclude manual-graded questions: otter grade scores them 0 (they await human
    # grading), so counting them would cap even the solution below 100%. Subtract each
    # manual question's column from both earned and possible.
    for q in exclude:
        col = next((c for c in sub if c == q or c.split()[0:1] == [q] or c.endswith(f"/{q}")), None)
        if col:
            try:
                earned -= float(sub.get(col) or 0)
                if pts_row:
                    possible -= float(pts_row.get(col) or 0)
            except ValueError:
                pass
    return earned, possible


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    assignments = sorted(d.name for d in TEST_FILES_DIR.iterdir() if d.is_dir()) if TEST_FILES_DIR.exists() else []
    if not assignments:
        print("No notebook pairs in tests/test_files/ — nothing to grade")
        return

    errors = []
    for assignment in assignments:
        print(f"\n--- {assignment} ---")
        autograder = TEST_FILES_DIR / assignment / "autograder.zip"
        manual = manual_question_names(assignment)
        if manual:
            print(f"  (excluding manual-graded question(s) from score: {', '.join(sorted(manual))})")
        for role, expected_ratio in (("student", 0.0), ("solution", 1.0)):
            nb = TEST_FILES_DIR / assignment / role / f"{assignment}.ipynb"
            if not nb.exists() or not autograder.exists():
                print(f"  [skip] {assignment} {role}: missing notebook or autograder.zip")
                continue
            print(f"  Grading {role} ({assignment}) with otter grade...")
            try:
                earned, possible = grade_in_container(args.container, assignment, role, nb, autograder, manual)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{assignment} {role}: {e}")
                print(f"    [FAIL] {e}")
                continue
            ratio = earned / possible if possible else 0.0
            if abs(ratio - expected_ratio) < 1e-6:
                print(f"    [PASS] {earned}/{possible} ({ratio:.1%})")
            else:
                errors.append(f"{assignment} {role}: expected {expected_ratio:.0%}, got {ratio:.1%} ({earned}/{possible})")
                print(f"    [FAIL] expected {expected_ratio:.0%}, got {ratio:.1%}")

    if errors:
        print(f"\n{len(errors)} failure(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    print("\nAll pair(s) graded as expected in the standalone image")


if __name__ == "__main__":
    main()
