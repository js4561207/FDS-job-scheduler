from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FDS_ROOT = Path(r"C:\Program Files\firemodels\FDS6")


@dataclass(frozen=True)
class FdsEnvironment:
    root: Path
    bin_dir: Path
    fdsinit: Path
    fds: Path
    fds_openmp: Path
    fds_local: Path
    mpiexec: Path

    @property
    def initialized_env(self) -> dict[str, str]:
        env = os.environ.copy()
        mpi_root = self.bin_dir / "mpi"
        env["I_MPI_ROOT"] = str(mpi_root)
        env["IN_CMDFDS"] = "1"
        env["MPIEXEC_PORT_RANGE"] = ""
        env["MPICH_PORT_RANGE"] = ""
        env["PATH"] = f"{mpi_root};{self.bin_dir};{env.get('PATH', '')}"
        return env

    def cmd_prefix(self) -> str:
        return f'call "{self.fdsinit}"'


def _shortcut_target(command: str) -> Path | None:
    resolved = shutil.which(command)
    if resolved:
        return Path(resolved)
    return None


def detect_fds_environment(preferred_root: str | Path | None = None) -> FdsEnvironment:
    candidates: list[Path] = []
    if preferred_root:
        candidates.append(Path(preferred_root))
    env_root = os.environ.get("FDS_ROOT") or os.environ.get("FDS_HOME")
    if env_root:
        candidates.append(Path(env_root))

    fds_on_path = _shortcut_target("fds")
    if fds_on_path:
        candidates.append(fds_on_path.parent.parent)
    candidates.append(DEFAULT_FDS_ROOT)

    seen: set[Path] = set()
    for root in candidates:
        root = root.expanduser()
        if root in seen:
            continue
        seen.add(root)
        bin_dir = root / "bin"
        env = FdsEnvironment(
            root=root,
            bin_dir=bin_dir,
            fdsinit=bin_dir / "fdsinit.bat",
            fds=bin_dir / "fds.exe",
            fds_openmp=bin_dir / "fds_openmp.exe",
            fds_local=bin_dir / "fds_local.bat",
            mpiexec=bin_dir / "mpi" / "mpiexec.exe",
        )
        if all(path.exists() for path in (env.fdsinit, env.fds, env.fds_local, env.mpiexec)):
            return env

    raise FileNotFoundError(
        "FDS environment was not found. Set FDS_ROOT or install FDS under "
        f"{DEFAULT_FDS_ROOT}."
    )


def run_fds_command(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = detect_fds_environment()
    command = " ".join(args)
    return subprocess.run(
        ["cmd.exe", "/d", "/s", "/c", f'{env.cmd_prefix()} && {command}'],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
