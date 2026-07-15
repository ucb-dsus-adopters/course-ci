#!/usr/bin/env python3
"""
Otter Assign runner for materials-fds-private-v2 (single course, path-routed).

Adapted from edx-berkeley/xDevs's runner. This repo has NO otter_service metadata
and NO course-config.json — assignments are identified purely by path:

    raw_notebooks/<type>/<assignment>/<assignment>.ipynb     (type in lab|hw|project)

For each such notebook it runs `otter assign` (inside base-user-image in CI) and
distributes the outputs:

    instructor_notebooks/<type>/<assignment>/   <- solution notebook + data + support files
    student_notebooks/<type>/<assignment>/      <- blank student notebook + data
    autograder_zips/<type>/<assignment>/<assignment>-autograder.zip

Non-otterized content (raw_notebooks/lec, raw_notebooks/reference) is ignored here;
it is copied through by the deploy step, not by otter assign.

Usage:
    python otter_assign_runner.py --all --kernel-name python3
    python otter_assign_runner.py --config /tmp/changed.txt        # CI: only changed notebooks
    python otter_assign_runner.py --notebook lab/lab01
"""

import argparse
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

import nbformat

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Top-level folders under raw_notebooks that contain otter-assign assignments.
# Per-notebook timeout for `otter assign` and per-cell timeout for pre-execution.
# Guards against cells that hang headless (e.g. a map/widget fetching tiles over the
# network) so a single notebook can't block the whole run for ~hours.
# otter assign normally takes ~1-3 min/notebook; a hang is bimodal (fast or forever),
# so 300s comfortably clears the slow-but-legit case while catching a hang quickly.
ASSIGN_TIMEOUT_SECONDS = 300
# Retry a notebook once if (and only if) otter assign times out — an intermittent
# headless hang. Real errors are not retried.
ASSIGN_ATTEMPTS = 2
CELL_TIMEOUT_SECONDS = 300

OTTERIZE_TYPES = ("lab", "hw", "project")
RAW_DIR = "raw_notebooks"
SOLUTION_DIR = "instructor_notebooks"
STUDENT_DIR = "student_notebooks"
AUTOGRADER_ZIPS_DIR = "autograder_zips"
SCRATCH_DIR = "otterized"


def _rmtree_force(path: Path) -> None:
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=_onerror)


