#!/usr/bin/env python3
"""
Stage regenerated notebook pairs for the grading tests.

After otter_assign_runner.py has run in CI, this copies the path-routed outputs into
the flat layout the test scripts expect:

    tests/test_files/<assignment>/student/<assignment>.ipynb   (+ data files)
    tests/test_files/<assignment>/solution/<assignment>.ipynb  (+ data files)
    tests/test_files/<assignment>/autograder.zip

Sources in this repo:
    student   <- student_notebooks/<type>/<assignment>/
    solution  <- instructor_notebooks/<type>/<assignment>/
    autograder<- autograder_zips/<type>/<assignment>/<assignment>-autograder.zip

Usage:
    # CI: derive assignments from the changed raw notebook paths
    python tests/fetch_test_notebooks.py --changed-files "raw_notebooks/lab/lab01/lab01.ipynb ..."

    # Or stage everything present
    python tests/fetch_test_notebooks.py --all
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# Course content root. Defaults to CWD; CI sets COURSE_ROOT explicitly because the
# tooling lives in a separate repo (ucb-dsus-adopters/course-ci), checked out apart
# from the course content. Output and source dirs are all resolved against it.
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
REPO_ROOT = COURSE_ROOT
OTTERIZE_TYPES = ("lab", "hw", "project")
OUTPUT_DIR = REPO_ROOT / "tests" / "test_files"


def assignment_from_raw(path_str: str):
    """raw_notebooks/<type>/<assignment>/<nb>.ipynb -> (type, assignment) or None."""
    parts = Path(path_str).parts
    if "raw_notebooks" not in parts:
        return None
    i = parts.index("raw_notebooks")
    rest = parts[i + 1:]
    if len(rest) >= 3 and rest[0] in OTTERIZE_TYPES:
        return rest[0], rest[1]
    return None


def discover_all():
    pairs = []
    for ntype in OTTERIZE_TYPES:
        base = REPO_ROOT / "student_notebooks" / ntype
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            pairs.append((ntype, folder.name))
    return pairs


def copytree_into(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def stage(ntype: str, assignment: str) -> bool:
    student_src = REPO_ROOT / "student_notebooks" / ntype / assignment
    solution_src = REPO_ROOT / "instructor_notebooks" / ntype / assignment
    zip_src = REPO_ROOT / "autograder_zips" / ntype / assignment / f"{assignment}-autograder.zip"

    if not student_src.is_dir():
        print(f"  [miss] student_notebooks/{ntype}/{assignment}")
        return False
    if not solution_src.is_dir():
        print(f"  [miss] instructor_notebooks/{ntype}/{assignment}")
        return False

    out = OUTPUT_DIR / assignment
    copytree_into(student_src, out / "student")
    copytree_into(solution_src, out / "solution")
    if zip_src.exists():
        (out).mkdir(parents=True, exist_ok=True)
        shutil.copy2(zip_src, out / "autograder.zip")
    else:
        print(f"  [warn] no autograder zip at {zip_src.relative_to(REPO_ROOT)}")
    print(f"  staged {assignment} ({ntype})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed-files", default="", help="Space-separated raw notebook paths")
    parser.add_argument("--all", action="store_true", help="Stage every assignment present")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.all:
        pairs = discover_all()
    else:
        seen, pairs = set(), []
        for tok in args.changed_files.split():
            meta = assignment_from_raw(tok)
            if meta and meta not in seen:
                seen.add(meta)
                pairs.append(meta)
        if not pairs:
            print("No otterizable assignments in --changed-files; nothing to stage.")
            return

    staged = sum(1 for ntype, a in pairs if stage(ntype, a))
    print(f"\nStaged {staged}/{len(pairs)} pair(s) -> {OUTPUT_DIR.relative_to(REPO_ROOT)}/")
    if staged == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
