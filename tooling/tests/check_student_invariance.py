#!/usr/bin/env python3
"""
Deploy-safety gate for a candidate base-user-image.

After regenerating all notebooks against a candidate image (in the working tree,
uncommitted), compare the freshly generated STUDENT notebooks against a committed
git ref on a NORMALIZED basis (cell type + source only; outputs, execution counts
and timing are ignored).

Rationale: students fork the student notebooks and cannot be updated mid-sequence,
so an image that changes student-facing content is NOT safe to deploy until a
sequence boundary. Autograder zips (graded centrally) are allowed to change and are
not considered here.

Exit code:
    0  no student notebook changed  -> safe to deploy now
    1  one or more changed          -> hold until a sequence boundary

Markdown summary is written to stdout (append to $GITHUB_STEP_SUMMARY).

    python tests/check_student_invariance.py --ref HEAD --root student_notebooks
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def normalize(nb_bytes: bytes) -> list[tuple[str, str]]:
    nb = json.loads(nb_bytes)
    return [
        (cell.get("cell_type", ""), "".join(cell.get("source", [])))
        for cell in nb.get("cells", [])
    ]


def committed_version(ref: str, path: str) -> bytes | None:
    res = subprocess.run(
        ["git", "show", f"{ref}:{path}"], capture_output=True
    )
    return res.stdout if res.returncode == 0 else None


def diff_cells(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> str:
    if len(old) != len(new):
        return f"cell count {len(old)} → {len(new)}"
    changed = [i for i, (o, n) in enumerate(zip(old, new)) if o != n]
    return f"cells changed: {', '.join(map(str, changed))}" if changed else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Git ref of the committed baseline")
    parser.add_argument("--root", default="student_notebooks", help="Student notebooks root")
    args = parser.parse_args()

    root = Path(args.root)
    notebooks = sorted(root.rglob("*.ipynb"))

    changed: list[tuple[str, str]] = []
    added: list[str] = []
    for nb_path in notebooks:
        rel = nb_path.as_posix()
        old_bytes = committed_version(args.ref, rel)
        if old_bytes is None:
            added.append(rel)
            continue
        try:
            old = normalize(old_bytes)
            new = normalize(nb_path.read_bytes())
        except Exception as e:  # noqa: BLE001
            changed.append((rel, f"could not compare: {e}"))
            continue
        detail = diff_cells(old, new)
        if detail:
            changed.append((rel, detail))

    lines: list[str] = ["\n## Student-notebook invariance\n"]
    lines.append(f"Compared {len(notebooks)} student notebook(s) against `{args.ref}` "
                 f"(source + structure only).\n")

    if not changed and not added:
        lines.append("✅ **No student notebooks changed — safe to deploy now.** "
                     "(Autograder-zip changes, if any, are expected and not blocking.)")
        print("\n".join(lines))
        return

    lines.append("❌ **Student notebooks would change — NOT safe to deploy mid-sequence.** "
                 "Hold until a sequence boundary, or adjust so student content is unchanged.\n")
    if changed:
        lines.append("| Notebook | What changed |")
        lines.append("| --- | --- |")
        for rel, detail in changed:
            lines.append(f"| `{rel}` | {detail} |")
    if added:
        lines.append("\n**New student notebooks (no baseline):**")
        for rel in added:
            lines.append(f"- `{rel}`")
    print("\n".join(lines))
    sys.exit(1)


if __name__ == "__main__":
    main()
