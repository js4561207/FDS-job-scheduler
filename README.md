# FDS Job Scheduler

Windows desktop scheduler for local Fire Dynamics Simulator (FDS) jobs.

This project provides a lightweight Tkinter-based interface for queuing,
monitoring, resubmitting, stopping, restarting, and inspecting local FDS
calculations on Windows workstations.

## Features

- detects a local FDS 6 installation
- queues single or batch `.fds` jobs
- supports `fds_local` and direct `mpiexec` launch modes
- tracks MPI processes, OpenMP threads, and mesh-to-rank hints
- stores scheduler history and restores it on next launch
- imports completed historical runs from an existing case directory
- supports graceful stop and restart workflows
- opens work directories, logs, Smokeview/PyroSim preview files, and CSV plots
- visualizes FDS CSV outputs such as `*_devc.csv`, `*_hrr.csv`, `*_steps.csv`, and `*_cpu.csv`
- packages into a Windows executable with PyInstaller

## Screenshots and local outputs

This public repository excludes local calculation projects, result files,
packaged executables, scheduler state, and bundled PDF manuals. Those files are
useful for day-to-day engineering work but are not appropriate for a clean
source repository.

## Requirements

- Windows
- Python 3.11+
- a local FDS installation, typically under
  `C:\Program Files\firemodels\FDS6`

PyroSim/Smokeview preview integration is supported when `PyroSimResults.exe`
is installed locally.

## Run from source

```powershell
python main.py
```

Entry point:

- `main.py`

## Build the executable

```powershell
.\build_exe.ps1
```

The packaged executable is written to:

```text
dist\FDS Scheduler\FDS Scheduler.exe
```

## Repository layout

```text
fds_scheduler/         application package
tests/                 unit tests and smoke-test inputs
build_exe.ps1          PyInstaller build script
FDS Scheduler.spec     PyInstaller spec file
FDS_RUN_RULES.md       local execution assumptions and notes
```

## Key behavior

### Scheduler state

The scheduler stores job history under `scheduler_state\jobs.json` and
per-job logs under `scheduler_state\logs\`.

### Existing result import

If you open an `.fds` file whose directory already contains matching result
files such as `.out`, `.err`, `.smv`, `.smvv`, `*_csv`, or `.restart`, the
application can import that finished or stopped run as a historical task.

### CSV visualization

The UI can plot multiple FDS result series from:

- `*_devc.csv`
- `*_hrr.csv`
- `*_steps.csv`
- `*_cpu.csv`

### Smokeview / PyroSim preview

The scheduler can open `.smvv` or `.smv` files associated with a job and will
prefer `PyroSimResults.exe` when available.

## Testing

Run unit tests:

```powershell
python -m pytest --basetemp .tmp_pytest tests\test_core.py
```

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

## Acknowledgements

- Thanks to the official FDS/Smokeview team at NIST for FDS, Smokeview, and
  the surrounding documentation and research ecosystem.
- OpenAI Codex served as an engineering collaborator for parts of the
  implementation and repository preparation.

## Disclaimer

This is an independent utility project. It is not an official FDS, Smokeview,
NIST, or PyroSim product.
