#!/usr/bin/env python3
"""
Tier 1 grading gate: run `otter run -a autograder.zip <notebook>` INSIDE base-user-image
for each staged pair and assert student=0 / solution=full.

This is the required PR gate (matches what xDevs does). The heavier otter-srv-stdalone
container check (tier 2) reuses tests/otter_grade_common.py with a docker-exec grade_fn.

Usage:
    python tests/run_otter_grade_tests.py --image <base-user-image:tag>
"""
import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from otter_grade_common import run_all, score_results

DEFAULT_IMAGE = "us-central1-docker.pkg.dev/cal-icor-hubs/user-images/base-user-image:latest"

# A single `otter run` grades one notebook; it should take ~1-2 min. Without a cap a
# cell that hangs headless (an interactive `interact()` widget, or a no-timeout
# requests.get whose egress CI silently drops) blocks otter run forever and stalls the
# whole job for hours. Bound each grade and fail with the notebook name so the culprit
# is obvious (unlike the grader.check tier, nbconvert's per-cell timeout doesn't apply
# here — otter run has no such guard).
GRADE_TIMEOUT_SECONDS = 600


def make_grade_fn(image: str):
    def grade(work_dir: Path, notebook_name: str, exclude=frozenset()):
        container = f"ottergrade-{uuid.uuid4().hex[:10]}"
        cmd = [
            "docker", "run", "--rm", "--name", container, "-u", "root",
            "-v", f"{work_dir.resolve()}:/work", "-w", "/work",
            image,
            "otter", "run", "--no-logo", "-a", "autograder.zip", "-o", "/work", notebook_name,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=GRADE_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            # subprocess.run's kill reaps `docker run`, but the container keeps running;
            # stop it explicitly so it doesn't leak and hold the mounted work dir.
            subprocess.run(["docker", "kill", container], capture_output=True, text=True)
            raise RuntimeError(
                f"otter run timed out after {GRADE_TIMEOUT_SECONDS}s grading {notebook_name} "
                f"— a cell likely hangs headless (an interactive widget such as "
                f"`interact(...)`, or a network call with no timeout whose egress CI drops). "
                f"Tag the offending cell `skip-execution`."
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"otter run exit {result.returncode}\nstderr: {result.stderr[-1500:]}\nstdout: {result.stdout[-500:]}"
            )
        results_path = work_dir / "results.json"
        if not results_path.exists():
            raise RuntimeError(f"results.json not produced; stdout: {result.stdout[-500:]}")
        return score_results(results_path, exclude)

    return grade


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()
    sys.exit(run_all(make_grade_fn(args.image)))


if __name__ == "__main__":
    main()
