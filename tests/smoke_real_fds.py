from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fds_scheduler.fds_output import summarize_fds_output
from fds_scheduler.job import FdsScheduler, JobConfig, JobStatus


def main() -> int:
    root = Path("scheduler_state_real_smoke")
    out = root / "run"
    if root.exists():
        shutil.rmtree(root)
    out.mkdir(parents=True, exist_ok=True)

    scheduler = FdsScheduler(state_dir=root / "state", max_parallel_jobs=1)
    scheduler.start()
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/smoke_fds_case.fds"),
            mpi_processes=1,
            openmp_threads=1,
            solver="fds",
            output_mode="named_dir",
            output_dir=out,
        )
    )

    deadline = time.time() + 60
    while time.time() < deadline:
        status = scheduler.jobs[record.id].status
        if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELLED}:
            break
        time.sleep(0.25)

    final = scheduler.jobs[record.id]
    summary = summarize_fds_output("smoke_fds_case", out, final.log_path)
    print(f"status={final.status.value}")
    print(f"return_code={final.return_code}")
    print(f"note={final.fds_note}")
    print(f"out_file={summary.out_file}")
    print(f"err_file={summary.err_file}")
    print(f"mpi_processes_started={summary.mpi_processes_started}")
    print(f"command={final.command}")

    if final.status != JobStatus.SUCCEEDED:
        print(summary.tail)
        return 1
    if not summary.out_file:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
