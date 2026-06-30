#!/usr/bin/env python3
"""
Execute student and solution notebooks inside base-user-image and check grader.check
results for each staged pair in tests/test_files/:

  - Solution notebook: every grader.check() cell must PASS (clean execution)
  - Student notebook:   at least one grader.check() cell must FAIL (solutions stripped)

Usage:
    python tests/run_grader_check_tests.py --image <base-user-image:tag>
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Staged pairs live under the course content tree, not next to this script (tooling is
# checked out separately in CI). COURSE_ROOT defaults to CWD for local runs.
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
TEST_FILES_DIR = COURSE_ROOT / "tests" / "test_files"
DEFAULT_IMAGE = "us-central1-docker.pkg.dev/cal-icor-hubs/user-images/base-user-image:latest"


def identify_failing_cell(image, nb_path, work_dir):
    """Re-run the notebook with --allow-errors so execution doesn't stop at the
    first failure, then report the first cell that errored (index, source, error,
    traceback). If no cell carries an error output, the kernel itself died
    (crash/OOM/timeout) — report that, since it's a different class of problem."""
    diag = work_dir / ("_diag_" + nb_path.name)
    cmd = [
        "docker", "run", "--rm", "-u", "root",
        # No bytecode: otherwise imported helpers (e.g. *_check.py) leave a
        # root-owned __pycache__/ in the bind-mounted work dir that the host
        # runner user can't delete, breaking TemporaryDirectory cleanup.
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{work_dir.resolve()}:/work", "-w", "/work",
        image,
        "jupyter", "nbconvert", "--to", "notebook", "--execute", "--allow-errors",
        "--ExecutePreprocessor.timeout=300",
        "--output", diag.name, nb_path.name,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    if not diag.exists():
        return "DIAGNOSIS: kernel died before producing a notebook (crash/OOM/timeout), not a cell exception."
    try:
        nb = json.loads(diag.read_text())
    finally:
        diag.unlink(missing_ok=True)
    for i, cell in enumerate(nb.get("cells", [])):
        for out in cell.get("outputs", []):
            if out.get("output_type") == "error":
                src = "".join(cell.get("source", []))
                first = next((ln for ln in src.splitlines() if ln.strip()), "")
                tb = "\n".join(out.get("traceback", []))[-1000:]
                return (f"FAILING CELL {i}: {out.get('ename')}: {out.get('evalue')}\n"
                        f"  source: {first[:120]}\n  traceback (tail):\n{tb}")
    return ("DIAGNOSIS: notebook completed under --allow-errors with no cell error — "
            "the hard failure was a kernel-level death (crash/OOM/timeout), not a cell exception.")


def run_notebook_in_docker(image, nb_path, work_dir, raise_on_error=True):
    # Make work_dir writable for the container's non-root user.
    subprocess.run(["chmod", "-R", "a+rwX", str(work_dir)], check=True)

    # Blank source of cells tagged otter_ignore (e.g. ipywidgets demos) so headless
    # nbconvert doesn't hang; scan only looks at grader.check cells anyway.
    nb_data = json.loads(nb_path.read_text())
    for cell in nb_data.get("cells", []):
        if cell.get("cell_type") == "code" and "otter_ignore" in cell.get("metadata", {}).get("tags", []):
            cell["source"] = []
    nb_path.write_text(json.dumps(nb_data))

    cmd = [
        "docker", "run", "--rm", "-u", "root",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{work_dir.resolve()}:/work", "-w", "/work",
        image,
        "jupyter", "nbconvert", "--to", "notebook", "--execute",
        "--ExecutePreprocessor.timeout=300",
        "--output", nb_path.name, nb_path.name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if raise_on_error and result.returncode != 0:
        if result.stderr:
            print("--- nbconvert stderr ---\n" + result.stderr + "\n--- end ---", flush=True)
        # The hard failure above doesn't say WHICH cell broke (and a kernel
        # crash/OOM leaves no traceback at all). Re-run with --allow-errors to
        # capture the executed notebook and pinpoint the failing cell, or confirm
        # it was a kernel-level death rather than a cell exception.
        detail = identify_failing_cell(image, nb_path, work_dir)
        print(detail, flush=True)
        raise RuntimeError(f"{detail} | rc={result.returncode} | {(result.stderr or result.stdout)[-500:]}")
    output_nb = work_dir / nb_path.name
    if not output_nb.exists():
        raise RuntimeError(f"Output notebook not found after execution: {output_nb}")
    return json.loads(output_nb.read_text())


def scan_check_outputs(nb):
    """Classify grader.check() cell outputs as pass/fail.

    Returns (passes, failures, failed_names) where failed_names lists the
    question name from each failing grader.check("<name>") cell (so callers can
    report *which* check failed, not just how many).
    """
    passes, failures, failed_names = 0, 0, []
    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", []))
        if "grader.check(" not in source:
            continue
        m = re.search(r"""grader\.check\(\s*["']([^"']+)["']""", source)
        if m:
            name = m.group(1)
        else:
            # checkpoint cell runs grader.check(test) over a list of names;
            # surface those names instead of an opaque "?".
            listed = re.findall(r"""["'](q[\w]+)["']""", source)
            name = "checkpoint[" + ",".join(listed) + "]" if listed else "?"
        output_text = ""
        for out in cell.get("outputs", []):
            for key in ("text", "text/plain", "text/html"):
                val = out.get("data", {}).get(key) or out.get(key)
                if val:
                    output_text += "".join(val) if isinstance(val, list) else val
        if output_text and ("All test cases passed" in output_text or "passed!" in output_text.lower()):
            passes += 1
        else:
            failures += 1
            failed_names.append(name)
    return passes, failures, failed_names


def run_pair(image, assignment):
    pair_dir = TEST_FILES_DIR / assignment
    nb_name = f"{assignment}.ipynb"
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for role, src_dir, expect_pass in (
            ("student", pair_dir / "student", False),
            ("solution", pair_dir / "solution", True),
        ):
            if not (src_dir / nb_name).exists():
                print(f"  [skip] {assignment}/{role}: notebook not present")
                continue
            role_work = work / role
            shutil.copytree(src_dir, role_work)
            print(f"  Executing {role} ({assignment})...")
            try:
                nb = run_notebook_in_docker(image, role_work / nb_name, role_work, raise_on_error=expect_pass)
            except RuntimeError as e:
                if expect_pass:
                    errors.append(f"{assignment} {role}: execution error — {str(e)[:300]}")
                    print("    [FAIL] execution error")
                else:
                    print(f"    [warn] student raised (may be OK): {str(e)[:200]}")
                continue
            passes, failures, failed_names = scan_check_outputs(nb)
            if expect_pass:
                if failures > 0:
                    named = ", ".join(failed_names)
                    errors.append(f"{assignment} {role}: {failures} check(s) failed [{named}]")
                    print(f"    [FAIL] {failures} failed, {passes} passed -> {named}")
                elif passes == 0:
                    errors.append(f"{assignment} {role}: no grader.check output found")
                    print("    [FAIL] no grader.check output found")
                else:
                    print(f"    [PASS] all {passes} check(s) passed")
            else:
                if failures == 0:
                    errors.append(f"{assignment} {role}: all {passes} passed — solutions may not be stripped")
                    print(f"    [FAIL] all {passes} passed (expected failures)")
                else:
                    print(f"    [PASS] {failures} check(s) failed as expected")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()

    pairs = sorted(d.name for d in TEST_FILES_DIR.iterdir() if d.is_dir()) if TEST_FILES_DIR.exists() else []
    if not pairs:
        print("No notebook pairs found in tests/test_files/ — nothing to test")
        sys.exit(0)

    all_errors = []
    for assignment in pairs:
        print(f"\n--- {assignment} ---")
        all_errors.extend(run_pair(args.image, assignment))

    if all_errors:
        print(f"\n{len(all_errors)} failure(s):")
        for e in all_errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    print(f"\nAll {len(pairs)} notebook pair(s) passed grader.check")


if __name__ == "__main__":
    main()
