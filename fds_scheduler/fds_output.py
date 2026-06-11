from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


ERROR_PATTERNS = (
    re.compile(r"\bERROR\((?P<code>\d+)\):\s*(?P<message>.+)", re.IGNORECASE),
    re.compile(r"\bforrtl:\s*(?P<message>.+)", re.IGNORECASE),
    re.compile(r"\bsevere\s*\(\s*(?P<code>\d+)\s*\):\s*(?P<message>.+)", re.IGNORECASE),
    re.compile(r"\bFatal error\b[:\s]*(?P<message>.+)", re.IGNORECASE),
)

SUCCESS_PATTERNS = (
    re.compile(r"\bSTOP:\s*Set-up only", re.IGNORECASE),
    re.compile(r"\bSTOP:\s*No reactions", re.IGNORECASE),
    re.compile(r"\bSTOP:\s*FDS completed successfully", re.IGNORECASE),
    re.compile(r"\bFDS completed successfully", re.IGNORECASE),
)

STOP_PATTERNS = (
    re.compile(r"\bSTOP:\s*Stopped by user", re.IGNORECASE),
    re.compile(r"\bSTOP:\s*User stop", re.IGNORECASE),
    re.compile(r"\.stop\b", re.IGNORECASE),
    re.compile(r"\bstop file\b", re.IGNORECASE),
    re.compile(r"\brestart file", re.IGNORECASE),
)

MPI_STARTED_RE = re.compile(r"MPI Process\s+(\d+)\s+started", re.IGNORECASE)
TIME_STEP_RE = re.compile(r"\bTime Step\b", re.IGNORECASE)
SIM_TIME_RE = re.compile(
    r"Time Step:\s*\d+,\s*Simulation Time:\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\s*s",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FdsOutputSummary:
    chid: str
    working_dir: Path
    out_file: Path | None = None
    err_file: Path | None = None
    restart_files: list[Path] = field(default_factory=list)
    cpu_file: Path | None = None
    success_detected: bool = False
    stop_detected: bool = False
    error_detected: bool = False
    error_code: str = ""
    error_message: str = ""
    mpi_processes_started: int = 0
    time_stepping_started: bool = False
    latest_simulation_time: float | None = None
    tail: str = ""

    @property
    def restart_available(self) -> bool:
        return bool(self.restart_files)

    @property
    def status_note(self) -> str:
        if self.error_detected:
            if self.error_code:
                return f"FDS error {self.error_code}: {self.error_message}"
            return self.error_message or "FDS error detected"
        if self.success_detected:
            return "FDS completed successfully"
        if self.stop_detected and self.restart_available:
            return "Stopped gracefully; restart files available"
        if self.stop_detected:
            return "Stop detected"
        if self.restart_available:
            return "Restart files available"
        return ""


def _read_tail(path: Path, max_chars: int = 20000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_chars:
            handle.seek(-max_chars, 2)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def summarize_fds_output(chid: str, working_dir: str | Path, scheduler_log: str | Path | None = None) -> FdsOutputSummary:
    work = Path(working_dir)
    out_file = work / f"{chid}.out"
    err_file = work / f"{chid}.err"
    cpu_file = work / f"{chid}_cpu.csv"
    restart_files = sorted(work.glob(f"{chid}*.restart"))

    chunks: list[str] = []
    for path in (out_file, err_file):
        text = _read_tail(path)
        if text:
            chunks.append(text)
    if scheduler_log:
        text = _read_tail(Path(scheduler_log), max_chars=8000)
        if text:
            chunks.append(text)

    combined = "\n".join(chunks)
    tail = combined[-4000:]

    error_code = ""
    error_message = ""
    for pattern in ERROR_PATTERNS:
        matches = list(pattern.finditer(combined))
        if matches:
            match = matches[-1]
            error_code = match.groupdict().get("code") or ""
            error_message = (match.groupdict().get("message") or match.group(0)).strip()
            break

    mpi_ranks = {int(value) for value in MPI_STARTED_RE.findall(combined)}
    sim_time_matches = SIM_TIME_RE.findall(combined)
    latest_simulation_time = float(sim_time_matches[-1]) if sim_time_matches else None

    return FdsOutputSummary(
        chid=chid,
        working_dir=work,
        out_file=out_file if out_file.exists() else None,
        err_file=err_file if err_file.exists() else None,
        restart_files=restart_files,
        cpu_file=cpu_file if cpu_file.exists() else None,
        success_detected=any(pattern.search(combined) for pattern in SUCCESS_PATTERNS),
        stop_detected=any(pattern.search(combined) for pattern in STOP_PATTERNS),
        error_detected=bool(error_message),
        error_code=error_code,
        error_message=error_message,
        mpi_processes_started=len(mpi_ranks),
        time_stepping_started=bool(TIME_STEP_RE.search(combined)),
        latest_simulation_time=latest_simulation_time,
        tail=tail,
    )
