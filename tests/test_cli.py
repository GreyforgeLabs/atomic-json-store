import json
import subprocess
import sys

import pytest

from atomic_json_store import __version__
from atomic_json_store.cli import (
    EXIT_ERROR,
    EXIT_MISSING,
    EXIT_OK,
    EXIT_USAGE,
    KeyPathError,
    delete_path,
    get_path,
    main,
    set_path,
)
from atomic_json_store.core import FORMAT, AtomicJsonStore


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "atomic_json_store", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert __version__ in result.stdout


def test_init_creates_store_and_is_idempotent(store_path, capsys):
    code, out, _ = run(capsys, str(store_path), "init", "--schema-version", "2")
    assert code == EXIT_OK
    info = json.loads(out)
    assert info["exists"] is True
    assert info["schema_version"] == 2
    AtomicJsonStore(store_path, schema_version=2).save({"kept": True})
    code, out, _ = run(capsys, str(store_path), "init", "--schema-version", "2")
    assert code == EXIT_OK
    assert AtomicJsonStore(store_path, schema_version=2).load() == {"kept": True}


def test_info_on_missing_and_existing(store_path, capsys):
    code, out, _ = run(capsys, str(store_path), "info")
    assert code == EXIT_OK
    assert json.loads(out)["exists"] is False
    AtomicJsonStore(store_path, schema_version=4).save({"a": 1})
    code, out, _ = run(capsys, str(store_path), "info")
    assert json.loads(out) == {
        "path": str(store_path),
        "exists": True,
        "format": FORMAT,
        "schema_version": 4,
        "updated_at": json.loads(out)["updated_at"],
        "size_bytes": store_path.stat().st_size,
    }


def test_set_get_dump_delete_flow(store_path, capsys):
    assert run(capsys, str(store_path), "set", "service.name", "api")[0] == EXIT_OK
    assert run(capsys, str(store_path), "set", "service.port", "8080", "--json")[0] == EXIT_OK
    assert run(capsys, str(store_path), "set", "tags", '["a","b"]', "--json")[0] == EXIT_OK

    code, out, _ = run(capsys, str(store_path), "get", "service.port")
    assert code == EXIT_OK
    assert json.loads(out) == 8080

    code, out, _ = run(capsys, str(store_path), "get", "tags.1")
    assert json.loads(out) == "b"

    code, out, _ = run(capsys, str(store_path), "dump")
    assert json.loads(out) == {"service": {"name": "api", "port": 8080}, "tags": ["a", "b"]}

    assert run(capsys, str(store_path), "delete", "service.name")[0] == EXIT_OK
    code, out, _ = run(capsys, str(store_path), "dump")
    assert json.loads(out) == {"service": {"port": 8080}, "tags": ["a", "b"]}
    assert json.loads(store_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_set_respects_existing_schema_version(store_path, capsys):
    AtomicJsonStore(store_path, schema_version=7).save({})
    assert run(capsys, str(store_path), "set", "k", "v")[0] == EXIT_OK
    assert json.loads(store_path.read_text(encoding="utf-8"))["schema_version"] == 7


def test_missing_key_exit_code_and_default(store_path, capsys):
    AtomicJsonStore(store_path).save({"a": 1})
    code, _, err = run(capsys, str(store_path), "get", "b")
    assert code == EXIT_MISSING
    assert "not found" in err
    code, out, _ = run(capsys, str(store_path), "get", "b", "--default", "null")
    assert code == EXIT_OK
    assert json.loads(out) is None
    code, _, err = run(capsys, str(store_path), "delete", "b")
    assert code == EXIT_MISSING


def test_missing_store_is_an_error_for_reads(store_path, capsys):
    code, _, err = run(capsys, str(store_path), "dump")
    assert code == EXIT_ERROR
    assert "does not exist" in err
    code, _, _ = run(capsys, str(store_path), "get", "a")
    assert code == EXIT_ERROR


def test_invalid_json_value_is_a_usage_error(store_path, capsys):
    code, _, err = run(capsys, str(store_path), "set", "k", "{broken", "--json")
    assert code == EXIT_USAGE
    assert "invalid JSON value" in err
    assert not store_path.exists()


def test_corrupt_store_is_an_error(store_path, capsys):
    store_path.write_text("nope", encoding="utf-8")
    code, _, err = run(capsys, str(store_path), "dump")
    assert code == EXIT_ERROR
    assert "not a readable" in err


def test_legacy_plain_file_can_be_edited(store_path, capsys):
    store_path.write_text(json.dumps({"plain": True}), encoding="utf-8")
    code, out, _ = run(capsys, str(store_path), "get", "plain")
    assert code == EXIT_OK
    assert json.loads(out) is True
    assert run(capsys, str(store_path), "set", "added", "1", "--json")[0] == EXIT_OK
    envelope = json.loads(store_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 0
    assert envelope["data"] == {"plain": True, "added": 1}


def test_path_helpers():
    data = {"a": {"b": [10, {"c": 1}]}}
    assert get_path(data, "a.b.1.c") == 1
    set_path(data, "a.b.0", 11)
    set_path(data, "x.y.z", "new")
    assert data["a"]["b"][0] == 11
    assert data["x"] == {"y": {"z": "new"}}
    delete_path(data, "x.y")
    assert data["x"] == {}
    for bad in ("", "a..b", "a.b.9", "a.b.0.c", "missing.key"):
        with pytest.raises(KeyPathError):
            get_path(data, bad)
    with pytest.raises(KeyPathError):
        set_path(data, "a.b.0.c", 1)
    with pytest.raises(KeyPathError):
        delete_path(data, "a.b.9")
