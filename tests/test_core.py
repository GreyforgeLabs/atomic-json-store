import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from atomic_json_store import (
    FORMAT,
    AtomicJsonStore,
    CorruptStoreError,
    LockTimeoutError,
    SchemaVersionError,
    StoreError,
)
from atomic_json_store import core as core_module


def read_envelope(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_file_yields_default_without_creating_it(store_path):
    store = AtomicJsonStore(store_path)
    assert store.load() == {}
    assert not store_path.exists()
    assert not store.exists()


def test_default_value_is_copied_not_shared(store_path):
    template = {"items": []}
    store = AtomicJsonStore(store_path, default=template)
    loaded = store.load()
    loaded["items"].append(1)
    assert template == {"items": []}


def test_default_callable_is_invoked(store_path):
    store = AtomicJsonStore(store_path, default=lambda: ["fresh"])
    assert store.load() == ["fresh"]


def test_save_and_load_roundtrip_with_envelope(store_path):
    store = AtomicJsonStore(store_path, schema_version=3)
    store.save({"name": "forge", "count": 2, "nested": {"ok": True}})
    assert store.load() == {"name": "forge", "count": 2, "nested": {"ok": True}}
    envelope = read_envelope(store_path)
    assert envelope["format"] == FORMAT
    assert envelope["schema_version"] == 3
    assert envelope["updated_at"].endswith("+00:00")
    assert set(envelope) == {"format", "schema_version", "updated_at", "data"}
    assert store_path.read_text(encoding="utf-8").endswith("\n")


def test_insertion_order_is_preserved_by_default(store_path):
    store = AtomicJsonStore(store_path)
    store.save({"zeta": 1, "alpha": 2})
    assert list(store.load()) == ["zeta", "alpha"]


def test_sort_keys_option(store_path):
    store = AtomicJsonStore(store_path, sort_keys=True)
    store.save({"zeta": 1, "alpha": 2})
    assert list(store.load()) == ["alpha", "zeta"]


def test_unicode_is_written_verbatim(store_path):
    store = AtomicJsonStore(store_path)
    store.save({"city": "Zürich", "mark": "✓"})
    assert "Zürich" in store_path.read_text(encoding="utf-8")
    assert store.load()["mark"] == "✓"


def test_update_with_in_place_mutation(store_path):
    store = AtomicJsonStore(store_path, default=lambda: {"n": 0})

    def bump(data):
        data["n"] += 1

    assert store.update(bump) == {"n": 1}
    assert store.update(bump) == {"n": 2}
    assert store.load() == {"n": 2}


def test_update_with_replacement_document(store_path):
    store = AtomicJsonStore(store_path)
    store.save([1, 2])
    assert store.update(lambda data: data + [3]) == [1, 2, 3]
    assert store.load() == [1, 2, 3]


def test_transaction_commits_on_clean_exit(store_path):
    store = AtomicJsonStore(store_path)
    with store.transaction() as data:
        data["written"] = True
    assert store.load() == {"written": True}


def test_transaction_discards_on_exception(store_path):
    store = AtomicJsonStore(store_path)
    store.save({"keep": 1})
    with pytest.raises(RuntimeError), store.transaction() as data:
        data["keep"] = 2
        raise RuntimeError("abort")
    assert store.load() == {"keep": 1}


def test_get_and_set_helpers(store_path):
    store = AtomicJsonStore(store_path)
    assert store.get("missing", "fallback") == "fallback"
    store.set("token", "abc")
    assert store.get("token") == "abc"


def test_get_and_set_reject_non_mapping_documents(store_path):
    store = AtomicJsonStore(store_path, default=list)
    with pytest.raises(TypeError):
        store.get("x")
    with pytest.raises(TypeError):
        store.set("x", 1)


def test_reset_restores_default(store_path):
    store = AtomicJsonStore(store_path, default=lambda: {"fresh": True})
    store.save({"fresh": False, "extra": 1})
    assert store.reset() == {"fresh": True}
    assert store.load() == {"fresh": True}


def test_failed_replace_leaves_original_and_no_temp_files(store_path, monkeypatch):
    store = AtomicJsonStore(store_path)
    store.save({"v": 1})

    def explode(src, dst):
        raise OSError("disk went away")

    monkeypatch.setattr(core_module.os, "replace", explode)
    with pytest.raises(OSError):
        store.save({"v": 2})
    assert read_envelope(store_path)["data"] == {"v": 1}
    leftovers = [p for p in store_path.parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_unserializable_data_never_touches_disk(store_path):
    store = AtomicJsonStore(store_path)
    store.save({"v": 1})
    before = store_path.stat().st_mtime_ns
    with pytest.raises(TypeError):
        store.save({"bad": object()})
    assert store_path.stat().st_mtime_ns == before
    assert store.load() == {"v": 1}
    leftovers = [p for p in store_path.parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_custom_encoder(store_path):
    class PathEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, Path):
                return str(o)
            return super().default(o)

    store = AtomicJsonStore(store_path, encoder=PathEncoder)
    store.save({"p": Path("/tmp/x")})
    assert store.load() == {"p": "/tmp/x"}


def test_new_file_is_private_and_existing_mode_is_preserved(store_path):
    store = AtomicJsonStore(store_path)
    store.save({})
    assert store_path.stat().st_mode & 0o777 == 0o600
    os.chmod(store_path, 0o644)
    store.save({"again": True})
    assert store_path.stat().st_mode & 0o777 == 0o644


def test_explicit_file_mode(store_path):
    store = AtomicJsonStore(store_path, file_mode=0o640)
    store.save({})
    assert store_path.stat().st_mode & 0o777 == 0o640


def test_parent_directories_are_created(tmp_path):
    path = tmp_path / "deep" / "er" / "state.json"
    AtomicJsonStore(path).save({"ok": 1})
    assert path.exists()


def test_corrupt_file_raises_by_default(store_path):
    store_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CorruptStoreError):
        AtomicJsonStore(store_path).load()
    assert store_path.read_text(encoding="utf-8") == "{not json"


def test_envelope_missing_fields_is_corrupt(store_path):
    store_path.write_text(json.dumps({"format": FORMAT, "schema_version": 1}), encoding="utf-8")
    with pytest.raises(CorruptStoreError):
        AtomicJsonStore(store_path).load()
    store_path.write_text(
        json.dumps({"format": FORMAT, "schema_version": "1", "data": {}}), encoding="utf-8"
    )
    with pytest.raises(CorruptStoreError):
        AtomicJsonStore(store_path).load()


def test_quarantine_policy_moves_bad_file_aside(store_path):
    store_path.write_text("garbage", encoding="utf-8")
    store = AtomicJsonStore(store_path, on_corrupt="quarantine", default=lambda: {"fresh": 1})
    assert store.load() == {"fresh": 1}
    quarantined = list(store_path.parent.glob("state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "garbage"
    assert read_envelope(store_path)["data"] == {"fresh": 1}


def test_legacy_plain_json_is_version_zero(store_path):
    store_path.write_text(json.dumps({"legacy": True}), encoding="utf-8")
    with pytest.raises(SchemaVersionError, match="0 -> 1"):
        AtomicJsonStore(store_path, schema_version=1).load()
    adopted = AtomicJsonStore(store_path, schema_version=0)
    assert adopted.load() == {"legacy": True}
    assert not AtomicJsonStore(store_path).info().format
    adopted.save({"legacy": True, "wrapped": True})
    assert read_envelope(store_path)["schema_version"] == 0


def test_migrations_run_in_order_and_persist(store_path):
    AtomicJsonStore(store_path, schema_version=1).save({"name": "a"})
    calls = []

    def to_v2(data):
        calls.append(2)
        return {"name": data["name"], "tags": []}

    def to_v3(data):
        calls.append(3)
        data["version_seen"] = 3
        return data

    store = AtomicJsonStore(store_path, schema_version=3, migrations={1: to_v2, 2: to_v3})
    assert store.load() == {"name": "a", "tags": [], "version_seen": 3}
    assert calls == [2, 3]
    assert read_envelope(store_path)["schema_version"] == 3
    store.load()
    assert calls == [2, 3]


def test_migration_from_legacy_plain_file(store_path):
    store_path.write_text(json.dumps({"legacy": True}), encoding="utf-8")
    store = AtomicJsonStore(store_path, schema_version=1, migrations={0: lambda d: {"wrapped": d}})
    assert store.load() == {"wrapped": {"legacy": True}}
    assert read_envelope(store_path)["format"] == FORMAT


def test_missing_migration_step_is_an_error(store_path):
    AtomicJsonStore(store_path, schema_version=1).save({})
    store = AtomicJsonStore(store_path, schema_version=3, migrations={1: lambda d: d})
    with pytest.raises(SchemaVersionError, match="2 -> 3"):
        store.load()
    assert read_envelope(store_path)["schema_version"] == 1


def test_migration_returning_none_is_an_error(store_path):
    AtomicJsonStore(store_path, schema_version=1).save({})
    store = AtomicJsonStore(store_path, schema_version=2, migrations={1: lambda d: None})
    with pytest.raises(SchemaVersionError, match="returned None"):
        store.load()


def test_newer_file_version_is_refused(store_path):
    AtomicJsonStore(store_path, schema_version=5).save({"future": True})
    old = AtomicJsonStore(store_path, schema_version=2)
    with pytest.raises(SchemaVersionError, match="newer"):
        old.load()
    with pytest.raises(SchemaVersionError):
        old.update(lambda d: d)
    assert read_envelope(store_path)["schema_version"] == 5


def test_update_migrates_before_applying(store_path):
    AtomicJsonStore(store_path, schema_version=1).save({"n": 1})
    store = AtomicJsonStore(
        store_path, schema_version=2, migrations={1: lambda d: {"n": d["n"], "m": 0}}
    )

    def bump(data):
        data["m"] += 1

    assert store.update(bump) == {"n": 1, "m": 1}


def test_info_reports_metadata_without_migrating(store_path):
    store = AtomicJsonStore(store_path)
    info = store.info()
    assert not info.exists
    assert info.schema_version is None
    store.save({"a": 1})
    info = AtomicJsonStore(store_path, schema_version=9).info()
    assert info.exists
    assert info.format == FORMAT
    assert info.schema_version == 1
    assert info.updated_at
    assert info.size_bytes == store_path.stat().st_size
    assert read_envelope(store_path)["schema_version"] == 1


def test_info_does_not_create_parent_or_lock(tmp_path):
    path = tmp_path / "uncreated" / "state.json"
    info = AtomicJsonStore(path).info()
    assert not info.exists
    assert not path.parent.exists()

    path.parent.mkdir()
    AtomicJsonStore(path).save({"ready": True})
    lock_path = path.with_name(path.name + ".lock")
    lock_path.unlink()
    assert AtomicJsonStore(path).info().exists
    assert not lock_path.exists()


def test_info_on_corrupt_file(store_path):
    store_path.write_text("nope", encoding="utf-8")
    info = AtomicJsonStore(store_path).info()
    assert info.exists
    assert info.format is None
    assert info.schema_version is None


def test_constructor_validation(store_path):
    with pytest.raises(TypeError):
        AtomicJsonStore(store_path, schema_version="1")
    with pytest.raises(ValueError):
        AtomicJsonStore(store_path, schema_version=-1)
    with pytest.raises(ValueError):
        AtomicJsonStore(store_path, lock_timeout=-1)
    with pytest.raises(ValueError):
        AtomicJsonStore(store_path, on_corrupt="ignore")
    with pytest.raises(TypeError):
        AtomicJsonStore(store_path, migrations={"1": lambda d: d})
    with pytest.raises(TypeError):
        AtomicJsonStore(store_path, migrations={1: "not callable"})


def test_lock_file_lives_beside_the_store(store_path):
    store = AtomicJsonStore(store_path)
    assert store.lock_path == store_path.with_name("state.json.lock")
    store.save({})
    assert store.lock_path.exists()


def test_reentrant_lock_within_a_thread(store_path):
    store = AtomicJsonStore(store_path, default=lambda: {"n": 0})

    def outer(data):
        data["n"] += 1
        data["inner"] = store.load()["n"]

    assert store.update(outer) == {"n": 1, "inner": 0}


def test_shared_lock_cannot_upgrade_to_exclusive(store_path):
    store = AtomicJsonStore(store_path)
    store.save({})
    with store._lock.held(exclusive=False), pytest.raises(StoreError):
        store.save({"x": 1})


def test_lock_timeout_when_another_thread_holds_the_lock(store_path):
    store = AtomicJsonStore(store_path, lock_timeout=0.2)
    store.save({})
    holder = AtomicJsonStore(store_path)
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with holder._lock.held(exclusive=True):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    entered.wait(5)
    started = time.monotonic()
    try:
        with pytest.raises(LockTimeoutError):
            store.save({"blocked": True})
        assert time.monotonic() - started >= 0.2
    finally:
        release.set()
        thread.join()
    assert store.load() == {}


def test_zero_timeout_fails_fast(store_path):
    store = AtomicJsonStore(store_path, lock_timeout=0)
    holder = AtomicJsonStore(store_path)
    with holder._lock.held(exclusive=True):
        with pytest.raises(LockTimeoutError):
            store.save({})


def test_threads_never_lose_increments(store_path):
    store = AtomicJsonStore(store_path, default=lambda: {"n": 0}, fsync=False)
    threads = 8
    rounds = 40

    def bump(data):
        data["n"] += 1

    def worker():
        for _ in range(rounds):
            store.update(bump)

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    assert store.load() == {"n": threads * rounds}


WORKER = """
import sys
from atomic_json_store import AtomicJsonStore
store = AtomicJsonStore(sys.argv[1], default=lambda: {"n": 0}, lock_timeout=60, fsync=False)
def bump(data):
    data["n"] += 1
for _ in range(int(sys.argv[2])):
    store.update(bump)
"""


def test_processes_never_lose_increments(store_path):
    processes = 6
    rounds = 30
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(store_path), str(rounds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(processes)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode()
    assert AtomicJsonStore(store_path).load() == {"n": processes * rounds}


def test_reader_sees_only_complete_documents_during_concurrent_writes(store_path):
    store = AtomicJsonStore(store_path, fsync=False)
    store.save({"seq": 0, "payload": "x" * 20000})
    stop = threading.Event()
    problems = []

    def writer():
        seq = 1
        while not stop.is_set():
            store.save({"seq": seq, "payload": "x" * 20000})
            seq += 1

    def reader():
        while not stop.is_set():
            try:
                data = store.load()
                if len(data["payload"]) != 20000:
                    problems.append("truncated")
            except Exception as exc:  # noqa: BLE001 - any failure is a defect here
                problems.append(repr(exc))

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    time.sleep(0.5)
    stop.set()
    for thread in threads:
        thread.join()
    assert problems == []
