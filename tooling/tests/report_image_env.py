#!/usr/bin/env python3
"""
Emit a Markdown report of the runtime a candidate base-user-image provides.

Run INSIDE the candidate image (so versions are exactly what would execute):

    docker run --rm -v "$PWD:/work" -w /work <image> \
        python tests/report_image_env.py --image-ref <image> >> "$GITHUB_STEP_SUMMARY"

It reports the image ref, the Python version, a set of key data-science packages,
and the installed version of every top-level module imported anywhere under the
notebook roots (so you can see exactly what the notebooks depend on).
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata as im
import json
import sys
from pathlib import Path

# Always show these, in this order, even if not imported by a notebook.
KEY_PACKAGES = ["python", "otter-grader", "datascience", "numpy", "pandas", "matplotlib"]


def _import_to_distributions() -> dict[str, list[str]]:
    try:
        return im.packages_distributions()
    except Exception:
        return {}


def _version_for_module(module: str, pkg_map: dict[str, list[str]]) -> tuple[str, str]:
    """Return (distribution_label, version) for a top-level import name."""
    if module in getattr(sys, "stdlib_module_names", set()):
        return ("(stdlib)", "—")
    dists = pkg_map.get(module)
    if not dists:
        # Some packages expose a different import name; try the name directly.
        try:
            return (module, im.version(module))
        except Exception:
            return ("(not found)", "—")
    labels, versions = [], []
    for d in sorted(set(dists)):
        labels.append(d)
        try:
            versions.append(im.version(d))
        except Exception:
            versions.append("—")
    return (", ".join(labels), ", ".join(versions))


def collect_imports(roots: list[Path]) -> list[str]:
    modules: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for nb_path in sorted(root.rglob("*.ipynb")):
            try:
                nb = json.loads(nb_path.read_text())
            except Exception:
                continue
            for cell in nb.get("cells", []):
                if cell.get("cell_type") != "code":
                    continue
                src = "".join(cell.get("source", []))
                # Drop IPython magics / shell escapes so ast.parse succeeds.
                code = "\n".join(
                    ln for ln in src.splitlines()
                    if not ln.lstrip().startswith(("%", "!"))
                )
                try:
                    tree = ast.parse(code)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            modules.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and node.level == 0:
                            modules.add(node.module.split(".")[0])
    return sorted(modules)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", default="(unspecified)")
    parser.add_argument("--roots", nargs="*", default=["raw_notebooks"],
                        help="Notebook roots to scan for imports")
    args = parser.parse_args()

    pkg_map = _import_to_distributions()
    py = ".".join(str(x) for x in sys.version_info[:3])

    out: list[str] = []
    out.append("## Candidate image runtime\n")
    out.append(f"**Image:** `{args.image_ref}`\n")
    out.append("| Package | Version |")
    out.append("| --- | --- |")
    out.append(f"| python | {py} |")
    for pkg in KEY_PACKAGES:
        if pkg == "python":
            continue
        try:
            out.append(f"| {pkg} | {im.version(pkg)} |")
        except Exception:
            out.append(f"| {pkg} | (not installed) |")

    modules = collect_imports([Path(r) for r in args.roots])
    out.append("\n### Modules imported by notebooks\n")
    out.append("| Import | Distribution | Version |")
    out.append("| --- | --- | --- |")
    for mod in modules:
        label, version = _version_for_module(mod, pkg_map)
        out.append(f"| {mod} | {label} | {version} |")

    print("\n".join(out))


if __name__ == "__main__":
    main()
