from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fds_scheduler.fds_output import summarize_fds_output
from fds_scheduler.job import FdsScheduler, JobConfig, JobStatus


TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELLED}


def wait_for_status(scheduler: FdsScheduler, job_id: str, timeout: float) -> JobStatus:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = scheduler.jobs[job_id].status
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(0.25)
    return scheduler.jobs[job_id].status


def main() -> int:
    root = Path("scheduler_state_stop_restart")
    out = root / "run"
    if root.exists():
        shutil.rmtree(root)
    out.mkdir(parents=True, exist_ok=True)

    scheduler = FdsScheduler(state_dir=root / "state", max_parallel_jobs=1)
    scheduler.start()
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/restart_fds_case.fds"),
            mpi_processes=1,
            openmp_threads=1,
            solver="fds",
            output_mode="named_dir",
            output_dir=out,
        )
    )

    while scheduler.jobs[record.id].status == JobStatus.QUEUED:
        time.sleep(0.1)
    deadline = time.time() + 60
    while time.time() < deadline:
        summary = summarize_fds_output("restart_fds_case", out, scheduler.jobs[record.id].log_path)
        if summary.time_stepping_started:
            break
        time.sleep(0.25)
    scheduler.stop_job(record.id)
    stopped_status = wait_for_status(scheduler, record.id, 90)
    stopped = scheduler.jobs[record.id]
    stopped_summary = summarize_fds_output("restart_fds_case", out, stopped.log_path)

    print(f"stopped_status={stopped_status.value}")
    print(f"stopped_note={stopped.fds_note}")
    print(f"restart_files={[p.name for p in stopped_summary.restart_files]}")
    print(f"stop_file_exists={(out / 'restart_fds_case.stop').exists()}")

    if stopped_status not in {JobStatus.STOPPED, JobStatus.SUCCEEDED}:
        print(stopped_summary.tail)
        return 1
    if not stopped_summary.restart_available:
        print(stopped_summary.tail)
        return 2

    restart_record = scheduler.create_restart_job(record.id)
    if (out / "restart_fds_case.stop").exists():
        return 3
    restart_case = out / "restart_fds_case_restart.fds"
    if not restart_case.exists() or "RESTART=T" not in restart_case.read_text(encoding="utf-8", errors="ignore"):
        return 4

    restart_status = wait_for_status(scheduler, restart_record.id, 120)
    restarted = scheduler.jobs[restart_record.id]
    restarted_summary = summarize_fds_output("restart_fds_case", out, restarted.log_path)
    print(f"restart_status={restart_status.value}")
    print(f"restart_note={restarted.fds_note}")
    print(f"restart_command={restarted.command}")
    print(f"restart_case={restart_case}")

    if restart_status != JobStatus.SUCCEEDED:
        print(restarted_summary.tail)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
