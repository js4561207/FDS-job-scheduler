from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


_CHID_RE = re.compile(r"\bCHID\s*=\s*('([^']+)'|\"([^\"]+)\"|([^,\s/]+))", re.IGNORECASE)
_MESH_LINE_RE = re.compile(r"^\s*&MESH\b(?P<body>.*?)/", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_MPI_PROCESS_RE = re.compile(r"\bMPI_PROCESS\s*=\s*(-?\d+)", re.IGNORECASE)
_IJK_RE = re.compile(r"\bIJK\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_T_END_RE = re.compile(r"\bT_END\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class MeshInfo:
    index: int
    mpi_process: int | None = None
    ijk: tuple[int, int, int] | None = None
    cell_count: int | None = None
    raw: str = ""


@dataclass(frozen=True)
class FdsCaseInfo:
    path: Path
    chid: str
    mesh_count: int
    meshes: list[MeshInfo] = field(default_factory=list)
    assigned_mpi_processes: list[int] = field(default_factory=list)
    has_restart_enabled: bool = False
    t_end: float | None = None

    @property
    def suggested_mpi_processes(self) -> int:
        if self.assigned_mpi_processes:
            return max(self.assigned_mpi_processes) + 1
        return max(1, self.mesh_count)

    @property
    def total_cells(self) -> int | None:
        counts = [mesh.cell_count for mesh in self.meshes if mesh.cell_count is not None]
        if len(counts) != len(self.meshes):
            return None
        return sum(counts)

    @property
    def mpi_loads(self) -> dict[int, int]:
        loads: dict[int, int] = {}
        for mesh in self.meshes:
            rank = mesh.mpi_process if mesh.mpi_process is not None else mesh.index - 1
            loads.setdefault(rank, 0)
            if mesh.cell_count is not None:
                loads[rank] += mesh.cell_count
        return dict(sorted(loads.items()))

    @property
    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.mesh_count == 0:
            warnings.append("No MESH lines were found.")
        if self.assigned_mpi_processes:
            expected = list(range(max(self.assigned_mpi_processes) + 1))
            if self.assigned_mpi_processes != expected:
                warnings.append("MPI_PROCESS values should be continuous starting at 0.")
            if self.assigned_mpi_processes and self.assigned_mpi_processes[0] != 0:
                warnings.append("The first MPI_PROCESS should be 0.")
        if any(mesh.cell_count is None for mesh in self.meshes):
            warnings.append("Some MESH lines are missing parseable IJK values.")
        return warnings


def _strip_comments(text: str) -> str:
    cleaned_lines = []
    for line in text.splitlines():
        if "!" in line:
            line = line.split("!", 1)[0]
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def parse_fds_case(path: str | Path) -> FdsCaseInfo:
    case_path = Path(path)
    text = case_path.read_text(encoding="utf-8", errors="ignore")
    clean = _strip_comments(text)

    chid_match = _CHID_RE.search(clean)
    chid = case_path.stem
    if chid_match:
        chid = next(group for group in chid_match.groups()[1:] if group)

    meshes: list[MeshInfo] = []
    for index, match in enumerate(_MESH_LINE_RE.finditer(clean), start=1):
        raw = match.group(0)
        mpi_match = _MPI_PROCESS_RE.search(raw)
        mpi_process = int(mpi_match.group(1)) if mpi_match else None
        ijk_match = _IJK_RE.search(raw)
        ijk = tuple(int(ijk_match.group(i)) for i in range(1, 4)) if ijk_match else None
        cell_count = ijk[0] * ijk[1] * ijk[2] if ijk else None
        meshes.append(MeshInfo(index=index, mpi_process=mpi_process, ijk=ijk, cell_count=cell_count, raw=raw.strip()))

    assigned = sorted({mesh.mpi_process for mesh in meshes if mesh.mpi_process is not None})
    has_restart = bool(re.search(r"\bRESTART\s*=\s*\.?T(?:RUE)?\.?", clean, re.IGNORECASE))
    t_end_match = _T_END_RE.search(clean)
    t_end = float(t_end_match.group(1)) if t_end_match else None
    return FdsCaseInfo(
        path=case_path,
        chid=chid,
        mesh_count=len(meshes),
        meshes=meshes,
        assigned_mpi_processes=[int(value) for value in assigned],
        has_restart_enabled=has_restart,
        t_end=t_end,
    )


def ensure_restart_case(source: str | Path, destination: str | Path | None = None) -> Path:
    source_path = Path(source)
    target_path = Path(destination) if destination else source_path.with_name(f"{source_path.stem}_restart.fds")
    text = source_path.read_text(encoding="utf-8", errors="ignore")

    misc_line = re.search(r"^\s*&MISC\b.*?/", text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if misc_line:
        line = misc_line.group(0)
        if re.search(r"\bRESTART\s*=", line, re.IGNORECASE):
            new_line = re.sub(
                r"\bRESTART\s*=\s*[^,\s/]+",
                "RESTART=T",
                line,
                flags=re.IGNORECASE,
            )
        else:
            new_line = line[:-1].rstrip() + ", RESTART=T /"
        text = text[: misc_line.start()] + new_line + text[misc_line.end() :]
    else:
        text = "&MISC RESTART=T /\n" + text

    target_path.write_text(text, encoding="utf-8")
    return target_path
