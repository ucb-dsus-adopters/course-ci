#!/usr/bin/env python3
"""
Validate regenerated artifacts and (optionally) sync student_notebooks/ out to the
public materials repo (default: ucb-dsus-adopters/materials-fds-v2) via a PR.

Modes:
  --list-notebooks   print all distributable notebook paths (feeds the a11y action)
  (default)          validate artifacts in place — no writes
  --apply            clone target repo, rsync student_notebooks/<type>/<assignment>,
                     push a branch, open a PR (never merges)

Layout mapping: student_notebooks/<type>/<assignment>/ -> <target>/<type>/<assignment>/
(lectures/reference in the target repo are not touched.)

Usage (COURSE_ROOT defaults to CWD; the deploy workflow always passes --repo):
  python deploy_notebooks.py --list-notebooks
  python deploy_notebooks.py
  python deploy_notebooks.py --apply --repo ucb-dsus-adopters/materials-fds-v2
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

# Course content root. Defaults to CWD; CI sets COURSE_ROOT explicitly because the
# tooling lives in a separate repo (ucb-dsus-adopters/course-ci).
REPO_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
TYPES = ("lab", "hw", "project")
STUDENT_DIR = REPO_ROOT / "student_notebooks"
SOLUTION_DIR = REPO_ROOT / "instructor_notebooks"
ZIPS_DIR = REPO_ROOT / "autograder_zips"
# Always overridden by the caller workflow's deploy_repo input in --apply mode.
DEFAULT_TARGET = "ucb-dsus-adopters/materials-fds-v2"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: List[str], **kw) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def iter_assignments(base: Path):
    for ntype in TYPES:
        d = base / ntype
        if not d.is_dir():
            continue
        for folder in sorted(p for p in d.iterdir() if p.is_dir()):
            yield ntype, folder.name


def list_notebooks() -> None:
    paths = []
    for base in (SOLUTION_DIR, STUDENT_DIR):
        if base.is_dir():
            paths += [str(p.relative_to(REPO_ROOT)) for p in sorted(base.rglob("*.ipynb"))]
    print(" ".join(paths))


def validate() -> None:
    if not STUDENT_DIR.is_dir():
        die("student_notebooks/ not found — run the notebook pipeline first")
    problems = 0
    count = 0
    for ntype, assignment in iter_assignments(STUDENT_DIR):
        count += 1
        student_nb = STUDENT_DIR / ntype / assignment / f"{assignment}.ipynb"
        solution_nb = SOLUTION_DIR / ntype / assignment / f"{assignment}.ipynb"
        zip_path = ZIPS_DIR / ntype / assignment / f"{assignment}-autograder.zip"
        for label, p in (("student", student_nb), ("solution", solution_nb), ("autograder zip", zip_path)):
            if not p.exists():
                print(f"  ✗ {assignment}: missing {label} ({p.relative_to(REPO_ROOT)})")
                problems += 1
    if problems:
        die(f"{problems} validation problem(s) across {count} assignment(s)")
    print(f"OK: {count} assignment(s) validated (student + solution + autograder zip present)")


def git_identity() -> None:
    run(["git", "config", "--global", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "config", "--global", "user.name", "github-actions[bot]"])


def apply(target_repo: str, branch: str, base_branch: str) -> None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        die("GH_TOKEN/GITHUB_TOKEN required for --apply")
    if shutil.which("gh") is None:
        die("gh CLI not found on PATH")
    git_identity()

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "target"
        url = f"https://x-access-token:{token}@github.com/{target_repo}.git"
        run(["git", "clone", "--depth", "1", url, str(dest)])

        # Sync each assignment folder (delete within-assignment stale files, but never
        # touch lectures/reference or unrelated content in the target).
        for ntype, assignment in iter_assignments(STUDENT_DIR):
            src = STUDENT_DIR / ntype / assignment
            dst = dest / ntype / assignment
            dst.parent.mkdir(parents=True, exist_ok=True)
            run(["rsync", "-a", "--delete", f"{src}/", f"{dst}/"])

        status = subprocess.run(
            ["git", "-C", str(dest), "status", "--porcelain"], capture_output=True, text=True, check=True
        )
        if not status.stdout.strip():
            print(f"No changes for {target_repo}; nothing to PR.")
            return

        run(["git", "-C", str(dest), "checkout", "-b", branch])
        run(["git", "-C", str(dest), "add", "-A"])
        run(["git", "-C", str(dest), "commit", "-m", f"materials-fds-v2 deploy: student notebooks ({branch})"])
        run(["git", "-C", str(dest), "push", "-u", "origin", branch],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})

        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        src_repo = os.environ.get("GITHUB_REPOSITORY", "materials-fds-private-v2")
        body = (
            f"Automated sync of `student_notebooks/` from [{src_repo}](https://github.com/{src_repo}).\n\n"
            f"Workflow run: {server}/{src_repo}/actions/runs/{run_id}\n"
        )
        run(["gh", "pr", "create", "--repo", target_repo, "--base", base_branch,
             "--head", branch, "--title", "materials-fds-v2 deploy: student notebooks", "--body", body],
            env={**os.environ, "GH_TOKEN": token})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-notebooks", action="store_true", help="print distributable notebook paths and exit")
    parser.add_argument("--apply", action="store_true", help="sync to target repo and open a PR")
    parser.add_argument("--repo", default=DEFAULT_TARGET, help="target repo owner/name")
    parser.add_argument("--base-branch", default="main", help="base branch in target repo")
    parser.add_argument("--branch", default="", help="PR head branch (default: deploy-<run_id>)")
    args = parser.parse_args()

    if args.list_notebooks:
        list_notebooks()
        return

    validate()
    if not args.apply:
        print("Validate-only mode (no --apply): not syncing.")
        return

    branch = args.branch or f"materials-fds-v2-deploy-{os.environ.get('GITHUB_RUN_ID', 'local')}"
    apply(args.repo, branch, args.base_branch)


if __name__ == "__main__":
    main()
