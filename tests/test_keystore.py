"""Keystore tests: on-disk layout, permissions, and name sanitization."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from xshellz import DEFAULT_KEYSTORE_DIR, Keystore
from xshellz.keystore import sanitize_name


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_save_load_roundtrip_with_0600_file_in_0700_dir(tmp_path: Path) -> None:
    store = Keystore(tmp_path / "keys")

    key_path = store.save("my-box", "PRIVATE KEY MATERIAL")

    assert key_path == tmp_path / "keys" / "my-box.key"
    assert store.load("my-box") == "PRIVATE KEY MATERIAL"
    assert mode_of(key_path) == 0o600
    assert mode_of(tmp_path / "keys") == 0o700


def test_save_overwrites_and_retightens_permissions(tmp_path: Path) -> None:
    store = Keystore(tmp_path)
    loose = store.path_for("box")
    loose.write_text("old")
    loose.chmod(0o644)

    store.save("box", "new")

    assert store.load("box") == "new"
    assert mode_of(loose) == 0o600


def test_load_missing_returns_none_and_delete_reports(tmp_path: Path) -> None:
    store = Keystore(tmp_path)

    assert store.load("nope") is None
    assert store.delete("nope") is False

    store.save("gone", "k")
    assert store.delete("gone") is True
    assert store.load("gone") is None


def test_names_are_sanitized_to_safe_filenames(tmp_path: Path) -> None:
    store = Keystore(tmp_path)

    store.save("my box/../evil!", "k")

    assert store.path_for("my box/../evil!").name == "my_box_.._evil_.key"
    assert store.load("my box/../evil!") == "k"
    # Nothing escaped the keystore directory.
    assert [p.name for p in tmp_path.iterdir()] == ["my_box_.._evil_.key"]


def test_sanitize_name_never_returns_empty() -> None:
    assert sanitize_name("///") == "___"
    assert sanitize_name("") == "_"
    assert sanitize_name("ok-1.2_3") == "ok-1.2_3"


def test_default_path_expands_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    store = Keystore()

    assert store.path == tmp_path / ".xshellz" / "keys"
    assert DEFAULT_KEYSTORE_DIR == Path("~/.xshellz/keys")
    assert "~" not in str(store.path)
