# FDS Run Rules for Scheduler

This project uses the Windows FDS command environment initialized by:

```bat
call "C:\Program Files\firemodels\FDS6\bin\fdsinit.bat"
```

The workspace shortcut `CMDfds.lnk` starts:

```bat
C:\Windows\System32\cmd.exe /k fdsinit
```

The FDS root detected on this machine is:

```text
C:\Program Files\firemodels\FDS6
```

## Verified Commands

After `fdsinit`, these commands resolve correctly:

```text
fds       -> C:\Program Files\firemodels\FDS6\bin\fds.exe
fds_local -> C:\Program Files\firemodels\FDS6\bin\fds_local.bat
mpiexec   -> C:\Program Files\firemodels\FDS6\bin\mpi\mpiexec.exe
```

Use `helpfds` or `fds_local -h` for help. Do not use `fds -h`: FDS treats `-h` as an input file name and reports `Input file -h not found`.

Installed version:

```text
FDS revision: FDS-6.10.1-0-g12efa16-release
MPI library: Intel(R) MPI Library 2021.6 for Windows
```

## Local Run Pattern

For single-machine runs, prefer `fds_local`:

```bat
fds_local -p <mpi_processes> -o <openmp_threads> <case>.fds
```

Pure OpenMP is valid:

```bat
fds_local -f -p 1 -o <openmp_threads> <case>.fds
```

FDS documentation states that OpenMP can run single-mesh or multi-mesh simulations on multiple cores without requiring MPI. MPI is generally the better scaling choice for multiple meshes, but OpenMP-only multi-mesh runs are supported.

Example:

```bat
fds_local -p 4 -o 2 job_name.fds
```

This uses `4 * 2 = 8` logical processors.

Important options from `fds_local -h`:

```text
-c       show generated mpiexec command without running
-p xx    number of MPI processes, default 1
-o yy    OpenMP threads per MPI process, default OMP_NUM_THREADS
-f       force fds_openmp even when OpenMP threads = 1
-O       oversubscribed mode
-v       show FDS version information
-y dir   run case in a specified directory
-Y       run case in directory named after the case
```

`fds_local -c -p 2 -o 4 dummy.fds` generates:

```bat
mpiexec -localonly -n 2 -env OMP_NUM_THREADS 4 fds_openmp dummy.fds
```

If OpenMP threads are 1 and `-f` is not supplied, `fds_local` uses the non-OpenMP executable `fds`.

## Direct MPI Pattern

The local wrapper ultimately runs:

```bat
mpiexec -localonly -n <mpi_processes> -env OMP_NUM_THREADS <threads> fds_openmp <case>.fds
```

For distributed runs, FDS help shows this pattern:

```bat
mpiexec -n <mpi_processes> -hostfile hostfile.txt -wdir <working_directory> -env OMP_NUM_THREADS <threads> fds <case>.fds
```

If OpenMP is used directly, call `fds_openmp` instead of `fds`.

## Scheduler Implications

The scheduler should:

1. Launch commands through `cmd.exe` and call `fdsinit.bat` first.
2. Prefer `fds_local` for local jobs unless distributed host scheduling is required.
3. Set the process working directory to the case directory before launching.
4. Validate that the `.fds` input file exists before queuing.
5. Capture stdout/stderr; FDS also writes detailed diagnostics to `<CHID>.out`.
6. Treat nonzero exit codes and FDS error text as job failures.
7. Support resource parameters: MPI processes, OpenMP threads, force OpenMP, oversubscribed mode, and optional output case directory.
8. Avoid running cases from cloud-synced or network folders for local jobs when possible, because FDS documentation warns this can reduce reliability/performance.
9. Support graceful stop by creating `<CHID>.stop` in the output/working directory after time stepping has started; FDS then writes restart files. Do not leave `<CHID>.stop` present before startup, because FDS reports ERROR(109).
10. For restart jobs, ensure restart files exist, delete `<CHID>.stop`, and use an input file containing `RESTART=T` on the `MISC` line.

## Useful Output Files

FDS writes outputs in the current working directory by default.

Common scheduler-visible files:

```text
<CHID>.out       diagnostic log
<CHID>.err       optional redirected console output
<CHID>.stop      stop signal file created by user/scheduler
<CHID>.restart   restart data generated during graceful stop
<CHID>_cpu.csv   CPU timing by MPI process
```

`RESULTS_DIR` on the FDS `DUMP` line can redirect binary result files.
