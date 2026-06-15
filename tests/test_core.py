from pathlib import Path
import time

import pytest

from fds_scheduler.fds_case import ensure_restart_case, parse_fds_case
from fds_scheduler.app import (
    ExperimentSeriesData,
    CsvSeriesData,
    find_pyrosim_results,
    match_validation_series,
    scheduler_help_text,
    smv_preview_file_for_record,
)
from fds_scheduler.fds_env import detect_fds_environment
from fds_scheduler.fds_output import summarize_fds_output
from fds_scheduler.job import FdsScheduler, JobConfig, JobRecord, JobStatus


def test_parse_sample_case():
    info = parse_fds_case(Path("tests/sample_case.fds"))
    assert info.chid == "sample_case"
    assert info.mesh_count == 2
    assert info.assigned_mpi_processes == [0, 1]
    assert info.suggested_mpi_processes == 2
    assert info.t_end == 1.0
    assert info.meshes[0].ijk == (10, 10, 10)
    assert info.meshes[0].cell_count == 1000
    assert info.total_cells == 2000
    assert info.mpi_loads == {0: 1000, 1: 1000}


def test_restart_case_creation(tmp_path):
    source = tmp_path / "case.fds"
    source.write_text("&HEAD CHID='case' /\n&TAIL /\n", encoding="utf-8")
    restart = ensure_restart_case(source)
    text = restart.read_text(encoding="utf-8")
    assert "RESTART=T" in text


def test_restart_case_updates_existing_misc(tmp_path):
    source = tmp_path / "case.fds"
    source.write_text("&HEAD CHID='case' /\n&MISC TMPA=25. /\n&TAIL /\n", encoding="utf-8")
    restart = ensure_restart_case(source)
    text = restart.read_text(encoding="utf-8")
    assert "&MISC TMPA=25., RESTART=T /" in text


def test_command_generation_uses_fdsinit():
    detect_fds_environment()
    scheduler = FdsScheduler(state_dir="scheduler_state_test")
    config = JobConfig(
        case_path=Path("tests/sample_case.fds"),
        mpi_processes=2,
        openmp_threads=4,
    )
    record = scheduler.submit(config)
    assert "fdsinit.bat" in record.command
    assert "fds_local" in record.command
    assert "-p 2" in record.command
    assert "-o 4" in record.command
    assert record.script_path
    assert record.script_path.exists()


def test_solver_choice_affects_fds_local_command():
    scheduler = FdsScheduler(state_dir="scheduler_state_test_solver")
    config = JobConfig(
        case_path=Path("tests/sample_case.fds"),
        mpi_processes=2,
        openmp_threads=4,
        solver="fds",
    )
    record = scheduler.submit(config)
    assert "-o 1" in record.command
    assert "-f" not in record.command

    config = JobConfig(
        case_path=Path("tests/sample_case.fds"),
        mpi_processes=2,
        openmp_threads=1,
        solver="openmp",
    )
    record = scheduler.submit(config)
    assert "-f" in record.command


def test_pure_openmp_command_uses_single_mpi_rank():
    scheduler = FdsScheduler(state_dir="scheduler_state_test_openmp")
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/sample_case.fds"),
            mpi_processes=1,
            openmp_threads=4,
            solver="openmp",
        )
    )
    assert "-f" in record.command
    assert "-p 1" in record.command
    assert "-o 4" in record.command


def test_output_summary_detects_error_and_restart(tmp_path):
    (tmp_path / "case.err").write_text(
        "MPI Process      0 started on host\nERROR(102): Input file missing\n",
        encoding="utf-8",
    )
    (tmp_path / "case.restart").write_text("restart", encoding="utf-8")
    summary = summarize_fds_output("case", tmp_path)
    assert summary.error_detected
    assert summary.error_code == "102"
    assert summary.restart_available
    assert summary.mpi_processes_started == 1


def test_output_summary_detects_time_stepping(tmp_path):
    (tmp_path / "case.err").write_text("Time Step:       1, Simulation Time:   0.1 s\n", encoding="utf-8")
    summary = summarize_fds_output("case", tmp_path)
    assert summary.time_stepping_started
    assert summary.latest_simulation_time == 0.1


