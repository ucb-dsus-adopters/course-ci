#!/usr/bin/env python3
"""
Keep the materials repo's requirement pins in sync with the deployed base-user-image.

Why this exists
---------------
Students run notebooks on `base-user-image` (the hub). Grading, however, happens in a
SEPARATE container built from each `autograder.zip`, whose environment is defined by
`assign_requirements.txt` (otter assign embeds it; see assign_config.yml). If those pins
drift from the image, a notebook can pass on the hub but grade differently in the
autograder. This script makes base-user-image's `environment.yml` the single source of
truth and verifies (or rewrites) the repo's pins to match.

What it checks
--------------
For every package pinned in the target files that ALSO appears in base-user-image's
environment.yml, the pinned version must equal the image's version. Packages not present
in the image (e.g. ipykernel/ipywidgets used only for local/Binder) are reported as
"extras" and never fail the check.

Usage
-----
  # Verify against the deployed image (default: cal-icor/base-user-image main):
  python tests/check_requirements_sync.py

  # Rewrite the pins in-place to match the image:
  python tests/check_requirements_sync.py --fix

  # Point at a different branch/ref or a local file:
  python tests/check_requirements_sync.py --ref my-package-bump-branch
  python tests/check_requirements_sync.py --image-env /path/to/environment.yml
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request
from pathlib import Path

# Course content root. Defaults to CWD; CI sets COURSE_ROOT explicitly because the
# tooling lives in a separate repo (ucb-dsus-adopters/course-ci).
COURSE_ROOT = Path(os.environ["COURSE_ROOT"]).resolve() if os.environ.get("COURSE_ROOT") else Path.cwd()
from typing import Dict, List, Tuple

DEFAULT_REPO = "cal-icor/base-user-image"
DEFAULT_REF = "main"
DEFAULT_ENV_PATH = "environment.yml"
TARGET_FILES = ["assign_requirements.txt", "requirements.txt"]

# A line like "name==1.2.3", "name>=1.2", "nbconvert[webpdf]==7.16.6", or bare "name".
REQ_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*"
    r"(?P<op>==|>=|<=|~=|!=|<|>)?\s*(?P<version>[A-Za-z0-9_.*+!\-]+)?\s*$"
)


def normalize(name: str) -> str:
    """PEP 503-ish normalization so e.g. scikit_learn == scikit-learn."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# Read the image's environment.yml -> {normalized_name: version}
# ---------------------------------------------------------------------------

def load_image_env_text(args: argparse.Namespace) -> str:
    if args.image_env:
        return Path(args.image_env).read_text(encoding="utf-8")
    url = (
        f"https://raw.githubusercontent.com/{args.repo}/{args.ref}/{args.env_path}"
    )
    print(f"Fetching image env: {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (trusted host)
        return resp.read().decode("utf-8")


def parse_image_versions(env_text: str) -> Dict[str, str]:
    """
    Extract name->version from a conda environment.yml (conda deps + the pip: sublist).
    Avoids a hard PyYAML dependency: the file is a simple list, so we scan lines for
    `- name==version` / `- name=version` entries. Wildcard pins (python==3.12.*) and
    unpinned entries are skipped (nothing to compare against).
    """
    versions: Dict[str, str] = {}
    # conda uses single '=' (name=version=build) or '=='; pip uses '=='.
    dep = re.compile(
        r"^\s*-\s+(?P<name>[A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*={1,2}\s*"
        r"(?P<version>[0-9][A-Za-z0-9_.*+!\-]*)"
    )
    for line in env_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = dep.match(line)
        if not m:
            continue
        version = m.group("version")
        if version.endswith(".*") or "*" in version:
            continue  # e.g. python==3.12.* — no exact target
        # conda "name=version=build": keep only the version segment
        version = version.split("=")[0]
        versions[normalize(m.group("name"))] = version
    return versions


# ---------------------------------------------------------------------------
# Read / rewrite the target requirement files
# ---------------------------------------------------------------------------

def parse_req_file(path: Path) -> List[Tuple[int, str, str | None, str | None]]:
    """Return [(line_index, name, op, version)] for each requirement line."""
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = REQ_LINE.match(line)
        if not m:
            continue
        out.append((i, m.group("name"), m.group("op"), m.group("version")))
    return out


def check_file(
    path: Path, image: Dict[str, str], fix: bool
) -> Tuple[List[str], List[str], bool]:
    """Returns (errors, extras, changed)."""
    errors: List[str] = []
    extras: List[str] = []
    if not path.exists():
        return errors, extras, False

    lines = path.read_text(encoding="utf-8").splitlines()
    changed = False

    for idx, name, op, version in parse_req_file(path):
        key = normalize(name)
        if key not in image:
            extras.append(f"{name} (not in base-user-image — local/Binder only)")
            continue
        want = image[key]
        if op == "==" and version == want:
            continue  # already in sync
        if fix:
            lines[idx] = f"{name}=={want}"
            changed = True
            print(f"  fixed {path.name}: {name} -> =={want}")
        else:
            have = f"{op or ''}{version or '(unpinned)'}"
            errors.append(
                f"{path.name}: {name} is {have} but base-user-image pins =={want}"
            )

    if fix and changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return errors, extras, changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name of the image repo")
    parser.add_argument("--ref", default=DEFAULT_REF, help="branch/tag/sha to read")
    parser.add_argument("--env-path", default=DEFAULT_ENV_PATH, help="path to environment.yml in the repo")
    parser.add_argument("--image-env", help="read a local environment.yml instead of fetching")
    parser.add_argument("--fix", action="store_true", help="rewrite pins to match the image")
    parser.add_argument("--files", nargs="*", default=TARGET_FILES, help="requirement files to check")
    args = parser.parse_args()

    image = parse_image_versions(load_image_env_text(args))
    if not image:
        print("ERROR: could not parse any pinned versions from the image env", file=sys.stderr)
        sys.exit(2)
    print(f"Image pins {len(image)} packages (e.g. datascience=={image.get('datascience','?')}).\n")

    all_errors: List[str] = []
    repo_root = COURSE_ROOT
    for fname in args.files:
        path = repo_root / fname
        errors, extras, _ = check_file(path, image, args.fix)
        all_errors.extend(errors)
        if extras:
            print(f"{fname} extras (ignored): " + ", ".join(extras))

    if args.fix:
        print("\nDone. Re-run without --fix to confirm everything is in sync.")
        return

    if all_errors:
        print(f"\n{len(all_errors)} pin(s) out of sync with base-user-image:")
        for e in all_errors:
            print(f"  ✗ {e}")
        print("\nRun `python tests/check_requirements_sync.py --fix` to update them.")
        sys.exit(1)
    print("All pinned packages are in sync with base-user-image. ✓")


if __name__ == "__main__":
    main()
