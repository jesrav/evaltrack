"""The `ObjectStore` contract, held identically by every backend."""

import pytest

from evaltrack.repositories.store import ObjectNotFoundError

from .helpers import StoreFactory, race

# --- ObjectStore contract ---


def test_verify_available_on_a_fresh_store_is_a_noop(
    store_factory: StoreFactory,
) -> None:
    """Availability must not require existing data. A local store creates its
    storage on the first write, so a fresh one is available already. The
    `azure` parametrization checks the live shared container instead."""
    store_factory().verify_available()


def test_verify_writable_on_a_fresh_store_is_a_noop(
    store_factory: StoreFactory,
) -> None:
    """A fresh store must never be flagged as unwritable. Local stores create
    their storage on the first write, and remote backends skip the check,
    because the check must not do network I/O."""
    store_factory().verify_writable()


def test_write_then_read(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"hello", "a/b.txt")
    assert store.read("a/b.txt") == b"hello"


def test_read_missing_raises_not_found(store_factory: StoreFactory) -> None:
    store = store_factory()
    with pytest.raises(ObjectNotFoundError):
        store.read("missing.txt")


def test_a_second_write_replaces(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"first", "a.txt")
    store.write(b"second", "a.txt")
    assert store.read("a.txt") == b"second"
    assert list(store.list("")) == ["a.txt"], "a replace must leave no extra keys"


def test_write_leaves_only_the_final_key(store_factory: StoreFactory) -> None:
    """A write publishes exactly its key. No staging or temp artifact is
    listed."""
    store = store_factory()
    store.write(b"hello", "dir/a.txt")
    assert list(store.list("")) == ["dir/a.txt"]


def test_append_creates_when_absent(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.append(b"line1\n", "log.jsonl")
    assert store.read("log.jsonl") == b"line1\n"


def test_append_extends_existing(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.append(b"line1\n", "log.jsonl")
    store.append(b"line2\n", "log.jsonl")
    store.append(b"line3\n", "log.jsonl")
    assert store.read("log.jsonl") == b"line1\nline2\nline3\n"


def test_append_large_content_round_trips(store_factory: StoreFactory) -> None:
    """Appends far above any single-write(2) size must land in full (no dropped
    bytes on a short write)."""
    store = store_factory()
    payload = bytes(range(256)) * 4096  # 1 MiB
    store.append(b"start\n", "log.jsonl")
    store.append(payload, "log.jsonl")
    assert store.read("log.jsonl") == b"start\n" + payload


def test_concurrent_appends_to_a_fresh_key_lose_no_entry(
    store_factory: StoreFactory,
) -> None:
    """Every concurrent append must land. Append is atomic and no write is lost.

    The fresh key matters. The create of the object on the first append is
    where one backend's create can race another writer's and drop an entry. The
    test runs several rounds of several writers, because that window is only
    reached on the right scheduling. test_azure_store.py replays the known Azure
    window exactly.
    """
    store = store_factory()
    writers, rounds = 8, 4
    for r in range(rounds):
        key = f"log-{r}.jsonl"
        race(
            *[
                lambda i=i, key=key: store.append(f"entry-{i}\n".encode(), key)
                for i in range(writers)
            ]
        )
        lines = store.read(key).decode().splitlines()
        assert sorted(lines) == sorted(f"entry-{i}" for i in range(writers)), (
            f"an append was lost or duplicated in round {r}"
        )


def test_replacing_an_appended_object_takes_a_delete_first(
    store_factory: StoreFactory,
) -> None:
    """The portable way to replace an appended object, and the only one.

    A key belongs to the mode that created it, so a store can refuse a `write`
    over an appended object. A delete first is what every store agrees on.
    Repairing a torn reflog needs this.
    """
    store = store_factory()
    store.append(b"first\n", "log.jsonl")
    store.delete("log.jsonl")
    store.write(b"replaced\n", "log.jsonl")
    assert store.read("log.jsonl") == b"replaced\n"


def test_an_appended_object_reads_back_through_read(
    store_factory: StoreFactory,
) -> None:
    """`append` and `write` can store different underlying object types, so a
    backend could serve one through `read` and not the other."""
    store = store_factory()
    store.append(b"one\n", "log.jsonl")
    store.append(b"two\n", "log.jsonl")
    assert store.read("log.jsonl") == b"one\ntwo\n"


def test_delete_removes(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"x", "a.txt")
    store.delete("a.txt")
    with pytest.raises(ObjectNotFoundError):
        store.read("a.txt")


def test_delete_missing_raises_not_found(store_factory: StoreFactory) -> None:
    store = store_factory()
    with pytest.raises(ObjectNotFoundError):
        store.delete("missing.txt")


def test_list_returns_matching_prefix(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"1", "runs/a.json")
    store.write(b"2", "runs/b.json")
    store.write(b"3", "refs/baseline.json")
    assert list(store.list("runs/")) == ["runs/a.json", "runs/b.json"]


def test_list_empty_for_no_matches(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"x", "a.txt")
    assert list(store.list("nonexistent/")) == []


def test_list_recurses_into_subdirs(store_factory: StoreFactory) -> None:
    store = store_factory()
    store.write(b"1", "refs/pr/123.json")
    store.write(b"2", "refs/pr/456.json")
    store.write(b"3", "refs/baseline.json")
    assert sorted(store.list("refs/")) == [
        "refs/baseline.json",
        "refs/pr/123.json",
        "refs/pr/456.json",
    ]
