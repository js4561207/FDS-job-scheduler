from __future__ import annotations

import os
import queue
import csv
import re
import shutil
import subprocess
import time
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree

from . import __version__
from .fds_case import parse_fds_case
from .fds_env import detect_fds_environment
from .job import FdsScheduler, JobConfig, JobRecord, JobStatus


JOB_COLUMNS = (
    "status",
    "case",
    "chid",
    "progress",
    "sim_time",
    "eta",
    "mpi",
    "openmp",
    "ranks",
    "restart",
    "cpu",
    "note",
    "started",
    "finished",
    "log",
)

JOB_HEADINGS = {
    "status": "Status",
    "case": "Case",
    "chid": "CHID",
    "progress": "Progress",
    "sim_time": "Sim Time",
    "eta": "ETA",
    "mpi": "MPI",
    "openmp": "OpenMP",
    "ranks": "Ranks",
    "restart": "Restart",
    "cpu": "CPU",
    "note": "Note",
    "started": "Started",
    "finished": "Finished",
    "log": "Log",
}

JOB_WIDTHS = {
    "status": 98,
    "case": 190,
    "chid": 120,
    "progress": 84,
    "sim_time": 92,
    "eta": 92,
    "mpi": 56,
    "openmp": 78,
    "ranks": 64,
    "restart": 76,
    "cpu": 58,
    "note": 240,
    "started": 148,
    "finished": 148,
    "log": 280,
}

DEFAULT_VISIBLE_COLUMNS = (
    "status",
    "case",
    "chid",
    "progress",
    "sim_time",
    "eta",
    "mpi",
    "openmp",
    "ranks",
    "restart",
    "cpu",
    "note",
    "started",
    "finished",
)


class SchedulerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"FDS Scheduler {__version__}")
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.scheduler = FdsScheduler(max_parallel_jobs=1)
        self.scheduler.add_callback(self._on_scheduler_update)
        self.scheduler.start()
        self.event_queue: queue.Queue[JobRecord] = queue.Queue()
        self.selected_case: Path | None = None
        self.job_rows: dict[str, str] = {}
        self.restart_children: dict[str, list[str]] = {}
        self.csv_plot_window: CsvPlotWindow | None = None
        self.output_windows: dict[str, OutputWindow] = {}

        self._build_variables()
        self._build_ui()
        self._load_environment()
        self._load_existing_jobs()
        self.after(300, self._drain_events)
        self.after(2000, self._auto_refresh_running_jobs)

    def _build_variables(self) -> None:
        self.fds_root_var = tk.StringVar()
        self.case_path_var = tk.StringVar()
        self.chid_var = tk.StringVar(value="-")
        self.mesh_var = tk.StringVar(value="-")
        self.mpi_assigned_var = tk.StringVar(value="-")
        self.restart_var = tk.StringVar(value="-")
        self.mpi_var = tk.IntVar(value=1)
        self.openmp_var = tk.IntVar(value=1)
        self.parallel_var = tk.IntVar(value=1)
        self.solver_var = tk.StringVar(value="auto")
        self.force_openmp_var = tk.BooleanVar(value=False)
        self.oversub_var = tk.BooleanVar(value=False)
        self.output_mode_var = tk.StringVar(value="case_dir")
        self.output_dir_var = tk.StringVar()
        self.redirect_var = tk.BooleanVar(value=True)
        self.use_fds_local_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self.command_preview_var = tk.StringVar(value="")
        self.column_visibility_vars = {
            column: tk.BooleanVar(value=column in DEFAULT_VISIBLE_COLUMNS) for column in JOB_COLUMNS
        }

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="FDS Root").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.fds_root_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Refresh", command=self._load_environment).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="Open CMDfds", command=self._open_cmdfds).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(top, text="Help", command=self._open_help_window).grid(row=0, column=4)

        case = ttk.LabelFrame(self, text="Case", padding=12)
        case.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        case.columnconfigure(1, weight=1)
        case.columnconfigure(5, weight=1)

        ttk.Label(case, text="Input").grid(row=0, column=0, sticky="w")
        ttk.Entry(case, textvariable=self.case_path_var).grid(row=0, column=1, columnspan=5, sticky="ew", padx=8)
        ttk.Button(case, text="Browse", command=self._browse_case).grid(row=0, column=6)

        ttk.Label(case, text="CHID").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(case, textvariable=self.chid_var).grid(row=1, column=1, sticky="w", pady=(10, 0))
        ttk.Label(case, text="Meshes").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Label(case, textvariable=self.mesh_var).grid(row=1, column=3, sticky="w", pady=(10, 0))
        ttk.Label(case, text="MPI Map").grid(row=1, column=4, sticky="w", pady=(10, 0))
        ttk.Label(case, textvariable=self.mpi_assigned_var).grid(row=1, column=5, sticky="w", pady=(10, 0))
        ttk.Label(case, textvariable=self.restart_var).grid(row=1, column=6, sticky="e", pady=(10, 0))

        settings = ttk.Frame(self, padding=(12, 0, 12, 6))
        settings.grid(row=2, column=0, sticky="nsew")
        settings.columnconfigure(0, weight=0)
        settings.columnconfigure(1, weight=1)
        settings.rowconfigure(0, weight=1)

        controls = ttk.LabelFrame(settings, text="Run Settings", padding=12)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 10))

        self._spin_row(controls, "MPI processes", self.mpi_var, 0, 1, 4096)
        self._spin_row(controls, "OpenMP threads", self.openmp_var, 1, 1, 256)
        self._spin_row(controls, "Parallel jobs", self.parallel_var, 2, 1, 64, self._update_parallelism)

        ttk.Label(controls, text="Solver").grid(row=3, column=0, sticky="w", pady=(10, 0))
        solver = ttk.Combobox(
            controls,
            textvariable=self.solver_var,
            values=("auto", "fds", "openmp"),
            state="readonly",
            width=14,
        )
        solver.grid(row=3, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Pure OpenMP", command=self._set_pure_openmp).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        ttk.Checkbutton(controls, text="Use fds_local wrapper", variable=self.use_fds_local_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )
        ttk.Checkbutton(controls, text="Force OpenMP executable", variable=self.force_openmp_var).grid(
            row=6, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(controls, text="Oversubscribed mode", variable=self.oversub_var).grid(
            row=7, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(controls, text="Redirect console to CHID.err", variable=self.redirect_var).grid(
            row=8, column=0, columnspan=2, sticky="w"
        )

        ttk.Label(controls, text="Output").grid(row=9, column=0, sticky="w", pady=(12, 0))
        ttk.Radiobutton(controls, text="Case directory", variable=self.output_mode_var, value="case_dir").grid(
            row=10, column=0, columnspan=2, sticky="w"
        )
        ttk.Radiobutton(controls, text="Named directory", variable=self.output_mode_var, value="named_dir").grid(
            row=11, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(controls, textvariable=self.output_dir_var, width=34).grid(row=12, column=0, columnspan=2, sticky="ew")
        ttk.Button(controls, text="Output Dir", command=self._browse_output_dir).grid(
            row=13, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )

        actions = ttk.Frame(controls)
        actions.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(actions, text="Add Job", command=self._add_job).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="Stop", command=self._stop_selected).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(actions, text="Cancel", command=self._cancel_selected).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Restart", command=self._restart_selected).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Open Log", command=self._open_selected_log).grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Batch Add", command=self._batch_add_jobs).grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Resubmit", command=self._resubmit_selected).grid(row=3, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Open FDS", command=self._open_selected_fds_file).grid(row=3, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Preview", command=self._preview_command).grid(row=4, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Help", command=self._open_help_window).grid(row=4, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Delete Job", command=self._delete_selected_job).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        details = ttk.LabelFrame(controls, text="Mesh / Command", padding=8)
        details.grid(row=15, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        controls.rowconfigure(15, weight=1)
        self.details_text = tk.Text(details, width=34, height=12, wrap="word")
        self.details_text.grid(row=0, column=0, sticky="nsew")
        details.rowconfigure(0, weight=1)
        details.columnconfigure(0, weight=1)

        jobs_frame = ttk.LabelFrame(settings, text="Jobs", padding=8)
        jobs_frame.grid(row=0, column=1, sticky="nsew")
        jobs_frame.rowconfigure(1, weight=1)
        jobs_frame.columnconfigure(0, weight=1)

        job_tools = ttk.LabelFrame(jobs_frame, text="Selected Job Tools", padding=(8, 6))
        job_tools.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        job_tools.columnconfigure((0, 1, 2, 3, 4, 5), weight=1, uniform="job_tools")
        tool_buttons = (
            ("Open Dir", self._open_selected_workdir),
            ("Open FDS", self._open_selected_fds_file),
            ("Output", self._open_selected_output_window),
            ("Open Log", self._open_selected_log),
            ("Refresh Out", self._refresh_selected_output),
            ("Columns", self._open_column_chooser),
            ("Plot CSV", self._plot_selected_csv),
            ("Validation", self._open_selected_validation),
            ("Open SMV", self._open_selected_smv_preview),
            ("Import Result", self._import_selected_existing_result),
            ("Delete Job", self._delete_selected_job),
            ("Help", self._open_help_window),
        )
        for index, (text, command) in enumerate(tool_buttons):
            ttk.Button(job_tools, text=text, command=command).grid(
                row=index // 6,
                column=index % 6,
                sticky="ew",
                padx=(0 if index % 6 == 0 else 4, 0 if index % 6 == 5 else 4),
                pady=(0 if index < 6 else 6, 0),
            )

        self.jobs_tree = ttk.Treeview(jobs_frame, columns=JOB_COLUMNS, show="headings", selectmode="browse")
        for column in JOB_COLUMNS:
            self.jobs_tree.heading(column, text=JOB_HEADINGS[column])
            self.jobs_tree.column(column, width=JOB_WIDTHS[column], minwidth=JOB_WIDTHS[column], anchor="w", stretch=False)
        self._apply_visible_columns()
        self.jobs_tree.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(jobs_frame, orient="vertical", command=self.jobs_tree.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(jobs_frame, orient="horizontal", command=self.jobs_tree.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        self.jobs_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.jobs_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_details())
        self.jobs_tree.bind("<Double-1>", lambda _event: self._open_selected_workdir())

        bottom = ttk.Frame(self, padding=(12, 4, 12, 10))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    def _spin_row(self, parent: ttk.Frame, label: str, variable: tk.IntVar, row: int, low: int, high: int, command=None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 6))
        ttk.Spinbox(parent, from_=low, to=high, textvariable=variable, width=10, command=command).grid(
            row=row, column=1, sticky="ew", pady=(0, 6)
        )

    def _load_environment(self) -> None:
        try:
            env = detect_fds_environment()
            self.fds_root_var.set(str(env.root))
            self.status_var.set("FDS environment ready")
        except Exception as exc:
            self.fds_root_var.set("Not found")
            self.status_var.set(str(exc))

    def _browse_case(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select FDS input",
            filetypes=(("FDS input", "*.fds"), ("All files", "*.*")),
        )
        if filename:
            self.case_path_var.set(filename)
            self._parse_selected_case()
            self._try_import_existing_result(Path(filename), quiet=True)

    def _browse_output_dir(self) -> None:
        directory = filedialog.askdirectory(title="Select output directory")
        if directory:
            self.output_dir_var.set(directory)
            self.output_mode_var.set("named_dir")

    def _parse_selected_case(self) -> None:
        path = Path(self.case_path_var.get())
        try:
            info = parse_fds_case(path)
            self.selected_case = path
            self.chid_var.set(info.chid)
            self.mesh_var.set(str(info.mesh_count))
            self.mpi_var.set(info.suggested_mpi_processes)
            self.mpi_assigned_var.set(
                ",".join(str(v) for v in info.assigned_mpi_processes) if info.assigned_mpi_processes else "auto"
            )
            self.restart_var.set("RESTART=T" if info.has_restart_enabled else "restart off")
            self.status_var.set(f"Loaded {path.name}")
            self._set_details(self._format_case_details(info, self._current_config_for_case(path)))
        except Exception as exc:
            messagebox.showerror("Case parse failed", str(exc))

    def _add_job(self) -> None:
        if self.case_path_var.get() and Path(self.case_path_var.get()).exists():
            if self.selected_case != Path(self.case_path_var.get()):
                self._parse_selected_case()
        if not self.selected_case:
            messagebox.showwarning("No case", "Select a .fds input file first.")
            return

        output_dir = Path(self.output_dir_var.get()).resolve() if self.output_dir_var.get() else None
        config = JobConfig(
            case_path=self.selected_case,
            mpi_processes=int(self.mpi_var.get()),
            openmp_threads=int(self.openmp_var.get()),
            solver=self.solver_var.get(),
            force_openmp=bool(self.force_openmp_var.get()),
            oversubscribed=bool(self.oversub_var.get()),
            output_mode=self.output_mode_var.get(),
            output_dir=output_dir,
            redirect_console=bool(self.redirect_var.get()),
            use_fds_local=bool(self.use_fds_local_var.get()),
        )
        try:
            warnings = self.scheduler.case_config_warnings(parse_fds_case(config.case_path), config)
            if warnings:
                self.status_var.set("Warning: " + warnings[0])
            record = self.scheduler.submit(config)
            self._upsert_job(record)
            self.status_var.set(f"Queued job {record.id}")
        except Exception as exc:
            messagebox.showerror("Submit failed", str(exc))

    def _batch_add_jobs(self) -> None:
        filenames = filedialog.askopenfilenames(
            title="Select FDS inputs",
            filetypes=(("FDS input", "*.fds"), ("All files", "*.*")),
        )
        if not filenames:
            return
        records = []
        for filename in filenames:
            try:
                info = parse_fds_case(filename)
                records.append(
                    self.scheduler.submit(
                        JobConfig(
                            case_path=Path(filename),
                            mpi_processes=info.suggested_mpi_processes,
                            openmp_threads=int(self.openmp_var.get()),
                            solver=self.solver_var.get(),
                            force_openmp=bool(self.force_openmp_var.get()),
                            oversubscribed=bool(self.oversub_var.get()),
                            output_mode="case_dir",
                            redirect_console=bool(self.redirect_var.get()),
                            use_fds_local=bool(self.use_fds_local_var.get()),
                        )
                    )
                )
            except Exception as exc:
                messagebox.showerror("Batch add failed", f"{filename}\n\n{exc}")
        for record in records:
            self._upsert_job(record)
        self.status_var.set(f"Queued {len(records)} job(s)")

    def _selected_job_id(self) -> str | None:
        selection = self.jobs_tree.selection()
        if not selection:
            return None
        return selection[0]

    def _stop_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            self.scheduler.stop_job(job_id)
        except Exception as exc:
            messagebox.showerror("Stop failed", str(exc))

    def _cancel_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            self.scheduler.cancel_job(job_id)
        except Exception as exc:
            messagebox.showerror("Cancel failed", str(exc))

    def _delete_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        chid = record.case_info.chid if record.case_info else record.config.case_path.stem
        if not messagebox.askyesno(
            "Delete job history",
            f"Remove job {job_id} ({chid}) from the scheduler list?\n\nFDS input and output files will not be deleted.",
        ):
            return
        try:
            deleted_ids = self.scheduler.delete_job(job_id)
        except Exception as exc:
            messagebox.showerror("Delete failed", str(exc))
            return
        for delete_id in reversed(deleted_ids):
            if delete_id in self.job_rows:
                self.jobs_tree.delete(delete_id)
                self.job_rows.pop(delete_id, None)
            self.restart_children.pop(delete_id, None)
        for children in self.restart_children.values():
            children[:] = [child for child in children if child not in deleted_ids]
        self._set_details("")
        self.status_var.set(f"Deleted {len(deleted_ids)} job history entr{'y' if len(deleted_ids) == 1 else 'ies'}")

    def _restart_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            record = self.scheduler.create_restart_job(job_id)
            self._upsert_job(record)
            self.jobs_tree.item(job_id, open=True)
        except Exception as exc:
            messagebox.showerror("Restart failed", str(exc))

    def _resubmit_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            record = self.scheduler.resubmit_job(job_id)
            self._upsert_job(record)
            self.status_var.set(f"Resubmitted job {record.id}")
        except Exception as exc:
            messagebox.showerror("Resubmit failed", str(exc))

    def _open_selected_log(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        if not record.log_path or not record.log_path.exists():
            messagebox.showinfo("No log", "Log file is not available yet.")
            return
        os.startfile(record.log_path)

    def _open_selected_workdir(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        workdir = record.working_dir or record.config.case_path.parent
        if workdir.exists():
            os.startfile(workdir)
        else:
            messagebox.showinfo("No directory", f"Working directory does not exist:\n{workdir}")

    def _open_selected_fds_file(self) -> None:
        job_id = self._selected_job_id()
        path = None
        if job_id:
            path = self.scheduler.jobs[job_id].config.case_path
        elif self.case_path_var.get():
            path = Path(self.case_path_var.get())
        if not path or not path.exists():
            messagebox.showinfo("No FDS file", "Select a job or browse to a .fds file first.")
            return
        try:
            opener = open_fds_in_editor(path)
            self.status_var.set(f"Opened {path.name} with {opener}")
        except OSError as exc:
            messagebox.showerror("Open FDS failed", f"{path}\n\n{exc}")

    def _open_selected_output_window(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        window = self.output_windows.get(job_id)
        if window and window.winfo_exists():
            window.refresh()
            window.focus()
            return
        self.output_windows[job_id] = OutputWindow(self, record)

    def _open_column_chooser(self) -> None:
        ColumnChooserWindow(self).focus()

    def _open_help_window(self) -> None:
        HelpWindow(self).focus()

    def _plot_selected_csv(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        workdir = record.working_dir or record.config.case_path.parent
        csv_files = self._csv_files_for_record(record)
        if not csv_files:
            messagebox.showinfo("No CSV", f"No time-series CSV files were found in:\n{workdir}")
            return
        if self.csv_plot_window and self.csv_plot_window.winfo_exists():
            self.csv_plot_window.destroy()
        self.csv_plot_window = CsvPlotWindow(self, csv_files)
        self.csv_plot_window.focus()

    def _open_selected_smv_preview(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        preview = smv_preview_file_for_record(record)
        if not preview:
            workdir = record.working_dir or record.config.case_path.parent
            messagebox.showinfo("No SMV preview", f"No .smvv or .smv preview file was found in:\n{workdir}")
            return
        try:
            opener = open_smv_preview_file(preview)
            self.status_var.set(f"Opened SMV preview with {opener}: {preview.name}")
        except OSError as exc:
            messagebox.showerror("Open SMV failed", f"{preview}\n\n{exc}")

    def _open_selected_validation(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        workdir = record.working_dir or record.config.case_path.parent
        excel_files = sorted(workdir.glob("*.xlsx"))
        csv_files = self._csv_files_for_record(record)
        if not excel_files:
            messagebox.showinfo("No experiment data", f"No .xlsx experiment data was found in:\n{workdir}")
            return
        if not csv_files:
            messagebox.showinfo("No FDS CSV", f"No FDS CSV files were found in:\n{workdir}")
            return
        ValidationWindow(self, excel_files, csv_files).focus()

    def _import_selected_existing_result(self) -> None:
        path_text = self.case_path_var.get()
        if not path_text:
            messagebox.showwarning("No case", "Select a .fds input file first.")
            return
        self._try_import_existing_result(Path(path_text), quiet=False)

    def _try_import_existing_result(self, path: Path, quiet: bool) -> None:
        try:
            record = self.scheduler.import_existing_result(path)
        except Exception as exc:
            if not quiet:
                messagebox.showerror("Import failed", str(exc))
            return
        if not record:
            if not quiet:
                messagebox.showinfo("No result", f"No existing FDS output was found beside:\n{path}")
            return
        self._upsert_job(record)
        self.jobs_tree.selection_set(record.id)
        self.jobs_tree.see(record.id)
        self._show_selected_details()
        self.status_var.set(f"Imported existing result: {record.case_info.chid if record.case_info else path.stem}")

    def _refresh_selected_output(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        try:
            record = self.scheduler.refresh_record_output(job_id)
            self._upsert_job(record)
            self._show_selected_details()
            self.status_var.set(f"Refreshed job {record.id}")
        except Exception as exc:
            messagebox.showerror("Refresh failed", str(exc))

    def _preview_command(self) -> None:
        if self.selected_case:
            try:
                config = JobConfig(
                    case_path=self.selected_case,
                    mpi_processes=int(self.mpi_var.get()),
                    openmp_threads=int(self.openmp_var.get()),
                    solver=self.solver_var.get(),
                    force_openmp=bool(self.force_openmp_var.get()),
                    oversubscribed=bool(self.oversub_var.get()),
                    output_mode=self.output_mode_var.get(),
                    output_dir=Path(self.output_dir_var.get()).resolve() if self.output_dir_var.get() else None,
                    redirect_console=bool(self.redirect_var.get()),
                    use_fds_local=bool(self.use_fds_local_var.get()),
                )
                record = self.scheduler.preview_config(config)
                self._set_details(record.command)
                return
            except Exception as exc:
                messagebox.showerror("Preview failed", str(exc))
                return
        job_id = self._selected_job_id()
        if job_id:
            self._set_details(self.scheduler.jobs[job_id].command)

    def _open_cmdfds(self) -> None:
        shortcut = Path.cwd() / "CMDfds.lnk"
        if shortcut.exists():
            os.startfile(shortcut)
            return
        env = detect_fds_environment()
        os.startfile(env.fdsinit)

    def _update_parallelism(self) -> None:
        self.scheduler.start(max(1, int(self.parallel_var.get())))

    def _set_pure_openmp(self) -> None:
        self.mpi_var.set(1)
        self.solver_var.set("openmp")
        self.force_openmp_var.set(True)
        if self.openmp_var.get() < 2:
            self.openmp_var.set(2)
        self.status_var.set("Pure OpenMP: MPI=1, solver=openmp")

    def _load_existing_jobs(self) -> None:
        for record in self.scheduler.jobs.values():
            self._upsert_job(record)
        if self.scheduler.jobs:
            self.status_var.set(f"Loaded {len(self.scheduler.jobs)} saved job(s)")

    def _on_scheduler_update(self, record: JobRecord) -> None:
        self.event_queue.put(record)

    def _drain_events(self) -> None:
        try:
            while True:
                self._upsert_job(self.event_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(300, self._drain_events)

    def _auto_refresh_running_jobs(self) -> None:
        for record in list(self.scheduler.jobs.values()):
            if record.status in {JobStatus.RUNNING, JobStatus.STOPPING}:
                try:
                    self.scheduler.refresh_record_output(record.id)
                except Exception:
                    pass
        self.after(2000, self._auto_refresh_running_jobs)

    def _upsert_job(self, record: JobRecord) -> None:
        values = (
            record.status.value,
            record.config.case_path.name,
            record.case_info.chid if record.case_info else "",
            self._format_progress(record.progress_percent),
            self._format_float(record.latest_simulation_time),
            self._format_duration(record.estimated_remaining_seconds),
            record.config.mpi_processes,
            record.config.openmp_threads,
            record.mpi_processes_started or "",
            "yes" if record.restart_available else "",
            "yes" if record.cpu_file_available else "",
            self._record_note(record),
            self._format_time(record.started_at),
            self._format_time(record.finished_at),
            str(record.log_path) if record.log_path else "",
        )
        if record.id in self.job_rows:
            self.jobs_tree.item(record.id, values=values)
        else:
            parent = record.config.restart_from_job_id
            if parent and parent in self.scheduler.jobs:
                if parent not in self.job_rows:
                    self._upsert_job(self.scheduler.jobs[parent])
                self.jobs_tree.insert(parent, "end", iid=record.id, values=values)
                self.restart_children.setdefault(parent, []).append(record.id)
            else:
                self.jobs_tree.insert("", "end", iid=record.id, values=values)
            self.job_rows[record.id] = record.id
        if record.status in {JobStatus.FAILED, JobStatus.STOPPED, JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            self.status_var.set(f"Job {record.id}: {record.status.value}")
        output_window = self.output_windows.get(record.id)
        if output_window and output_window.winfo_exists():
            output_window.refresh(record)

    def _show_selected_details(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        record = self.scheduler.jobs[job_id]
        parts = [f"Job: {record.id}", f"Command: {record.command}"]
        if record.config.restart_from_job_id:
            parts.insert(1, f"Restart from: {record.config.restart_from_job_id}")
        if record.working_dir:
            parts.append(f"Working directory: {record.working_dir}")
        parts.append(f"Progress: {self._format_progress(record.progress_percent)}")
        parts.append(f"Simulation time: {self._format_float(record.latest_simulation_time)}")
        parts.append(f"Estimated remaining: {self._format_duration(record.estimated_remaining_seconds)}")
        parts.append(f"CPU file: {'yes' if record.cpu_file_available else 'no'}")
        smv_preview = smv_preview_file_for_record(record)
        parts.append(f"SMV preview: {smv_preview.name if smv_preview else 'no'}")
        csv_files = self._csv_files_for_record(record)
        if csv_files:
            parts.append(f"CSV files: {len(csv_files)}")
        if record.case_info:
            parts.append("")
            parts.append(self._format_case_details(record.case_info, record.config))
        self._set_details("\n".join(parts))

    def _set_details(self, text: str) -> None:
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", text)
        self.details_text.configure(state="disabled")

    def _current_config_for_case(self, path: Path) -> JobConfig:
        return JobConfig(
            case_path=path,
            mpi_processes=int(self.mpi_var.get()),
            openmp_threads=int(self.openmp_var.get()),
            solver=self.solver_var.get(),
            force_openmp=bool(self.force_openmp_var.get()),
            oversubscribed=bool(self.oversub_var.get()),
            output_mode=self.output_mode_var.get(),
            output_dir=Path(self.output_dir_var.get()).resolve() if self.output_dir_var.get() else None,
            redirect_console=bool(self.redirect_var.get()),
            use_fds_local=bool(self.use_fds_local_var.get()),
        )

    def _format_case_details(self, info, config: JobConfig | None = None) -> str:
        lines = [
            f"CHID: {info.chid}",
            f"Meshes: {info.mesh_count}",
            f"Total cells: {info.total_cells if info.total_cells is not None else 'unknown'}",
            f"Suggested MPI processes: {info.suggested_mpi_processes}",
            f"T_END: {info.t_end if info.t_end is not None else 'unknown'}",
            f"Restart flag: {'yes' if info.has_restart_enabled else 'no'}",
            "Mesh MPI map:",
        ]
        for mesh in info.meshes:
            value = mesh.mpi_process if mesh.mpi_process is not None else "auto"
            ijk = mesh.ijk if mesh.ijk is not None else "unknown"
            cells = mesh.cell_count if mesh.cell_count is not None else "unknown"
            lines.append(f"  MESH {mesh.index}: MPI_PROCESS={value}, IJK={ijk}, cells={cells}")
        if info.mpi_loads:
            lines.append("MPI load by rank:")
            for rank, cells in info.mpi_loads.items():
                lines.append(f"  rank {rank}: {cells} cells")
        warnings = self.scheduler.case_config_warnings(info, config) if config else info.warnings
        if warnings:
            lines.append("Warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        return "\n".join(lines)

    @staticmethod
    def _format_time(value: float | None) -> str:
        if value is None:
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))

    @staticmethod
    def _format_progress(value: float | None) -> str:
        if value is None:
            return ""
        return f"{value:.1f}%"

    @staticmethod
    def _format_float(value: float | None) -> str:
        if value is None:
            return ""
        return f"{value:.3g}"

    @staticmethod
    def _format_duration(value: float | None) -> str:
        if value is None:
            return ""
        seconds = max(0, int(round(value)))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d}h {minutes:02d}m"
        if minutes:
            return f"{minutes:d}m {seconds:02d}s"
        return f"{seconds:d}s"

    def _apply_visible_columns(self) -> None:
        visible = [column for column, var in self.column_visibility_vars.items() if var.get()]
        if not visible:
            visible = ["status"]
            self.column_visibility_vars["status"].set(True)
        self.jobs_tree.configure(displaycolumns=visible)

    @staticmethod
    def _record_note(record: JobRecord) -> str:
        note = record.fds_note or record.error
        if record.config.restart and record.config.restart_from_job_id:
            prefix = f"restart of {record.config.restart_from_job_id}"
            return f"{prefix}; {note}" if note else prefix
        return note

    @staticmethod
    def _csv_files_for_record(record: JobRecord) -> list[Path]:
        workdir = record.working_dir or record.config.case_path.parent
        if not workdir.exists():
            return []
        chid = record.case_info.chid if record.case_info else record.config.case_path.stem
        preferred = sorted(workdir.glob(f"{chid}_*.csv"))
        return preferred or sorted(workdir.glob("*.csv"))


def smv_preview_file_for_record(record: JobRecord) -> Path | None:
    workdir = record.working_dir or record.config.case_path.parent
    if not workdir.exists():
        return None
    chid = record.case_info.chid if record.case_info else record.config.case_path.stem
    candidates = [
        workdir / f"{chid}.smvv",
        workdir / f"{chid}.smv",
        *sorted(workdir.glob("*.smvv")),
        *sorted(workdir.glob("*.smv")),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def open_smv_preview_file(path: Path) -> str:
    viewer = find_pyrosim_results()
    if viewer:
        subprocess.Popen([str(viewer), str(path)], cwd=str(path.parent))
        return viewer.name
    os.startfile(path)
    return "Windows default app"


def find_pyrosim_results() -> Path | None:
    for command in ("PyroSimResults.exe", "pyrosimresults.exe"):
        resolved = shutil.which(command)
        if resolved:
            return Path(resolved)
    candidates = [
        Path(r"C:\Program Files\PyroSim 2024\PyroSimResults.exe"),
        Path(r"C:\Program Files\PyroSim 2023\PyroSimResults.exe"),
        Path(r"C:\Program Files\PyroSim 2022\PyroSimResults.exe"),
        Path(r"C:\Program Files\PyroSim\PyroSimResults.exe"),
        Path(r"C:\Program Files (x86)\PyroSim\PyroSimResults.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for root in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if not root.exists():
            continue
        matches = sorted(root.glob("PyroSim*/PyroSimResults.exe"), reverse=True)
        if matches:
            return matches[0]
    return None


def open_fds_in_editor(path: Path) -> str:
    for command in ("code.cmd", "code.exe", "code"):
        resolved = shutil.which(command)
        if resolved:
            subprocess.Popen([resolved, str(path)], cwd=str(path.parent))
            return "VS Code"
    vscode_candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(r"C:\Program Files\Microsoft VS Code\Code.exe"),
        Path(r"C:\Program Files (x86)\Microsoft VS Code\Code.exe"),
    ]
    for candidate in vscode_candidates:
        if candidate.exists():
            subprocess.Popen([str(candidate), str(path)], cwd=str(path.parent))
            return "VS Code"
    os.startfile(path)
    return "Windows default app"


def output_file_for_record(record: JobRecord) -> Path | None:
    candidates = []
    if record.log_path:
        candidates.append(record.log_path)
    workdir = record.working_dir or record.config.case_path.parent
    chid = record.case_info.chid if record.case_info else record.config.case_path.stem
    candidates.extend([workdir / f"{chid}.err", workdir / f"{chid}.out"])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return record.log_path


class OutputWindow(tk.Toplevel):
    def __init__(self, master: tk.Tk, record: JobRecord) -> None:
        super().__init__(master)
        self.record = record
        self.title(f"Output - {record.case_info.chid if record.case_info else record.id}")
        self.geometry("980x560")
        self.minsize(720, 420)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.path_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.path_var, padding=(10, 8, 10, 4)).grid(row=0, column=0, sticky="ew")
        frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.text = tk.Text(frame, background="#050505", foreground="#e5e7eb", insertbackground="#e5e7eb", wrap="none")
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.after_id: str | None = None
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.refresh(record)

    def refresh(self, record: JobRecord | None = None) -> None:
        if record is not None:
            self.record = record
        path = output_file_for_record(self.record)
        self.path_var.set(str(path) if path else "No output file yet")
        text = ""
        if path and path.exists():
            text = _read_tail_text(path, max_chars=60000)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.see("end")
        self.text.configure(state="disabled")
        if self.after_id:
            self.after_cancel(self.after_id)
        self.after_id = self.after(2000, self.refresh)

    def _close(self) -> None:
        if self.after_id:
            self.after_cancel(self.after_id)
        self.destroy()


class ColumnChooserWindow(tk.Toplevel):
    def __init__(self, app: SchedulerApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("Job Columns")
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        for index, column in enumerate(JOB_COLUMNS):
            ttk.Checkbutton(frame, text=JOB_HEADINGS[column], variable=app.column_visibility_vars[column], command=app._apply_visible_columns).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=3
            )


def scheduler_help_text(language: str = "zh") -> str:
    if language == "en":
        return (
            "FDS Scheduler Help\n\n"
            "1. Workflow\n"
            "- Browse: choose a .fds input file.\n"
            "- Add Job: queue the case.\n"
            "- Output: open the live black output window for the selected job.\n"
            "- Open FDS: open the selected .fds file in VS Code.\n"
            "- Plot CSV / Validation / Open SMV: inspect curves, compare experiment data, or open the Smokeview/PyroSim preview.\n\n"
            "2. MPI / OpenMP defaults\n"
            "- MPI processes: the scheduler reads MESH blocks and uses MPI_PROCESS assignments when present. If no assignment exists, it falls back to the mesh count.\n"
            "- OpenMP threads: default is 1.\n"
            "- Solver = auto: use normal FDS when OpenMP threads = 1; use the OpenMP-capable executable when OpenMP threads > 1.\n"
            "- Solver = fds: force normal FDS and keep OpenMP threads at 1.\n"
            "- Solver = openmp: force the OpenMP-capable executable.\n"
            "- Pure OpenMP: set MPI = 1, Solver = openmp, and OpenMP threads >= 2.\n\n"
            "3. Practical rules\n"
            "- For most multi-mesh cases, start with MPI first. A safe first try is MPI = mesh count and OpenMP = 1.\n"
            "- Use OpenMP when you have a clear core plan, or when one large mesh benefits from threading.\n"
            "- Avoid MPI x OpenMP settings that exceed real CPU cores unless you intentionally enable oversubscribed mode.\n"
            "- Keep MPI consistent with existing MPI_PROCESS assignments in the .fds file.\n"
            "- Too many MPI ranks for too few meshes usually wastes time.\n\n"
            "4. fds_local\n"
            "- Use the fds_local wrapper first on Windows.\n"
            "- If you turn it off, the scheduler builds a direct mpiexec command.\n"
            "- fds_local does not handle spaces in .fds file names well.\n\n"
            "5. Path advice\n"
            "- Avoid Chinese characters in project paths and .fds file names.\n"
            "- Also avoid spaces and very long paths. Short ASCII folders are safer.\n"
            "- Put each case in its own folder when possible.\n\n"
            "6. ETA\n"
            "- ETA is estimated from elapsed wall time and the current progress percentage.\n"
            "- It becomes meaningful only after FDS starts writing time-step progress.\n"
            "- Early ETA can jump around; trust the trend after the run settles."
        )
    return (
        "FDS 调度器帮助\n\n"
        "1. 基本流程\n"
        "- Browse：选择 .fds 输入文件。\n"
        "- Add Job：加入任务队列。\n"
        "- Output：打开所选任务的黑色输出窗口。\n"
        "- Open FDS：用 VS Code 打开所选 .fds 文件。\n"
        "- Plot CSV / Validation / Open SMV：看曲线、对比实验数据，或打开 Smokeview / PyroSim 预览。\n\n"
        "2. MPI / OpenMP 默认逻辑\n"
        "- MPI 进程：程序会读取 MESH 和 MPI_PROCESS 设置；如果文件里没有写 MPI_PROCESS，就按网格数作为默认值。\n"
        "- OpenMP 线程：默认是 1。\n"
        "- Solver = auto：OpenMP 线程等于 1 时用普通 FDS；大于 1 时用 OpenMP 版本。\n"
        "- Solver = fds：强制普通 FDS，并把 OpenMP 视为 1。\n"
        "- Solver = openmp：强制使用 OpenMP 版本。\n"
        "- Pure OpenMP：把 MPI 设为 1，Solver 设为 openmp，OpenMP 至少为 2。\n\n"
        "3. 简单规则\n"
        "- 大多数多网格算例，先用 MPI。一个常见起点是 MPI = 网格数，OpenMP = 1。\n"
        "- OpenMP 更适合线程规划清楚，或者单个大网格能从线程中受益的情况。\n"
        "- 不要让 MPI x OpenMP 超过真实 CPU 核心数，除非你明确要用 oversubscribed 模式。\n"
        "- 如果 .fds 里已经写了 MPI_PROCESS，最好保持 MPI 进程数一致。\n"
        "- MPI 进程过多、网格过少，一般只会浪费时间。\n\n"
        "4. fds_local\n"
        "- Windows 上优先用 fds_local 启动。\n"
        "- 关闭后，程序会改成直接拼 mpiexec 命令。\n"
        "- fds_local 对 .fds 文件名里的空格不友好。\n\n"
        "5. 路径建议\n"
        "- 强烈建议不要在项目路径或 .fds 文件名里使用中文字符。\n"
        "- 也尽量不要有空格和特别长的路径。短一点的英文路径更稳。\n"
        "- 尽量一个算例一个文件夹。\n\n"
        "6. 剩余时间估计\n"
        "- ETA 根据已用时间和当前进度百分比估算。\n"
        "- 只有 FDS 开始写时间步进度后才比较有意义。\n"
        "- 前期 ETA 可能波动较大，运行稳定后更可信。"
    )


class HelpWindow(tk.Toplevel):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("Help / 帮助")
        self.geometry("760x620")
        self.minsize(620, 460)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(1, weight=1)

        self.language_var = tk.StringVar(value="zh")
        ttk.Label(bar, text="Language").grid(row=0, column=0, sticky="w")
        language = ttk.Combobox(bar, textvariable=self.language_var, values=("zh", "en"), state="readonly", width=8)
        language.grid(row=0, column=1, sticky="w", padx=(8, 0))
        language.bind("<<ComboboxSelected>>", lambda _event: self._refresh_text())

        ttk.Button(bar, text="Close", command=self.destroy).grid(row=0, column=2, sticky="e")

        text_frame = ttk.Frame(frame)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(text_frame, wrap="word", height=28)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=yscroll.set)
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", scheduler_help_text(self.language_var.get()))
        self.text.configure(state="disabled")


def _read_tail_text(path: Path, max_chars: int = 60000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_chars:
                handle.seek(-max_chars, 2)
            data = handle.read()
        return data.decode("utf-8", errors="replace")
    except OSError as exc:
        return f"Unable to read output:\n{exc}"


class CsvSeriesData:
    def __init__(
        self,
        path: Path,
        units: list[str],
        headers: list[str],
        rows: list[list[float | None]],
        series_columns: list[int],
    ) -> None:
        self.path = path
        self.units = units
        self.headers = headers
        self.rows = rows
        self.series_columns = series_columns

    @property
    def x_label(self) -> str:
        if not self.headers:
            return "Index"
        unit = self.units[0] if self.units else ""
        return f"{self.headers[0]} ({unit})" if unit else self.headers[0]

    @property
    def x_values(self) -> list[float]:
        values = [row[0] for row in self.rows if row and row[0] is not None]
        return [float(value) for value in values]


class ExperimentSeriesData:
    def __init__(self, path: Path, sheet: str, headers: list[str], rows: list[list[float | None]]) -> None:
        self.path = path
        self.sheet = sheet
        self.headers = headers
        self.rows = rows
        self.series_columns = _numeric_series_columns(rows, start_column=1)

    @property
    def x_label(self) -> str:
        return self.headers[0] if self.headers else "Time"


def load_csv_series(path: Path) -> CsvSeriesData:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        source_rows = [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]
    if len(source_rows) < 2:
        raise ValueError("CSV file does not contain enough rows to plot.")

    first_row = [cell.strip() for cell in source_rows[0]]
    second_row = [cell.strip() for cell in source_rows[1]]
    uses_units_row = not _row_has_numeric_values(first_row) and second_row and _is_fds_units_header_pair(second_row)
    if uses_units_row:
        units = first_row
        headers = [cell or f"Column {index + 1}" for index, cell in enumerate(second_row)]
        data_rows = source_rows[2:]
    else:
        units = [""] * len(first_row)
        headers = [cell or f"Column {index + 1}" for index, cell in enumerate(first_row)]
        data_rows = source_rows[1:]

    parsed_rows: list[list[float | None]] = []
    for source in data_rows:
        if not any(cell.strip() for cell in source):
            continue
        parsed: list[float | None] = []
        for cell in source[: len(headers)]:
            try:
                parsed.append(float(cell))
            except ValueError:
                parsed.append(None)
        if parsed:
            parsed_rows.append(parsed)
    if not parsed_rows:
        raise ValueError("CSV file does not contain numeric data rows.")
    series_columns = _numeric_series_columns(parsed_rows, start_column=1)
    if not series_columns:
        raise ValueError("CSV file does not contain numeric series columns.")
    return CsvSeriesData(path, units, headers, parsed_rows, series_columns)


def load_experiment_excel(path: Path) -> ExperimentSeriesData:
    with zipfile.ZipFile(path) as package:
        workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
        ns = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
        }
        sheets = workbook.find("main:sheets", ns)
        if sheets is None or not list(sheets):
            raise ValueError("Excel workbook contains no sheets.")
        first_sheet = list(sheets)[0]
        sheet_name = first_sheet.attrib.get("name", "Sheet1")
        relation_id = first_sheet.attrib.get(f"{{{ns['rel']}}}id")
        rels = ElementTree.fromstring(package.read("xl/_rels/workbook.xml.rels"))
        target = ""
        for rel in rels:
            if rel.attrib.get("Id") == relation_id:
                target = rel.attrib.get("Target", "")
                break
        sheet_path = "xl/" + target.lstrip("/")
        if not sheet_path.endswith(".xml"):
            sheet_path += ".xml"
        shared_strings = _xlsx_shared_strings(package)
        sheet_xml = ElementTree.fromstring(package.read(sheet_path))
        rows: list[list[str]] = []
        for row_node in sheet_xml.findall(".//main:sheetData/main:row", ns):
            values: list[str] = []
            for cell in row_node.findall("main:c", ns):
                index = _xlsx_column_index(cell.attrib.get("r", ""))
                while len(values) < index - 1:
                    values.append("")
                values.append(_xlsx_cell_text(cell, shared_strings, ns))
            if any(value.strip() for value in values):
                rows.append(values)
    if len(rows) < 2:
        raise ValueError("Experiment workbook does not contain headers and data.")
    headers = [value.strip() or f"Column {index + 1}" for index, value in enumerate(rows[0])]
    parsed_rows: list[list[float | None]] = []
    for source in rows[1:]:
        parsed: list[float | None] = []
        for value in source[: len(headers)]:
            try:
                parsed.append(float(value))
            except ValueError:
                parsed.append(None)
        if parsed and parsed[0] is not None:
            parsed_rows.append(parsed)
    if not parsed_rows:
        raise ValueError("Experiment workbook does not contain numeric data rows.")
    return ExperimentSeriesData(path, sheet_name, headers, parsed_rows)


def _xlsx_shared_strings(package: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(package.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: list[str] = []
    for item in root.findall("main:si", ns):
        parts = [node.text or "" for node in item.findall(".//main:t", ns)]
        strings.append("".join(parts))
    return strings


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value_node = cell.find("main:v", ns)
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", ns))
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (ValueError, IndexError):
            return ""
    return value


def _xlsx_column_index(reference: str) -> int:
    letters = "".join(ch for ch in reference if ch.isalpha())
    if not letters:
        return 1
    index = 0
    for letter in letters.upper():
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index


def _row_has_numeric_values(row: list[str]) -> bool:
    for cell in row:
        try:
            float(cell)
            return True
        except ValueError:
            continue
    return False


def _is_fds_units_header_pair(row: list[str]) -> bool:
    first = row[0].strip().lower() if row else ""
    return first in {"time", "time step"}


def _numeric_series_columns(rows: list[list[float | None]], start_column: int) -> list[int]:
    max_columns = max((len(row) for row in rows), default=0)
    columns: list[int] = []
    for column in range(start_column, max_columns):
        numeric_count = sum(1 for row in rows if column < len(row) and row[column] is not None)
        if numeric_count:
            columns.append(column)
    return columns


class CsvPlotWindow(tk.Toplevel):
    COLORS = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706", "#0891b2", "#be123c", "#4f46e5")

    def __init__(self, master: tk.Tk, csv_files: list[Path]) -> None:
        super().__init__(master)
        self.title("CSV Time Series")
        self.geometry("980x640")
        self.minsize(760, 500)
        self.csv_files = csv_files
        self.data: CsvSeriesData | None = None
        self.visible_columns: list[int] = []
        self.start_var = tk.DoubleVar(value=0)
        self.end_var = tk.DoubleVar(value=100)
        self.file_var = tk.StringVar(value=str(csv_files[0]))
        self.status_var = tk.StringVar(value="")

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_ui()
        self._load_selected_file()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="CSV").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(top, textvariable=self.file_var, values=[str(path) for path in self.csv_files], state="readonly")
        combo.grid(row=0, column=1, sticky="ew", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_file())
        ttk.Button(top, text="Open Dir", command=self._open_csv_dir).grid(row=0, column=2)

        side = ttk.Frame(self, padding=(10, 0, 8, 10))
        side.grid(row=1, column=0, sticky="ns")
        side.rowconfigure(1, weight=1)
        ttk.Label(side, text="Series").grid(row=0, column=0, sticky="w")
        self.series_list = tk.Listbox(side, selectmode="extended", width=28, exportselection=False)
        self.series_list.grid(row=1, column=0, sticky="ns")
        self.series_list.bind("<<ListboxSelect>>", lambda _event: self._redraw())
        buttons = ttk.Frame(side)
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="All", command=self._select_all).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Clear", command=self._clear_selection).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        plot_frame = ttk.Frame(self, padding=(0, 0, 10, 10))
        plot_frame.grid(row=1, column=1, sticky="nsew")
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(plot_frame, background="white", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._redraw())

        range_frame = ttk.Frame(plot_frame)
        range_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        range_frame.columnconfigure(1, weight=1)
        range_frame.columnconfigure(3, weight=1)
        ttk.Label(range_frame, text="Start").grid(row=0, column=0, sticky="w")
        ttk.Scale(range_frame, from_=0, to=100, variable=self.start_var, command=lambda _v: self._redraw()).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Label(range_frame, text="End").grid(row=0, column=2, sticky="w")
        ttk.Scale(range_frame, from_=0, to=100, variable=self.end_var, command=lambda _v: self._redraw()).grid(
            row=0, column=3, sticky="ew", padx=8
        )
        ttk.Label(plot_frame, textvariable=self.status_var).grid(row=2, column=0, sticky="w", pady=(6, 0))

    def _load_selected_file(self) -> None:
        try:
            self.data = load_csv_series(Path(self.file_var.get()))
        except Exception as exc:
            messagebox.showerror("CSV load failed", str(exc))
            return
        self.series_list.delete(0, "end")
        self.visible_columns = self.data.series_columns
        for index in self.visible_columns:
            header = self.data.headers[index] if index < len(self.data.headers) else f"Column {index + 1}"
            unit = self.data.units[index] if index < len(self.data.units) else ""
            label = f"{header} ({unit})" if unit else header
            self.series_list.insert("end", label)
        self._select_first_series()
        self.start_var.set(0)
        self.end_var.set(100)
        self._redraw()

    def _select_first_series(self) -> None:
        if self.series_list.size() > 0:
            self.series_list.selection_set(0)

    def _select_all(self) -> None:
        self.series_list.selection_set(0, "end")
        self._redraw()

    def _clear_selection(self) -> None:
        self.series_list.selection_clear(0, "end")
        self._redraw()

    def _open_csv_dir(self) -> None:
        path = Path(self.file_var.get())
        if path.exists():
            os.startfile(path.parent)

    def _redraw(self) -> None:
        self.canvas.delete("all")
        if not self.data:
            return
        width = max(self.canvas.winfo_width(), 300)
        height = max(self.canvas.winfo_height(), 220)
        left, right, top, bottom = 72, max(width - 210, 180), 34, height - 58
        if right <= left or bottom <= top:
            return

        selected = [self.visible_columns[index] for index in self.series_list.curselection()]
        if not selected:
            self.status_var.set("Select one or more series.")
            return
        start_pct = min(self.start_var.get(), self.end_var.get())
        end_pct = max(self.start_var.get(), self.end_var.get())
        total = len(self.data.rows)
        start = int((total - 1) * start_pct / 100)
        end = int((total - 1) * end_pct / 100) + 1
        rows = self.data.rows[start:end]
        if len(rows) < 2:
            self.status_var.set("Move the sliders to include at least two data points.")
            return

        x_values = [row[0] for row in rows if row[0] is not None]
        y_values = [row[col] for row in rows for col in selected if col < len(row) and row[col] is not None]
        if not x_values or not y_values:
            self.status_var.set("Selected range has no numeric values.")
            return
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        if x_min == x_max:
            x_max = x_min + 1
        if y_min == y_max:
            pad = abs(y_min) * 0.05 or 1
            y_min -= pad
            y_max += pad

        self._draw_axes(left, right, top, bottom, x_min, x_max, y_min, y_max)
        for series_index, col in enumerate(selected):
            points = []
            for row in rows:
                if col >= len(row) or row[0] is None or row[col] is None:
                    continue
                x = left + (row[0] - x_min) / (x_max - x_min) * (right - left)
                y = bottom - (row[col] - y_min) / (y_max - y_min) * (bottom - top)
                points.extend((x, y))
            if len(points) >= 4:
                color = self.COLORS[series_index % len(self.COLORS)]
                self.canvas.create_line(*points, fill=color, width=2)
                label = self.data.headers[col] if col < len(self.data.headers) else f"Column {col + 1}"
                self.canvas.create_line(right + 18, top + 16 + series_index * 18, right + 40, top + 16 + series_index * 18, fill=color, width=2)
                self.canvas.create_text(right + 46, top + 16 + series_index * 18, text=label, fill=color, anchor="w")
        title = self.data.path.name
        self.canvas.create_text((left + right) / 2, 16, text=title, anchor="center", font=("TkDefaultFont", 10, "bold"))
        self.status_var.set(f"{len(rows)} rows, {len(selected)} series, {self.data.x_label}")

    def _draw_axes(self, left: int, right: int, top: int, bottom: int, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        self.canvas.create_line(left, bottom, right, bottom, fill="#334155")
        self.canvas.create_line(left, top, left, bottom, fill="#334155")
        for tick in range(6):
            x = left + tick * (right - left) / 5
            value = x_min + tick * (x_max - x_min) / 5
            self.canvas.create_line(x, bottom, x, bottom + 4, fill="#334155")
            self.canvas.create_text(x, bottom + 18, text=f"{value:.3g}", fill="#475569")
            y = bottom - tick * (bottom - top) / 5
            y_value = y_min + tick * (y_max - y_min) / 5
            self.canvas.create_line(left - 4, y, left, y, fill="#334155")
            self.canvas.create_text(left - 8, y, text=f"{y_value:.3g}", fill="#475569", anchor="e")
            if tick:
                self.canvas.create_line(left, y, right, y, fill="#e2e8f0")


class ValidationWindow(tk.Toplevel):
    COLORS = CsvPlotWindow.COLORS

    def __init__(self, master: tk.Tk, excel_files: list[Path], csv_files: list[Path]) -> None:
        super().__init__(master)
        self.title("Validation")
        self.geometry("1060x660")
        self.minsize(820, 520)
        self.experiment = load_experiment_excel(excel_files[0])
        self.fds = self._load_best_fds_data(csv_files)
        self.matches = match_validation_series(self.experiment, self.fds)
        self.visible_matches: list[ValidationMatch] = []
        self.status_var = tk.StringVar(value="")

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_ui()
        self._populate_matches()
        self._redraw()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(top, text=f"Experiment: {self.experiment.path.name}").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=f"FDS: {self.fds.path.name}").grid(row=0, column=1, sticky="w", padx=(20, 0))

        side = ttk.Frame(self, padding=(10, 0, 8, 10))
        side.grid(row=1, column=0, sticky="ns")
        side.rowconfigure(1, weight=1)
        ttk.Label(side, text="Matched points").grid(row=0, column=0, sticky="w")
        self.match_list = tk.Listbox(side, selectmode="extended", width=30, exportselection=False)
        self.match_list.grid(row=1, column=0, sticky="ns")
        self.match_list.bind("<<ListboxSelect>>", lambda _event: self._redraw())
        buttons = ttk.Frame(side)
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="All", command=self._select_all).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Clear", command=self._clear_selection).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        plot_frame = ttk.Frame(self, padding=(0, 0, 10, 10))
        plot_frame.grid(row=1, column=1, sticky="nsew")
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(plot_frame, background="white", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        ttk.Label(plot_frame, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _populate_matches(self) -> None:
        self.match_list.delete(0, "end")
        self.visible_matches = self.matches
        for match in self.visible_matches:
            self.match_list.insert("end", f"{match.label}: FDS {match.fds_label} / Exp {match.experiment_label}")
        if self.visible_matches:
            self.match_list.selection_set(0)

    def _select_all(self) -> None:
        self.match_list.selection_set(0, "end")
        self._redraw()

    def _clear_selection(self) -> None:
        self.match_list.selection_clear(0, "end")
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 320)
        height = max(self.canvas.winfo_height(), 240)
        left, right, top, bottom = 72, max(width - 260, 200), 36, height - 58
        if right <= left or bottom <= top:
            return
        selected = [self.visible_matches[index] for index in self.match_list.curselection()]
        if not selected:
            self.status_var.set("Select one or more matched points.")
            return

        series = []
        for match in selected:
            series.append((match, "FDS", self._series_points(self.fds.rows, match.fds_column)))
            series.append((match, "Experiment", self._series_points(self.experiment.rows, match.experiment_column)))
        x_values = [x for _match, _source, points in series for x, _y in points]
        y_values = [y for _match, _source, points in series for _x, y in points]
        if not x_values or not y_values:
            self.status_var.set("Selected points have no numeric values.")
            return
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        if x_min == x_max:
            x_max += 1
        if y_min == y_max:
            pad = abs(y_min) * 0.05 or 1
            y_min -= pad
            y_max += pad

        self._draw_axes(left, right, top, bottom, x_min, x_max, y_min, y_max)
        legend_y = top + 14
        for index, match in enumerate(selected):
            color = self.COLORS[index % len(self.COLORS)]
            fds_points = self._map_points(self._series_points(self.fds.rows, match.fds_column), left, right, top, bottom, x_min, x_max, y_min, y_max)
            exp_points = self._map_points(
                self._series_points(self.experiment.rows, match.experiment_column), left, right, top, bottom, x_min, x_max, y_min, y_max
            )
            if len(fds_points) >= 4:
                self.canvas.create_line(*fds_points, fill=color, width=2)
            if len(exp_points) >= 4:
                self._draw_dashed_polyline(exp_points, color)
            self.canvas.create_line(right + 18, legend_y, right + 40, legend_y, fill=color, width=2)
            self.canvas.create_text(right + 46, legend_y, text=f"{match.label} FDS", fill=color, anchor="w")
            self._draw_dashed_line(right + 18, legend_y + 16, right + 40, legend_y + 16, color)
            self.canvas.create_text(right + 46, legend_y + 16, text=f"{match.label} Experiment", fill=color, anchor="w")
            legend_y += 38
        self.canvas.create_text((left + right) / 2, 16, text="FDS vs Experiment", anchor="center", font=("TkDefaultFont", 10, "bold"))
        self.status_var.set(f"{len(selected)} matched point(s). Solid = FDS, dashed = experiment.")

    def _draw_axes(self, left: int, right: int, top: int, bottom: int, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        self.canvas.create_line(left, bottom, right, bottom, fill="#334155")
        self.canvas.create_line(left, top, left, bottom, fill="#334155")
        for tick in range(6):
            x = left + tick * (right - left) / 5
            value = x_min + tick * (x_max - x_min) / 5
            self.canvas.create_line(x, bottom, x, bottom + 4, fill="#334155")
            self.canvas.create_text(x, bottom + 18, text=f"{value:.3g}", fill="#475569")
            y = bottom - tick * (bottom - top) / 5
            y_value = y_min + tick * (y_max - y_min) / 5
            self.canvas.create_line(left - 4, y, left, y, fill="#334155")
            self.canvas.create_text(left - 8, y, text=f"{y_value:.3g}", fill="#475569", anchor="e")
            if tick:
                self.canvas.create_line(left, y, right, y, fill="#e2e8f0")

    @staticmethod
    def _series_points(rows: list[list[float | None]], column: int) -> list[tuple[float, float]]:
        points = []
        for row in rows:
            if column < len(row) and row[0] is not None and row[column] is not None:
                points.append((float(row[0]), float(row[column])))
        return points

    @staticmethod
    def _map_points(
        points: list[tuple[float, float]], left: int, right: int, top: int, bottom: int, x_min: float, x_max: float, y_min: float, y_max: float
    ) -> list[float]:
        mapped: list[float] = []
        for x_value, y_value in points:
            x = left + (x_value - x_min) / (x_max - x_min) * (right - left)
            y = bottom - (y_value - y_min) / (y_max - y_min) * (bottom - top)
            mapped.extend((x, y))
        return mapped

    def _draw_dashed_polyline(self, points: list[float], color: str) -> None:
        for index in range(0, len(points) - 2, 2):
            self._draw_dashed_line(points[index], points[index + 1], points[index + 2], points[index + 3], color)

    def _draw_dashed_line(self, x1: float, y1: float, x2: float, y2: float, color: str) -> None:
        self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2, dash=(5, 3))

    @staticmethod
    def _load_best_fds_data(csv_files: list[Path]) -> CsvSeriesData:
        preferred = [path for path in csv_files if path.name.lower().endswith("_devc.csv")]
        for path in preferred + csv_files:
            data = load_csv_series(path)
            if data.series_columns:
                return data
        raise ValueError("No plottable FDS CSV data found.")


class ValidationMatch:
    def __init__(self, label: str, experiment_column: int, fds_column: int, experiment_label: str, fds_label: str) -> None:
        self.label = label
        self.experiment_column = experiment_column
        self.fds_column = fds_column
        self.experiment_label = experiment_label
        self.fds_label = fds_label


def match_validation_series(experiment: ExperimentSeriesData, fds: CsvSeriesData) -> list[ValidationMatch]:
    fds_by_key = {_measurement_key(fds.headers[column]): column for column in fds.series_columns if column < len(fds.headers)}
    matches: list[ValidationMatch] = []
    for column in experiment.series_columns:
        if column >= len(experiment.headers):
            continue
        exp_label = experiment.headers[column]
        key = _measurement_key(exp_label)
        fds_column = fds_by_key.get(key)
        if fds_column is None:
            continue
        fds_label = fds.headers[fds_column]
        matches.append(ValidationMatch(label=_display_measurement_label(exp_label), experiment_column=column, fds_column=fds_column, experiment_label=exp_label, fds_label=fds_label))
    return matches


def _measurement_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _display_measurement_label(label: str) -> str:
    parts = re.split(r"[-_\s]+", label.strip())
    return "-".join(part.upper() if len(part) <= 2 else part for part in parts if part)


def main() -> None:
    app = SchedulerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
