"""run_code: temp-file + interpreter execution over the fake transport."""

from __future__ import annotations

import re

import pytest

from xshellz import CommandResult, CommandTimeoutError, UnsupportedLanguageError

from .conftest import FakeTransport, make_sandbox

TEMP_PATH_RE = re.compile(r"^/tmp/xshellz-run-[0-9a-f]{8}\.(py|js|sh|rb|php)$")


@pytest.mark.parametrize(
    ("language", "interpreter", "extension"),
    [
        ("python", "python3", "py"),
        ("node", "node", "js"),
        ("bash", "bash", "sh"),
        ("ruby", "ruby", "rb"),
        ("php", "php", "php"),
    ],
)
def test_run_code_writes_executes_and_deletes_the_temp_file(
    language: str, interpreter: str, extension: str
) -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="42\n", stderr="", exit_code=0)
    sbx = make_sandbox(fake)

    result = sbx.run_code(language, "some code")

    assert result.stdout == "42\n"
    # The code landed in a random temp file with the right extension...
    assert len(fake.files) == 1
    temp_path = next(iter(fake.files))
    assert TEMP_PATH_RE.match(temp_path)
    assert temp_path.endswith(f".{extension}")
    assert fake.files[temp_path] == b"some code"
    # ...was executed with the right interpreter, then deleted.
    assert fake.commands == [f"{interpreter} {temp_path}", f"rm -f {temp_path}"]


def test_run_code_passes_cwd_env_and_returns_nonzero_as_data() -> None:
    fake = FakeTransport()
    fake.next_result = CommandResult(stdout="", stderr="boom", exit_code=3)
    sbx = make_sandbox(fake)

    result = sbx.run_code("python", "raise SystemExit(3)", cwd="/srv", env={"A": "1"})

    assert result.exit_code == 3
    assert fake.commands[0].startswith("export A=1 && cd /srv && python3 /tmp/xshellz-run-")


def test_run_code_deletes_the_temp_file_even_when_run_raises() -> None:
    fake = FakeTransport()
    sbx = make_sandbox(fake)

    def handler(cmd: str) -> CommandResult:
        if cmd.startswith("python3"):
            raise CommandTimeoutError("too slow")
        return CommandResult(stdout="", stderr="", exit_code=0)

    fake.exec_handler = handler

    with pytest.raises(CommandTimeoutError):
        sbx.run_code("python", "while True: pass", timeout=0.1)

    assert fake.commands[-1].startswith("rm -f /tmp/xshellz-run-")


def test_run_code_unknown_language_raises_listing_supported() -> None:
    sbx = make_sandbox(FakeTransport())

    with pytest.raises(UnsupportedLanguageError, match="bash, node, php, python, ruby"):
        sbx.run_code("perl", "print 42")
