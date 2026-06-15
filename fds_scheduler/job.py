from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue
from typing import Callable

from .fds_case import FdsCaseInfo, ensure_restart_case, parse_fds_case
from .fds_env import detect_fds_environment
from .fds_output import FdsOutputSummary, summarize_fds_output


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobConfig:
    case_path: Path
    mpi_processes: int = 1
    openmp_threads: int = 1
    solver: str = "auto"
    force_openmp: bool = False
    oversubscribed: bool = False
    output_mode: str = "case_dir"
    output_dir: Path | None = None
    redirect_console: bool = True
    use_fds_local: bool = True
    restart: bool = False
    restart_from_job_id: str = ""


@dataclass
class JobRecord:
    id: str
    config: JobConfig
    status: JobStatus = JobStatus.QUEUED
    case_info: FdsCaseInfo | None = None
    command: str = ""
    working_dir: Path | None = None
    log_path: Path | None = None
    script_path: Path | None = None
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    error: str = ""
    fds_note: str = ""
    restart_available: bool = False
    mpi_processes_started: int = 0
    latest_simulation_time: float | None = None
    progress_percent: float | None = None
    estimated_remaining_seconds: float | None = None
    cpu_file_available: bool = False

    def to_jsonable(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["config"]["case_path"] = str(self.config.case_path)
        if self.config.output_dir:
            data["config"]["output_dir"] = str(self.config.output_dir)
        if self.working_dir:
            data["working_dir"] = str(self.working_dir)
        if self.log_path:
            data["log_path"] = str(self.log_path)
        if self.script_path:
            data["script_path"] = str(self.script_path)
        if self.case_info:
            data["case_info"]["path"] = str(self.case_info.path)
        return data

    @staticmethod
    def from_jsonable(data: dict) -> "JobRecord":
        config_data = data.get("config", {})
        output_dir = config_data.get("output_dir")
        config = JobConfig(
            case_path=Path(config_data["case_path"]),
            mpi_processes=int(config_data.get("mpi_processes", 1)),
            openmp_threads=int(config_data.get("openmp_threads", 1)),
            solver=config_data.get("solver", "auto"),
            force_openmp=bool(config_data.get("force_openmp", False)),
            oversubscribed=bool(config_data.get("oversubscribed", False)),
            output_mode=config_data.get("output_mode", "case_dir"),
            output_dir=Path(output_dir) if output_dir else None,
            redirect_console=bool(config_data.get("redirect_console", True)),
            use_fds_local=bool(config_data.get("use_fds_local", True)),
            restart=bool(config_data.get("restart", False)),
            restart_from_job_id=config_data.get("restart_from_job_id", ""),
        )
        case_info = None
        case_path = config.case_path
        if case_path.exists():
            try:
                case_info = parse_fds_case(case_path)
            except Exception:
                case_info = None
        return JobRecord(
            id=data.get("id", str(uuid.uuid4())[:8]),
            config=config,
            status=JobStatus(data.get("status", JobStatus.FAILED.value)),
            case_info=case_info,
            command=data.get("command", ""),
            working_dir=Path(data["working_dir"]) if data.get("working_dir") else None,
            log_path=Path(data["log_path"]) if data.get("log_path") else None,
            script_path=Path(data["script_path"]) if data.get("script_path") else None,
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            return_code=data.get("return_code"),
            error=data.get("error", ""),
            fds_note=data.get("fds_note", ""),
            restart_available=bool(data.get("restart_available", False)),
            mpi_processes_started=int(data.get("mpi_processes_started", 0) or 0),
            latest_simulation_time=data.get("latest_simulation_time"),
            progress_percent=data.get("progress_percent"),
            estimated_remaining_seconds=data.get("estimated_remaining_seconds"),
            cpu_file_available=bool(data.get("cpu_file_available", False)),
        )


JobCallback = Callable[[JobRecord], None]


class FdsScheduler:
    def __init__(self, state_dir: str | Path = "scheduler_state", max_parallel_jobs: int = 1):
        self.state_dir = Path(state_dir).resolve()
        self.logs_dir = self.state_dir / "logs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.max_parallel_jobs = max(1, max_parallel_jobs)
        self.jobs: dict[str, JobRecord] = {}
        self._queue: Queue[str] = Queue()
        self._callbacks: list[JobCallback] = []
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._shutdown = threading.Event()
        self.load_state()

    def start(self, target_workers: int | None = None) -> None:
        if target_workers is not None:
            self.max_parallel_jobs = max(1, target_workers)
        with self._lock:
            while len(self._workers) < self.max_parallel_jobs:
                worker = threading.Thread(target=self._worker_loop, daemon=True)
                worker.start()
                self._workers.append(worker)

    def add_callback(self, callback: JobCallback) -> None:
        self._callbacks.append(callback)

    def submit(self, config: JobConfig) -> JobRecord:
        config.case_path = Path(config.case_path).resolve()
        if not config.case_path.exists():
            raise FileNotFoundError(f"Input file does not exist: {config.case_path}")
        case_info = parse_fds_case(config.case_path)
        if config.mpi_processes < 1:
            config.mpi_processes = case_info.suggested_mpi_processes
        if config.openmp_threads < 1:
            config.openmp_threads = 1
        self._validate_config(config)

        record = JobRecord(id=str(uuid.uuid4())[:8], config=config, case_info=case_info)
        record.working_dir = self._resolve_working_dir(record)
        record.log_path = self.logs_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{record.id}_{case_info.chid}.log"
        record.script_path = self.logs_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{record.id}_{case_info.chid}.cmd"
        self._prepare_case_file(record)
        record.command = self._build_command(record)
        self._write_job_script(record)
        with self._lock:
            self.jobs[record.id] = record
            self._save_state()
            self._queue.put(record.id)
        self._notify(record)
        return record

    def submit_many(self, configs: list[JobConfig]) -> list[JobRecord]:
        records = []
        for config in configs:
            records.append(self.submit(config))
        return records

    def preview_config(self, config: JobConfig) -> JobRecord:
        config.case_path = Path(config.case_path).resolve()
        if not config.case_path.exists():
            raise FileNotFoundError(f"Input file does not exist: {config.case_path}")
        case_info = parse_fds_case(config.case_path)
        if config.mpi_processes < 1:
            config.mpi_processes = case_info.suggested_mpi_processes
        if config.openmp_threads < 1:
            config.openmp_threads = 1
        self._validate_config(config)
        record = JobRecord(id="preview", config=config, case_info=case_info)
        record.working_dir = self._resolve_working_dir(record)
        record.log_path = self.logs_dir / "preview.log"
        record.script_path = self.logs_dir / "preview.cmd"
        record.command = self._build_command(record)
        return record

    def resubmit_job(self, job_id: str) -> JobRecord:
        source = self.jobs[job_id]
        config = JobConfig(
            case_path=source.config.case_path,
            mpi_processes=source.config.mpi_processes,
            openmp_threads=source.config.openmp_threads,
            solver=source.config.solver,
            force_openmp=source.config.force_openmp,
            oversubscribed=source.config.oversubscribed,
            output_mode=source.config.output_mode,
            output_dir=source.config.output_dir,
            redirect_console=source.config.redirect_console,
            use_fds_local=source.config.use_fds_local,
            restart=source.config.restart,
            restart_from_job_id=source.config.restart_from_job_id,
        )
        return self.submit(config)

    def stop_job(self, job_id: str) -> None:
        with self._lock:
            record = self.jobs[job_id]
            if record.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return
            process = self._processes.get(job_id)
            if record.status == JobStatus.RUNNING and process and process.poll() is not None:
                self._finalize_record(record, process.returncode)
                return
            record.status = JobStatus.STOPPING
            self._save_state()
        threading.Thread(target=self._create_stop_file_when_ready, args=(record,), daemon=True).start()
        self._notify(record)

    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            record = self.jobs[job_id]
            if record.status not in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.STOPPING}:
                return
            if record.status == JobStatus.QUEUED:
                record.status = JobStatus.CANCELLED
            process = self._processes.get(job_id)
            self._save_state()
        if process and process.poll() is None:
            record.status = JobStatus.CANCELLED
            process.terminate()
            self._save_state()
        self._notify(record)

    def delete_job(self, job_id: str) -> list[str]:
        with self._lock:
            record = self.jobs[job_id]
            if record.status in {JobStatus.RUNNING, JobStatus.STOPPING}:
                raise ValueError("Stop or cancel the running job before deleting it from history.")
            if job_id in self._processes:
                raise ValueError("Cannot delete a job while its process is still tracked.")
            ids = self._job_descendant_ids(job_id)
            for delete_id in ids:
                child = self.jobs.get(delete_id)
                if child and child.status in {JobStatus.RUNNING, JobStatus.STOPPING}:
                    raise ValueError("Stop or cancel running restart jobs before deleting this history entry.")
            for delete_id in ids:
                self.jobs.pop(delete_id, None)
            self._save_state()
            return ids

    def create_restart_job(self, job_id: str) -> JobRecord:
        source = self.jobs[job_id]
        if not source.case_info:
            raise ValueError("Cannot restart a job without parsed case information.")
        work_dir = source.working_dir or source.config.case_path.parent
        output = self.inspect_output(job_id)
        if not output.restart_available:
            raise FileNotFoundError(
                f"No restart files found for CHID={source.case_info.chid} in {work_dir}"
            )
        restart_case = ensure_restart_case(
            source.config.case_path,
            Path(work_dir) / f"{source.config.case_path.stem}_restart.fds",
        )
        self._remove_stop_file(source)
        config = JobConfig(
            case_path=restart_case,
            mpi_processes=source.config.mpi_processes,
            openmp_threads=source.config.openmp_threads,
            solver=source.config.solver,
            force_openmp=source.config.force_openmp,
            oversubscribed=source.config.oversubscribed,
            output_mode=source.config.output_mode,
            output_dir=source.config.output_dir,
            redirect_console=source.config.redirect_console,
            use_fds_local=source.config.use_fds_local,
            restart=True,
            restart_from_job_id=source.id,
        )
        return self.submit(config)

    def inspect_output(self, job_id: str) -> FdsOutputSummary:
        record = self.jobs[job_id]
        if not record.case_info:
            raise ValueError("Cannot inspect output without parsed case information.")
        work_dir = record.working_dir or record.config.case_path.parent
        return summarize_fds_output(record.case_info.chid, work_dir, record.log_path)

    def shutdown(self) -> None:
        self._shutdown.set()

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            job_id = self._queue.get()
            with self._lock:
                record = self.jobs.get(job_id)
            if not record or record.status != JobStatus.QUEUED:
                self._queue.task_done()
                continue
            try:
                self._run_record(record)
            finally:
                self._queue.task_done()

    def _run_record(self, record: JobRecord) -> None:
        record.status = JobStatus.RUNNING
        record.started_at = time.time()
        self._save_state()
        self._notify(record)

        assert record.working_dir is not None
        assert record.log_path is not None
        record.working_dir.mkdir(parents=True, exist_ok=True)
        self._resolve_output_dir(record).mkdir(parents=True, exist_ok=True)
        env = detect_fds_environment().initialized_env

        with record.log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"Job {record.id}\n")
            log.write(f"Command: {record.command}\n")
            log.write(f"Working directory: {record.working_dir}\n\n")
            log.flush()
            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", str(record.script_path)],
                cwd=str(record.working_dir),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with self._lock:
                self._processes[record.id] = process
            return_code = process.wait()

        with self._lock:
            self._finalize_record(record, return_code)
        self._notify(record)

    def _finalize_record(self, record: JobRecord, return_code: int | None) -> None:
        self._processes.pop(record.id, None)
        record.return_code = return_code
        record.finished_at = time.time()
        output = summarize_fds_output(
            record.case_info.chid if record.case_info else record.config.case_path.stem,
            record.working_dir or record.config.case_path.parent,
            record.log_path,
        )
        record.fds_note = output.status_note
        record.restart_available = output.restart_available
        record.mpi_processes_started = output.mpi_processes_started
        record.latest_simulation_time = output.latest_simulation_time
        record.cpu_file_available = bool(output.cpu_file)
        record.progress_percent = self._calculate_progress(record, output)
        record.estimated_remaining_seconds = self._estimate_remaining_seconds(record)
        if record.status == JobStatus.CANCELLED:
            pass
        elif record.status == JobStatus.STOPPING:
            record.status = JobStatus.STOPPED
        elif output.error_detected:
            record.status = JobStatus.FAILED
            record.error = output.status_note
        elif return_code == 0 or output.success_detected:
            record.status = JobStatus.SUCCEEDED
        else:
            record.status = JobStatus.FAILED
            record.error = f"Process exited with code {return_code}"
        self._save_state()

    def refresh_record_output(self, job_id: str) -> JobRecord:
        record = self.jobs[job_id]
        output = self.inspect_output(job_id)
        record.fds_note = output.status_note
        record.restart_available = output.restart_available
        record.mpi_processes_started = output.mpi_processes_started
        record.latest_simulation_time = output.latest_simulation_time
        record.cpu_file_available = bool(output.cpu_file)
        record.progress_percent = self._calculate_progress(record, output)
        record.estimated_remaining_seconds = self._estimate_remaining_seconds(record)
        self._save_state()
        self._notify(record)
        return record

    def import_existing_result(self, case_path: str | Path) -> JobRecord | None:
        case_path = Path(case_path).resolve()
        if not case_path.exists():
            raise FileNotFoundError(f"Input file does not exist: {case_path}")
        case_info = parse_fds_case(case_path)
        working_dir = case_path.parent
        if not self._has_existing_output(case_info.chid, working_dir):
            return None

        for record in self.jobs.values():
            if record.config.case_path.resolve() == case_path and (record.working_dir or record.config.case_path.parent).resolve() == working_dir:
                self.refresh_record_output(record.id)
                return record

        output = summarize_fds_output(case_info.chid, working_dir)
        config = JobConfig(
            case_path=case_path,
            mpi_processes=output.mpi_processes_started or case_info.suggested_mpi_processes,
            openmp_threads=1,
            output_mode="case_dir",
        )
        record = JobRecord(
            id=f"import_{str(uuid.uuid4())[:8]}",
            config=config,
            status=self._status_from_existing_output(output),
            case_info=case_info,
            command="Imported existing FDS output",
            working_dir=working_dir,
            log_path=output.err_file or output.out_file,
            started_at=self._oldest_output_time(case_info.chid, working_dir),
            finished_at=self._newest_output_time(case_info.chid, working_dir),
            fds_note=output.status_note or "Imported existing result",
            restart_available=output.restart_available,
            mpi_processes_started=output.mpi_processes_started,
            latest_simulation_time=output.latest_simulation_time,
            progress_percent=self._calculate_progress_for_info(case_info, output),
            estimated_remaining_seconds=0.0 if output.success_detected else None,
            cpu_file_available=bool(output.cpu_file),
        )
        with self._lock:
            self.jobs[record.id] = record
            self._save_state()
        self._notify(record)
        return record

    @staticmethod
    def _calculate_progress(record: JobRecord, output: FdsOutputSummary) -> float | None:
        return FdsScheduler._calculate_progress_for_info(record.case_info, output)

    @staticmethod
    def _calculate_progress_for_info(case_info: FdsCaseInfo | None, output: FdsOutputSummary) -> float | None:
        t_end = case_info.t_end if case_info else None
        if t_end is None or t_end <= 0 or output.latest_simulation_time is None:
            if output.success_detected:
                return 100.0
            return None
        return max(0.0, min(100.0, output.latest_simulation_time / t_end * 100.0))

    @staticmethod
    def _estimate_remaining_seconds(record: JobRecord) -> float | None:
        if record.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELLED}:
            return 0.0
        if not record.started_at or not record.progress_percent or record.progress_percent <= 0:
            return None
        if record.progress_percent >= 100:
            return 0.0
        elapsed = time.time() - record.started_at
        if elapsed <= 0:
            return None
        total_estimated = elapsed / (record.progress_percent / 100.0)
        return max(0.0, total_estimated - elapsed)

    @staticmethod
    def _status_from_existing_output(output: FdsOutputSummary) -> JobStatus:
        if output.error_detected:
            return JobStatus.FAILED
        if output.success_detected:
            return JobStatus.SUCCEEDED
        if output.stop_detected:
            return JobStatus.STOPPED
        return JobStatus.SUCCEEDED

    @staticmethod
    def _has_existing_output(chid: str, working_dir: Path) -> bool:
        patterns = (
            f"{chid}.out",
            f"{chid}.err",
            f"{chid}.smv",
            f"{chid}.smvv",
            f"{chid}_*.csv",
            f"{chid}*.restart",
        )
        return any(any(working_dir.glob(pattern)) for pattern in patterns)

    @staticmethod
    def _oldest_output_time(chid: str, working_dir: Path) -> float | None:
        times = FdsScheduler._output_mtimes(chid, working_dir)
        return min(times) if times else None

    @staticmethod
    def _newest_output_time(chid: str, working_dir: Path) -> float | None:
        times = FdsScheduler._output_mtimes(chid, working_dir)
        return max(times) if times else None

    @staticmethod
    def _output_mtimes(chid: str, working_dir: Path) -> list[float]:
        paths: list[Path] = []
        for pattern in (f"{chid}.out", f"{chid}.err", f"{chid}.smv", f"{chid}.smvv", f"{chid}_*.csv", f"{chid}*.restart"):
            paths.extend(working_dir.glob(pattern))
        return [path.stat().st_mtime for path in paths if path.exists()]

    def _build_command(self, record: JobRecord) -> str:
        config = record.config
        env = detect_fds_environment()
        runtime_case = self._runtime_case_path(record)
        if config.use_fds_local and any(char.isspace() for char in runtime_case.name):
            raise ValueError("fds_local.bat does not support spaces in .fds file names.")
        case_arg = runtime_case.name if config.use_fds_local else f'"{runtime_case.name}"'
        flags: list[str] = []
        if config.oversubscribed:
            flags.append("-O")

        if config.use_fds_local:
            openmp_threads = config.openmp_threads
            force_openmp = config.force_openmp
            if config.solver == "fds":
                openmp_threads = 1
                force_openmp = False
            elif config.solver == "openmp":
                force_openmp = True
            if force_openmp:
                flags.append("-f")
            flags.extend(["-p", str(config.mpi_processes), "-o", str(openmp_threads)])
            command = f'call "{env.fdsinit}" && fds_local {" ".join(flags)} {case_arg}'
        else:
            executable = "fds"
            if config.solver == "openmp" or (config.solver == "auto" and config.openmp_threads > 1) or config.force_openmp:
                executable = "fds_openmp"
            mpi_flags = ["-localonly", "-n", str(config.mpi_processes)]
            if executable == "fds_openmp":
                mpi_flags.extend(["-env", "OMP_NUM_THREADS", str(config.openmp_threads)])
            if config.oversubscribed:
                mpi_flags.extend(["-env", "I_MPI_WAIT_MODE", "1"])
            command = f'call "{env.fdsinit}" && mpiexec {" ".join(mpi_flags)} {executable} {case_arg}'

        if config.redirect_console and record.case_info:
            err_path = self._resolve_output_dir(record) / f"{record.case_info.chid}.err"
            command += f' > "{err_path}" 2>&1'
        return command

    def _validate_config(self, config: JobConfig) -> None:
        if config.mpi_processes < 1:
            raise ValueError("MPI processes must be at least 1.")
        if config.openmp_threads < 1:
            raise ValueError("OpenMP threads must be at least 1.")
        if config.solver not in {"auto", "fds", "openmp"}:
            raise ValueError("Solver must be auto, fds, or openmp.")
        if config.output_mode not in {"case_dir", "named_dir"}:
            raise ValueError("Output mode must be case_dir or named_dir.")
        if config.output_mode == "named_dir" and not config.output_dir:
            raise ValueError("Select an output directory for named output mode.")
        if config.use_fds_local and any(char.isspace() for char in Path(config.case_path).name):
            raise ValueError("fds_local.bat does not support spaces in .fds file names.")

    @staticmethod
    def case_config_warnings(case_info: FdsCaseInfo, config: JobConfig) -> list[str]:
        warnings = list(case_info.warnings)
        if case_info.mesh_count and config.mpi_processes > case_info.mesh_count:
            warnings.append("MPI processes exceed mesh count; FDS may report an MPI/mesh assignment error.")
        if case_info.assigned_mpi_processes and config.mpi_processes < case_info.suggested_mpi_processes:
            warnings.append("MPI processes are fewer than the highest MPI_PROCESS assignment requires.")
        if config.openmp_threads > 1 and config.mpi_processes > 1:
            warnings.append("MPI plus OpenMP can be useful, but FDS guidance usually favors more MPI meshes before many OpenMP threads.")
        return warnings

    def _write_job_script(self, record: JobRecord) -> None:
        if not record.script_path:
            return
        record.script_path.parent.mkdir(parents=True, exist_ok=True)
        record.script_path.write_text(
            "@echo off\n"
            "setlocal\n"
            f"{record.command}\n"
            "exit /b %ERRORLEVEL%\n",
            encoding="utf-8",
        )

    def _resolve_working_dir(self, record: JobRecord) -> Path:
        config = record.config
        if config.output_mode == "named_dir" and config.output_dir:
            return Path(config.output_dir).resolve()
        return config.case_path.parent

    def _resolve_output_dir(self, record: JobRecord) -> Path:
        return self._resolve_working_dir(record)

    def _runtime_case_path(self, record: JobRecord) -> Path:
        return self._resolve_working_dir(record) / record.config.case_path.name

    def _prepare_case_file(self, record: JobRecord) -> None:
        working_dir = self._resolve_working_dir(record)
        working_dir.mkdir(parents=True, exist_ok=True)
        runtime_case = self._runtime_case_path(record)
        if runtime_case.resolve() != record.config.case_path.resolve():
            shutil.copy2(record.config.case_path, runtime_case)

    def _create_stop_file(self, record: JobRecord) -> None:
        if not record.case_info:
            return
        work_dir = record.working_dir or record.config.case_path.parent
        stop_file = work_dir / f"{record.case_info.chid}.stop"
        stop_file.write_text("Requested by FDS Scheduler\n", encoding="utf-8")

    def _create_stop_file_when_ready(self, record: JobRecord, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if record.status != JobStatus.STOPPING:
                    return
            output = summarize_fds_output(
                record.case_info.chid if record.case_info else record.config.case_path.stem,
                record.working_dir or record.config.case_path.parent,
                record.log_path,
            )
            if output.time_stepping_started or output.restart_available:
                break
            time.sleep(0.25)
        with self._lock:
            if record.status != JobStatus.STOPPING:
                return
        self._create_stop_file(record)

    def _remove_stop_file(self, record: JobRecord) -> None:
        if not record.case_info:
            return
        work_dir = record.working_dir or record.config.case_path.parent
        stop_file = work_dir / f"{record.case_info.chid}.stop"
        if stop_file.exists():
            stop_file.unlink()

    def _job_descendant_ids(self, job_id: str) -> list[str]:
        ids = [job_id]
        index = 0
        while index < len(ids):
            parent = ids[index]
            for child_id, record in self.jobs.items():
                if record.config.restart_from_job_id == parent and child_id not in ids:
                    ids.append(child_id)
            index += 1
        return ids

    def _save_state(self) -> None:
        state_path = self.state_dir / "jobs.json"
        state = [record.to_jsonable() for record in self.jobs.values()]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self) -> None:
        state_path = self.state_dir / "jobs.json"
        if not state_path.exists():
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        with self._lock:
            for item in data:
                try:
                    record = JobRecord.from_jsonable(item)
                    if record.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.STOPPING}:
                        record.status = JobStatus.FAILED
                        record.error = "Recovered after application exit; resubmit if needed."
                    self.jobs[record.id] = record
                except Exception:
                    continue

    def _notify(self, record: JobRecord) -> None:
        for callback in self._callbacks:
            try:
                callback(record)
            except Exception:
                pass
