"""Background jobs: spawn / JobHandle / jobs() against the fake transport."""

from __future__ import annotations

import re

import pytest

from xshellz import CommandResult, JobInfo, XshellzError
from xshellz.jobs import build_spawn_command, new_job_id, parse_jobs_output

from .conftest import FakeTransport, make_sandbox

JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+-[0-9a-f]{8}$")


def test_new_job_id_is_name_prefixed_and_sanitized() -> None:
    assert new_job_id("my worker!").startswith("my_worker_-")
    assert new_job_id().startswith("job-")
    assert JOB_ID_RE.match(new_job_id("w"))
    assert new_job_id() != new_job_id()


def test_build_spawn_command_shape_and_quoting() -> None:
    cmd = build_spawn_command("echo 'it works' > /tmp/x", "w-deadbeef")

    assert cmd == (
        "mkdir -p ~/.xshellz/jobs && "
        "nohup bash -c 'echo '\"'\"'it works'\"'\"' > /tmp/x' "
        "> ~/.xshellz/jobs/w-deadbeef.log 2>&1 < /dev/null & "
        "echo $! > ~/.xshellz/jobs/w-deadbeef.pid; echo $!"
    )


def test_spawn_returns_handle_with_pid_id_and_log_path() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="4242\n", stderr="", exit_code=0)
    sbx = make_sandbox(fake)

    job = sbx.spawn("sleep 999", name="worker")

    assert job.pid == 4242
    assert job.id.startswith("worker-")
    assert job.log_path == f"~/.xshellz/jobs/{job.id}.log"
    assert len(fake.commands) == 1
    assert fake.commands[0].startswith("mkdir -p ~/.xshellz/jobs && nohup bash -c 'sleep 999'")


def test_spawn_failure_raises() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="", stderr="mkdir: permission denied", exit_code=1)
    sbx = make_sandbox(fake)

    with pytest.raises(XshellzError, match="Failed to spawn"):
        sbx.spawn("true")


def test_spawn_non_numeric_pid_raises() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="garbage\n", stderr="", exit_code=0)
    sbx = make_sandbox(fake)

    with pytest.raises(XshellzError, match="Failed to spawn"):
        sbx.spawn("true")


def _spawned_job(fake: FakeTransport):
    fake.next_result = CommandResult(stdout="4242\n", stderr="", exit_code=0)
    sbx = make_sandbox(fake)
    job = sbx.spawn("sleep 999", name="w")
    fake.commands.clear()
    return job


def test_is_running_probes_with_kill_0() -> None:
    fake = FakeTransport()
    job = _spawned_job(fake)

    fake.next_result = CommandResult(stdout="", stderr="", exit_code=0)
    assert job.is_running() is True

    fake.next_result = CommandResult(stdout="", stderr="", exit_code=1)
    assert job.is_running() is False

    assert fake.commands == ["kill -0 4242 2>/dev/null"] * 2


def test_logs_tails_the_log_file() -> None:
    fake = FakeTransport()
    job = _spawned_job(fake)
    fake.next_result = CommandResult(stdout="line9\nline10\n", stderr="", exit_code=0)

    assert job.logs(tail_lines=2) == "line9\nline10\n"
    assert fake.commands == [f"tail -n 2 {job.log_path}"]

    with pytest.raises(XshellzError, match="tail_lines"):
        job.logs(tail_lines=0)


def test_stop_sends_sigterm_and_skips_sigkill_when_process_dies() -> None:
    fake = FakeTransport()
    job = _spawned_job(fake)
    # SIGTERM succeeds; the subsequent kill -0 probe reports "gone".
    fake.exec_handler = lambda cmd: CommandResult(
        stdout="", stderr="", exit_code=1 if cmd.startswith("kill -0") else 0
    )

    job.stop(grace=1.0)

    assert fake.commands[0] == "kill 4242 2>/dev/null"
    assert not any(cmd.startswith("kill -9") for cmd in fake.commands)


def test_stop_escalates_to_sigkill_after_grace() -> None:
    fake = FakeTransport()
    job = _spawned_job(fake)
    fake.exec_handler = lambda cmd: CommandResult(stdout="", stderr="", exit_code=0)

    job.stop(grace=0.0)

    assert fake.commands[0] == "kill 4242 2>/dev/null"
    assert fake.commands[-1] == "kill -9 4242 2>/dev/null"


def test_jobs_lists_log_files_with_liveness() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(
        stdout="worker-aa11bb22|4242|1\nold-job-cc33dd44||0\n", stderr="", exit_code=0
    )
    sbx = make_sandbox(fake)

    jobs = sbx.jobs()

    assert jobs == [
        JobInfo(
            id="worker-aa11bb22",
            pid=4242,
            log_path="~/.xshellz/jobs/worker-aa11bb22.log",
            running=True,
        ),
        JobInfo(
            id="old-job-cc33dd44",
            pid=None,
            log_path="~/.xshellz/jobs/old-job-cc33dd44.log",
            running=False,
        ),
    ]
    assert len(fake.commands) == 1
    assert fake.commands[0].startswith("cd ~/.xshellz/jobs")


def test_parse_jobs_output_skips_garbage_lines() -> None:
    assert parse_jobs_output("") == []
    assert parse_jobs_output("no pipes here\n\nx|y\n") == []
    assert parse_jobs_output("a|12|1\n")[0].pid == 12
