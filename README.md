# FDS Job Scheduler

FDS Job Scheduler is a small Windows desktop tool for running and managing
local Fire Dynamics Simulator (FDS) jobs.

It is intended for day-to-day simulation work where you have many `.fds` cases
to run, stop, restart, inspect, and compare. Instead of repeatedly opening a
CMDfds window and typing commands by hand, you can queue cases from a simple
interface and keep their logs, status, output folders, CSV curves, and
Smokeview/PyroSim result previews in one place.

## What it can do

- add one or more `.fds` files to a local job queue
- choose MPI process count and OpenMP thread count
- run jobs through the installed FDS environment on Windows
- show progress, latest simulation time, MPI ranks, restart availability, and CPU output status
- stop a running job by creating the FDS `.stop` file
- create restart jobs when restart files are available
- reopen logs and output directories from the job list
- import an already-finished case folder as a historical job
- plot common FDS CSV outputs such as `*_devc.csv`, `*_hrr.csv`, `*_steps.csv`, and `*_cpu.csv`
- open `.smv` or `.smvv` result previews with PyroSim Results when available

## Requirements

- Windows
- Python 3.11 or newer
- FDS 6 installed locally, normally at:

```text
C:\Program Files\firemodels\FDS6
```

PyroSim Results is optional. If installed, the scheduler can use
`PyroSimResults.exe` to open `.smv` and `.smvv` preview files.

## Install and run with Python

Clone the repository:

```powershell
git clone https://github.com/js4561207/FDS-job-scheduler.git
cd FDS-job-scheduler
```

Run the app:

```powershell
python main.py
```

For tests:

```powershell
python -m pytest --basetemp .tmp_pytest tests\test_core.py
```

## Use the packaged Windows build

Download the latest Windows zip from the Releases page:

```text
https://github.com/js4561207/FDS-job-scheduler/releases
```

Unzip it and run:

```text
FDS Scheduler.exe
```

Do not move the executable away from the `_internal` folder in the same
directory; the packaged app needs those files.

## Basic use

1. Click `Browse` and select a `.fds` input file.
2. Check the parsed CHID, mesh count, and suggested MPI setting.
3. Adjust MPI processes, OpenMP threads, solver mode, and output directory if needed.
4. Click `Add Job`.
5. Use the job list to stop, restart, resubmit, open logs, open output folders, plot CSV files, or open SMV previews.

If you select a `.fds` file from a folder that already contains completed FDS
outputs, use `Import Result` to add that finished run to the job list.

## Build from source

PyInstaller is used for the Windows build:

```powershell
.\build_exe.ps1
```

The output is written to:

```text
dist\FDS Scheduler\FDS Scheduler.exe
```

## Notes

This tool is independent and unofficial. It is not part of FDS, Smokeview,
NIST, Thunderhead Engineering, or PyroSim.

Local simulation outputs, PDF manuals, packaged binaries, scheduler state, and
test run folders are intentionally excluded from the repository.

## Acknowledgements

Thanks to the official FDS and Smokeview developers at NIST for the simulator,
visualization tools, documentation, and public research ecosystem that make
this workflow possible.

## Contribution

Contributions are welcome through issues and pull requests.

OpenAI Codex was used as a coding collaborator during development.

## License

MIT License. See [LICENSE](LICENSE).