class OtterAssignRunner:
    def __init__(
        self,
        kernel_name: str = "python3",
        allow_errors: bool = False,
        wrap_otter_ignore: bool = True,
        clear_outputs: bool = False,
    ):
        self.repo_root = Path.cwd().resolve()
        self.raw_root = self.repo_root / RAW_DIR
        self.kernel_name = kernel_name
        self.allow_errors = allow_errors
        self.wrap_otter_ignore = wrap_otter_ignore
        self.clear_outputs_flag = clear_outputs
        logger.info(f"Repo root: {self.repo_root}")
        logger.info(f"Kernel: {self.kernel_name}")

    # ------------------------------------------------------------------ #
    # Discovery + path routing
    # ------------------------------------------------------------------ #
    def _resolve_meta(self, notebook: Path) -> Tuple[str, str]:
        """Return (type, assignment) from raw_notebooks/<type>/<assignment>/<nb>.ipynb."""
        rel = notebook.resolve().relative_to(self.raw_root)
        parts = rel.parts
        if len(parts) < 3 or parts[0] not in OTTERIZE_TYPES:
            raise ValueError(
                f"Not an otterizable assignment path (expected "
                f"raw_notebooks/<{'/'.join(OTTERIZE_TYPES)}>/<assignment>/<nb>.ipynb): {notebook}"
            )
        return parts[0], parts[1]

    def _is_assignment_notebook(self, nb: Path) -> bool:
        """True for raw_notebooks/<type>/<assignment>/<assignment>.ipynb under an otterize type."""
        try:
            ntype, assignment = self._resolve_meta(nb)
        except ValueError:
            return False
        # The assignment notebook is the one whose stem matches its folder.
        return nb.stem == assignment

    def find_notebooks(self, config_file: Optional[str]) -> List[Path]:
        if config_file:
            cfg = Path(config_file)
            if not cfg.exists():
                raise FileNotFoundError(f"Config file not found: {cfg}")
            notebooks = []
            for line in cfg.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = (self.repo_root / line).resolve()
                if not p.exists():
                    logger.warning(f"Listed notebook not found: {p}")
                    continue
                if self._is_assignment_notebook(p):
                    notebooks.append(p)
                else:
                    logger.info(f"Skipping non-assignment path: {line}")
            return sorted(set(notebooks))

        # Scan all otterize types for <assignment>/<assignment>.ipynb
        notebooks = []
        for ntype in OTTERIZE_TYPES:
            base = self.raw_root / ntype
            if not base.is_dir():
                continue
            for folder in sorted(p for p in base.iterdir() if p.is_dir()):
                nb = folder / f"{folder.name}.ipynb"
                if nb.exists():
                    notebooks.append(nb.resolve())
                else:
                    # fall back to a lone .ipynb in the folder
                    ipynbs = list(folder.glob("*.ipynb"))
                    if len(ipynbs) == 1:
                        notebooks.append(ipynbs[0].resolve())
        return sorted(set(notebooks))

    def _scratch_out_dir(self, notebook: Path) -> Path:
        ntype, assignment = self._resolve_meta(notebook)
        out = self.repo_root / SCRATCH_DIR / ntype / assignment
        out.mkdir(parents=True, exist_ok=True)
        return out

    # ------------------------------------------------------------------ #
    # Notebook execution + cleanup (verbatim behavior from xDevs)
    # ------------------------------------------------------------------ #
    def execute_notebook(self, notebook: Path) -> Tuple[bool, str]:
        try:
            from nbclient import NotebookClient  # lazy: only needed when executing

            logger.info(f"Executing cells: {notebook.name}")
            nb = nbformat.read(str(notebook), as_version=4)
            # `grader` is injected by otter assign (grader = otter.Notebook(...));
            # it does not exist during this pre-execution pass, so cells that
            # reference it (grader.check, grader.check_all, grader.export, a bare
            # grader) raise NameError. Skip them here by temporarily tagging them
            # with otter_ignore, then strip the temp tag before writing back.
            #
            # Cells the author tagged `skip-execution` (e.g. live-API/LLM calls that
            # can't run headless) must also be skipped here. nbclient would honor
            # `skip-execution` on its own, but we override its skip trait to
            # `otter_ignore` below — so fold those cells into the same temp tag. The
            # author's `skip-execution` tag is left in place so otter assign skips
            # them too; only the temp otter_ignore is stripped afterward.
            temp_tagged = []
            if self.wrap_otter_ignore:
                for cell in nb.cells:
                    if cell.get("cell_type") != "code":
                        continue
                    src = cell.get("source", "")
                    text = "".join(src) if isinstance(src, list) else str(src)
                    existing_tags = cell.get("metadata", {}).get("tags", []) or []
                    if re.search(r"\bgrader\b", text) or "skip-execution" in existing_tags:
                        tags = cell.setdefault("metadata", {}).setdefault("tags", [])
                        if "otter_ignore" not in tags:
                            tags.append("otter_ignore")
                            temp_tagged.append(cell)
            resources = {"metadata": {"path": str(notebook.parent)}}
            kwargs = dict(
                timeout=CELL_TIMEOUT_SECONDS,
                resources=resources,
                allow_errors=self.allow_errors,
                kernel_name=self.kernel_name,
            )
            if self.wrap_otter_ignore:
                kwargs["skip_cells_with_tag"] = "otter_ignore"
            NotebookClient(nb, **kwargs).execute()
            for cell in temp_tagged:
                cell["metadata"]["tags"].remove("otter_ignore")
                if not cell["metadata"]["tags"]:
                    del cell["metadata"]["tags"]
            nbformat.write(nb, str(notebook))
            return True, "Executed"
        except Exception as e:
            logger.error(f"Failed executing {notebook.name}: {e}")
            return False, str(e)

    def clear_execution_metadata(self, notebook: Path) -> None:
        """Null execution counts and strip execution timing metadata from the raw
        notebook (outputs are kept) to minimize git churn — matches xDevs."""
        try:
            nb = nbformat.read(str(notebook), as_version=4)
            for cell in nb.cells:
                if cell.get("cell_type") == "code":
                    cell["execution_count"] = None
                    if self.clear_outputs_flag:
                        cell["outputs"] = []
                for key in ("execution", "ExecuteTime", "execution_start_time", "execution_end_time"):
                    cell.get("metadata", {}).pop(key, None)
            nbformat.write(nb, str(notebook))
        except Exception as e:
            logger.warning(f"Could not clear execution metadata for {notebook.name}: {e}")

    def _clean_generated(self, source_nb: Path, target_nb: Path) -> None:
        """Sync cell ids from source, strip execution metadata, optionally clear outputs."""
        if not target_nb.exists():
            return
        try:
            source = nbformat.read(str(source_nb), as_version=4)
            target = nbformat.read(str(target_nb), as_version=4)
            for s_cell, t_cell in zip(source.cells, target.cells):
                if "id" in s_cell:
                    t_cell["id"] = s_cell["id"]
            for t_cell in target.cells:
                if t_cell.get("cell_type") == "code":
                    t_cell["execution_count"] = None
                    if self.clear_outputs_flag:
                        t_cell["outputs"] = []
                for key in ("execution", "ExecuteTime"):
                    t_cell.get("metadata", {}).pop(key, None)
            nbformat.write(target, str(target_nb))
        except Exception as e:
            logger.warning(f"Could not clean {target_nb.name}: {e}")

    def _patch_otter_init_reference(self, notebook_path: Path, target_basename: str) -> None:
        """Rewrite otter.Notebook("...") so it matches the on-disk filename after rename."""
        try:
            if not notebook_path.exists():
                return
            nb = nbformat.read(str(notebook_path), as_version=4)
            pattern = re.compile(r'otter\.Notebook\(\s*["\'][^"\']+["\']\s*\)')
            replacement = f'otter.Notebook("{target_basename}")'
            changed = False
            for cell in nb.cells:
                if cell.get("cell_type") != "code":
                    continue
                src = cell.get("source", "")
                text = "".join(src) if isinstance(src, list) else str(src)
                if "otter.Notebook(" not in text:
                    continue
                new = pattern.sub(replacement, text)
                if new != text:
                    cell["source"] = new
                    changed = True
                break
            if changed:
                nbformat.write(nb, str(notebook_path))
        except Exception as e:
            logger.warning(f"Could not patch otter.Notebook() in {notebook_path.name}: {e}")

    def _reposition_init_cell(self, notebook_path: Path) -> None:
        """
        Move otter's injected 'Initialize Otter' cell to just after the notebook's
        title so the first cell is the H1 heading. The a11y checker's
        heading-missing-h1 rule is positional (it requires the first cell to carry
        the H1); otherwise every generated notebook fails it. The init cell only
        needs to run before the first grader.check(), so placing it right after the
        title (which precedes all checks) is safe. No-op if not found/already first.
        """
        try:
            if not notebook_path.exists():
                return
            nb = nbformat.read(str(notebook_path), as_version=4)
            cells = nb.cells
            init_idx = None
            for i, c in enumerate(cells):
                if c.get("cell_type") != "code":
                    continue
                text = "".join(c.get("source", "")) if isinstance(c.get("source"), list) else str(c.get("source", ""))
                if "otter.Notebook(" in text or "Initialize Otter" in text:
                    init_idx = i
                    break
            if init_idx is None:
                return
            init = cells.pop(init_idx)
            # Insert right after the first markdown cell bearing an H1 (the title),
            # else after the first markdown cell, else back at the top.
            target = 0
            for i, c in enumerate(cells):
                if c.get("cell_type") == "markdown":
                    md = "".join(c.get("source", "")) if isinstance(c.get("source"), list) else str(c.get("source", ""))
                    target = i + 1
                    if re.search(r'(?m)^#\s', md):
                        break
            cells.insert(target, init)
            nb.cells = cells
            nbformat.write(nb, str(notebook_path))
        except Exception as e:
            logger.warning(f"Could not reposition init cell in {notebook_path.name}: {e}")

    # ------------------------------------------------------------------ #
    # Autograder zip
    # ------------------------------------------------------------------ #
    def copy_autograder_zip(self, notebook: Path, scratch_out: Path) -> None:
        try:
            ntype, assignment = self._resolve_meta(notebook)
            autograder_dir = scratch_out / "autograder"
            if not autograder_dir.exists():
                return
            zips = list(autograder_dir.glob("*.zip"))
            if not zips:
                return
            source_zip = max(zips, key=lambda p: p.stat().st_mtime)
            dest_dir = self.repo_root / AUTOGRADER_ZIPS_DIR / ntype / assignment
            dest_dir.mkdir(parents=True, exist_ok=True)
            # Prune stale zips first, e.g. previously committed timestamped copies like
            # <assignment>-autograder_2026_01_12T....zip, so only the canonical name remains.
            for old in dest_dir.glob("*.zip"):
                old.unlink()
            dest = dest_dir / f"{assignment}-autograder.zip"
            # Copy otter's zip as-is (keeps its real build timestamps); only the
            # filename is canonicalized so tooling can find <assignment>-autograder.zip.
            shutil.copy2(source_zip, dest)
            logger.info(f"Autograder zip -> {dest.relative_to(self.repo_root)}")
        except Exception as e:
            logger.warning(f"Could not copy autograder zip for {notebook.name}: {e}")

    # ------------------------------------------------------------------ #
    # Relocate outputs to instructor_notebooks / student_notebooks
    # ------------------------------------------------------------------ #
    def relocate_outputs(self, notebook: Path, scratch_out: Path) -> None:
        try:
            ntype, assignment = self._resolve_meta(notebook)
            student_src = scratch_out / "student"
            autograder_src = scratch_out / "autograder"
            solution_src = scratch_out / notebook.name

            student_dst = self.repo_root / STUDENT_DIR / ntype / assignment
            solution_dst = self.repo_root / SOLUTION_DIR / ntype / assignment
            student_dst.parent.mkdir(parents=True, exist_ok=True)
            solution_dst.parent.mkdir(parents=True, exist_ok=True)

            # Student bundle (blank notebook + data files)
            if student_src.exists():
                if student_dst.exists():
                    _rmtree_force(student_dst)
                shutil.move(str(student_src), str(student_dst))
                blank = student_dst / notebook.name
                target = student_dst / f"{assignment}.ipynb"
                if blank.exists() and blank != target:
                    if target.exists():
                        target.unlink()
                    blank.rename(target)
                self._patch_otter_init_reference(target, f"{assignment}.ipynb")
                self._reposition_init_cell(target)
                logger.info(f"Student -> {student_dst.relative_to(self.repo_root)}")

            # Solution bundle (solution notebook + autograder support files, minus zips)
            solution_dst.mkdir(parents=True, exist_ok=True)
            if solution_src.exists():
                dest_solution = solution_dst / f"{assignment}.ipynb"
                if dest_solution.exists():
                    dest_solution.unlink()
                shutil.move(str(solution_src), str(dest_solution))
                self._patch_otter_init_reference(dest_solution, f"{assignment}.ipynb")
                self._reposition_init_cell(dest_solution)
            if autograder_src.exists():
                for item in autograder_src.iterdir():
                    if item.suffix == ".zip":
                        continue
                    if item.suffix == ".ipynb" and item.name in {notebook.name, f"{assignment}.ipynb"}:
                        dest_item = solution_dst / f"{assignment}.ipynb"
                    else:
                        dest_item = solution_dst / item.name
                    if dest_item.exists():
                        if dest_item.is_dir():
                            _rmtree_force(dest_item)
                        else:
                            dest_item.unlink()
                    shutil.move(str(item), str(dest_item))
                    if dest_item.name == f"{assignment}.ipynb":
                        self._patch_otter_init_reference(dest_item, f"{assignment}.ipynb")
                        # The autograder copy overwrites the solution written above,
                        # so re-run the init-cell reposition here too; otherwise the
                        # instructor notebook keeps otter's 'Initialize Otter' code cell
                        # at position 0 and fails the a11y heading-missing-h1 rule.
                        self._reposition_init_cell(dest_item)
                logger.info(f"Solution -> {solution_dst.relative_to(self.repo_root)}")

            if scratch_out.exists():
                _rmtree_force(scratch_out)
        except Exception as e:
            logger.warning(f"Could not relocate outputs for {notebook.name}: {e}")

    def _has_questions(self, notebook: Path) -> bool:
        """True if the notebook declares at least one otter question (# BEGIN QUESTION).

        otter assign requires >=1 question (it errors on ./source/tests otherwise), so a
        notebook without any is treated as instructional and published as-is via
        copy_through() instead of being assigned.
        """
        try:
            nb = nbformat.read(str(notebook), as_version=4)
        except Exception:
            return True  # unreadable: take the assign path so the real error surfaces
        for c in nb.cells:
            src = c.get("source", "")
            text = "".join(src) if isinstance(src, list) else str(src)
            if re.search(r"(?m)^#\s*BEGIN QUESTION\b", text):
                return True
        return False

    def copy_through(self, notebook: Path) -> None:
        """Publish an instructional (question-less) notebook as-is.

        Copies the notebook + its sibling data files into student_notebooks/ (outputs
        cleared) and instructor_notebooks/. No autograder is produced, and the notebook
        is NOT executed (so cells that need a network/API/key at runtime don't fail CI).
        """
        ntype, assignment = self._resolve_meta(notebook)
        src_folder = notebook.parent
        for dest_root, clear in ((STUDENT_DIR, True), (SOLUTION_DIR, False)):
            dest = self.repo_root / dest_root / ntype / assignment
            if dest.exists():
                _rmtree_force(dest)
            dest.mkdir(parents=True, exist_ok=True)
            for item in src_folder.iterdir():
                if item.is_dir() or item.name.startswith("."):
                    continue
                shutil.copy2(item, dest / item.name)
            dst_nb = dest / f"{assignment}.ipynb"
            copied = dest / notebook.name
            if copied.exists() and copied != dst_nb:
                if dst_nb.exists():
                    dst_nb.unlink()
                copied.rename(dst_nb)
            if clear:
                nb = nbformat.read(str(dst_nb), as_version=4)
                for c in nb.cells:
                    if c.cell_type == "code":
                        c.outputs = []
                        c.execution_count = None
                nbformat.write(nb, str(dst_nb))
        logger.info(f"Copied through (no otter questions) -> {ntype}/{assignment}")

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def run_one(self, notebook: Path) -> Tuple[Path, bool, str]:
        if not self._has_questions(notebook):
            try:
                self.copy_through(notebook)
                return notebook, True, "copied through (no otter questions)"
            except Exception as e:
                return notebook, False, f"copy-through failed: {e}"
        scratch_out = self._scratch_out_dir(notebook)
        try:
            ok, msg = self.execute_notebook(notebook)
            if not ok:
                return notebook, False, f"execution failed: {msg}"

            cmd = ["otter", "assign", "--no-pdfs", str(notebook), str(scratch_out)]
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            # A timeout (not a normal error) means a cell hung headless — e.g. an
            # interactive widget whose signal-based guard didn't fire. That's
            # intermittent, so retry the timed-out notebook once (only this one;
            # successful notebooks are untouched). A real error is NOT retried.
            result = None
            for attempt in range(1, ASSIGN_ATTEMPTS + 1):
                if attempt > 1 and scratch_out.exists():
                    _rmtree_force(scratch_out)
                    scratch_out.mkdir(parents=True, exist_ok=True)
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, env=env, errors="replace",
                        timeout=ASSIGN_TIMEOUT_SECONDS,
                    )
                    break
                except subprocess.TimeoutExpired:
                    if attempt < ASSIGN_ATTEMPTS:
                        logger.warning(
                            f"{notebook.name}: otter assign timed out after "
                            f"{ASSIGN_TIMEOUT_SECONDS}s (attempt {attempt}/{ASSIGN_ATTEMPTS}); "
                            f"retrying once (likely an intermittent headless widget hang)."
                        )
                        continue
                    return notebook, False, (
                        f"otter assign timed out after {ASSIGN_TIMEOUT_SECONDS}s on "
                        f"{ASSIGN_ATTEMPTS} attempts (a cell likely hangs headless — "
                        f"e.g. a widget/map/network call)"
                    )
            if result.returncode != 0:
                return notebook, False, f"otter assign failed: {result.stderr or result.stdout}"

            self.copy_autograder_zip(notebook, scratch_out)
            # Strip execution counts/timing from the raw notebook (keep outputs) to
            # avoid committing churn each run — matches xDevs.
            self.clear_execution_metadata(notebook)
            for gen in (scratch_out / notebook.name, scratch_out / "student" / notebook.name,
                        scratch_out / "autograder" / notebook.name):
                self._clean_generated(notebook, gen)
            self.relocate_outputs(notebook, scratch_out)
            return notebook, True, "ok"
        except Exception as e:
            return notebook, False, str(e)

    def process(self, notebooks: List[Path], threads: int, keep_going: bool = False) -> int:
        if not notebooks:
            logger.warning("No assignment notebooks to process.")
            return 0
        logger.info(f"Processing {len(notebooks)} notebook(s) with {threads} thread(s)")
        failures = []
        with ThreadPoolExecutor(max_workers=threads) as ex:
            futures = {ex.submit(self.run_one, nb): nb for nb in notebooks}
            for fut in as_completed(futures):
                nb, ok, msg = fut.result()
                rel = nb.relative_to(self.repo_root) if nb.is_absolute() else nb
                if ok:
                    logger.info(f"✓ {rel}")
                else:
                    logger.error(f"✗ {rel}: {msg}")
                    failures.append((rel, msg))
        logger.info("=" * 60)
        logger.info(f"Done: {len(notebooks) - len(failures)}/{len(notebooks)} succeeded")
        # Record failures to a file so a tolerant (--keep-going) bulk run can still
        # commit the notebooks that succeeded while surfacing the ones that didn't.
        fail_file = self.repo_root / ".otter_assign_failures.txt"
        if failures:
            for rel, msg in failures:
                logger.error(f"  ✗ {rel}: {msg}")
            fail_file.write_text(
                "\n".join(f"{rel}: {str(msg).splitlines()[0]}" for rel, msg in failures),
                encoding="utf-8",
            )
        else:
            try:
                fail_file.unlink()
            except FileNotFoundError:
                pass
        if keep_going:
            return 0
        return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="Process all assignment notebooks")
    parser.add_argument("--notebook", help="Process a single notebook by partial path (e.g. lab/lab01)")
    parser.add_argument("--config", help="File listing changed raw notebook paths (relative to repo root)")
    parser.add_argument("--kernel-name", default="python3", help="Jupyter kernel (default: python3)")
    parser.add_argument("--threads", type=int, default=1, help="Concurrent notebooks (default: 1)")
    parser.add_argument("--allow-errors", action="store_true", help="Continue execution past cell errors")
    parser.add_argument("--clear-outputs", action="store_true", help="Clear code outputs in generated notebooks")
    parser.add_argument("--keep-going", action="store_true",
                        help="Process every notebook and exit 0 even if some fail (failures are "
                             "written to .otter_assign_failures.txt); use for bulk regeneration")
    args = parser.parse_args()

    runner = OtterAssignRunner(
        kernel_name=args.kernel_name,
        allow_errors=args.allow_errors,
        clear_outputs=args.clear_outputs,
    )

    if args.notebook:
        q = args.notebook.replace("\\", "/").lower()
        matches = [nb for nb in runner.find_notebooks(None) if q in str(nb).replace("\\", "/").lower()]
        if not matches:
            logger.error(f"No assignment notebook matches: {args.notebook}")
            sys.exit(1)
        if len(matches) > 1:
            logger.warning(f"Multiple matches; using first: {matches[0]}")
        sys.exit(runner.process([matches[0]], threads=1, keep_going=args.keep_going))

    notebooks = runner.find_notebooks(args.config)
    sys.exit(runner.process(notebooks, threads=args.threads, keep_going=args.keep_going))


if __name__ == "__main__":
    main()