def test_output_summary_detects_setup_only_success(tmp_path):
    (tmp_path / "case.out").write_text("STOP: Set-up only (CHID: case)\n", encoding="utf-8")
    summary = summarize_fds_output("case", tmp_path)
    assert summary.success_detected
    assert summary.status_note == "FDS completed successfully"


def test_fds_local_rejects_case_file_names_with_spaces(tmp_path):
    case = tmp_path / "case with space.fds"
    case.write_text("&HEAD CHID='space_case' /\n&MESH IJK=1,1,1, XB=0,1,0,1,0,1 /\n&TAIL /\n", encoding="utf-8")
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    with pytest.raises(ValueError, match="spaces"):
        scheduler.submit(JobConfig(case_path=case))


def test_stop_does_not_change_terminal_job_status(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    record.status = JobStatus.SUCCEEDED
    scheduler.stop_job(record.id)
    assert record.status == JobStatus.SUCCEEDED


def test_cancel_queued_job_marks_cancelled(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    scheduler.cancel_job(record.id)
    assert record.status == JobStatus.CANCELLED


def test_delete_job_removes_history_only(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    runtime_case = record.working_dir / record.config.case_path.name

    deleted_ids = scheduler.delete_job(record.id)

    assert deleted_ids == [record.id]
    assert record.id not in scheduler.jobs
    assert runtime_case.exists()
    loaded = FdsScheduler(state_dir=tmp_path / "state")
    assert record.id not in loaded.jobs


def test_delete_running_job_is_rejected(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    record.status = JobStatus.RUNNING

    with pytest.raises(ValueError, match="running job"):
        scheduler.delete_job(record.id)


def test_finalize_preserves_cancelled_status(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    record.status = JobStatus.CANCELLED
    scheduler._finalize_record(record, 1)
    assert record.status == JobStatus.CANCELLED


def test_scheduler_loads_saved_jobs_without_requeue(tmp_path):
    state_dir = tmp_path / "state"
    scheduler = FdsScheduler(state_dir=state_dir)
    record = scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds")))
    record.status = JobStatus.SUCCEEDED
    scheduler._save_state()

    loaded = FdsScheduler(state_dir=state_dir)
    assert record.id in loaded.jobs
    assert loaded.jobs[record.id].status == JobStatus.SUCCEEDED


def test_restart_job_records_parent_id(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/sample_case.fds"),
            output_mode="named_dir",
            output_dir=tmp_path / "run",
        )
    )
    (tmp_path / "run" / "sample_case.restart").write_text("restart", encoding="utf-8")
    restart = scheduler.create_restart_job(record.id)
    assert restart.config.restart
    assert restart.config.restart_from_job_id == record.id


def test_preview_config_does_not_create_job(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    preview = scheduler.preview_config(JobConfig(case_path=Path("tests/sample_case.fds"), mpi_processes=0))
    assert preview.id == "preview"
    assert "-p 2" in preview.command
    assert not scheduler.jobs


def test_invalid_solver_is_rejected(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    with pytest.raises(ValueError, match="Solver"):
        scheduler.submit(JobConfig(case_path=Path("tests/sample_case.fds"), solver="bad"))


def test_submit_many(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    records = scheduler.submit_many(
        [
            JobConfig(case_path=Path("tests/sample_case.fds")),
            JobConfig(case_path=Path("tests/smoke_fds_case.fds")),
        ]
    )
    assert len(records) == 2
    assert len(scheduler.jobs) == 2


def test_case_warnings_for_noncontinuous_mpi(tmp_path):
    case = tmp_path / "bad_mpi.fds"
    case.write_text(
        "&HEAD CHID='bad_mpi' /\n"
        "&MESH IJK=2,2,2, XB=0,1,0,1,0,1, MPI_PROCESS=1 /\n"
        "&TAIL /\n",
        encoding="utf-8",
    )
    info = parse_fds_case(case)
    assert any("continuous" in warning for warning in info.warnings)


def test_case_config_warnings_for_too_many_mpi(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    info = parse_fds_case(Path("tests/sample_case.fds"))
    warnings = scheduler.case_config_warnings(info, JobConfig(case_path=Path("tests/sample_case.fds"), mpi_processes=4))
    assert any("exceed mesh count" in warning for warning in warnings)


def test_refresh_record_output_updates_progress_and_cpu(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/sample_case.fds"),
            output_mode="named_dir",
            output_dir=tmp_path / "run",
        )
    )
    workdir = record.working_dir or record.config.case_path.parent
    (workdir / "sample_case.err").write_text("Time Step:       1, Simulation Time:   0.5 s\n", encoding="utf-8")
    (workdir / "sample_case_cpu.csv").write_text("Rank,MAIN\n0,1.0\n", encoding="utf-8")
    refreshed = scheduler.refresh_record_output(record.id)
    assert refreshed.latest_simulation_time == 0.5
    assert refreshed.progress_percent == 50.0
    assert refreshed.cpu_file_available


def test_refresh_record_output_estimates_remaining_time(tmp_path):
    scheduler = FdsScheduler(state_dir=tmp_path / "state")
    record = scheduler.submit(
        JobConfig(
            case_path=Path("tests/sample_case.fds"),
            output_mode="named_dir",
            output_dir=tmp_path / "run",
        )
    )
    record.status = JobStatus.RUNNING
    record.started_at = time.time() - 10
    workdir = record.working_dir or record.config.case_path.parent
    (workdir / "sample_case.err").write_text("Time Step:       1, Simulation Time:   0.5 s\n", encoding="utf-8")

    refreshed = scheduler.refresh_record_output(record.id)

    assert refreshed.progress_percent == 50.0
    assert refreshed.estimated_remaining_seconds is not None
    assert refreshed.estimated_remaining_seconds > 0


def test_smv_preview_prefers_saved_view_file(tmp_path):
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / "sample_case.smv").write_text("smv", encoding="utf-8")
    (workdir / "sample_case.smvv").write_bytes(b"view")
    record = JobRecord(
        id="job",
        config=JobConfig(case_path=Path("tests/sample_case.fds")),
        case_info=parse_fds_case(Path("tests/sample_case.fds")),
        working_dir=workdir,
    )
    assert smv_preview_file_for_record(record) == workdir / "sample_case.smvv"


def test_import_existing_result_from_case_directory(tmp_path):
    case = tmp_path / "finished.fds"
    case.write_text("&HEAD CHID='finished' /\n&TIME T_END=1.0 /\n&TAIL /\n", encoding="utf-8")
    (tmp_path / "finished.out").write_text("STOP: FDS completed successfully\n", encoding="utf-8")
    (tmp_path / "finished.err").write_text("Time Step:       1, Simulation Time:   1.0 s\n", encoding="utf-8")
    (tmp_path / "finished_hrr.csv").write_text("s,kW\nTime,HRR\n0,0\n1,10\n", encoding="utf-8")
    scheduler = FdsScheduler(state_dir=tmp_path / "state")

    record = scheduler.import_existing_result(case)

    assert record is not None
    assert record.status == JobStatus.SUCCEEDED
    assert record.working_dir == tmp_path
    assert record.progress_percent == 100.0
    assert record.id in scheduler.jobs

    loaded = FdsScheduler(state_dir=tmp_path / "state")
    assert record.id in loaded.jobs


def test_pyrosim_results_can_be_located():
    viewer = find_pyrosim_results()
    assert viewer is not None
    assert viewer.name.lower() == "pyrosimresults.exe"


def test_validation_matches_case_insensitive_measurement_names():
    experiment = ExperimentSeriesData(
        path=Path("experiment.xlsx"),
        sheet="Sheet1",
        headers=["Time", "ir-v1", "Th-h1"],
        rows=[[0.0, 1.0, 2.0]],
    )
    fds = CsvSeriesData(
        path=Path("case_devc.csv"),
        units=["s", "kW/m2", "C"],
        headers=["Time", "IR-V1", "Th-h1"],
        rows=[[0.0, 1.1, 2.1]],
        series_columns=[1, 2],
    )
    matches = match_validation_series(experiment, fds)
    assert [(match.experiment_label, match.fds_label) for match in matches] == [("ir-v1", "IR-V1"), ("Th-h1", "Th-h1")]


def test_scheduler_help_text_mentions_default_logic_and_path_advice():
    text = scheduler_help_text()
    assert "Solver = auto" in text
    assert "Pure OpenMP" in text
    assert "do not use Chinese characters" in text
    assert "MPI = mesh count" in text
