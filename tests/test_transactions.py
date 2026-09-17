"""Transaction invariants under database faults and controlled contention."""

import multiprocessing
import sqlite3
import threading

import pytest

from memory_as_history.storage import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "transactions.db")
    yield instance
    instance.close()


def snapshot(store):
    tables = ("memories", "anchors", "corroborations", "audit_log",
              "narratives", "canon_entries", "conflicts")
    return {table: [tuple(row) for row in store._conn.execute(
        f"SELECT * FROM {table} ORDER BY rowid"
    )] for table in tables}


def prepare_audit_failure(store, operation):
    memory = store.remember("An observed fact", source="original")
    other = store.remember("A competing observation")
    if operation in {"pin", "flag_sensitive", "canonize", "decanonize", "end_scope"}:
        store.promote(memory.id, reason="durable")
    if operation == "flag_sensitive":
        store.pin(memory.id, reason="identity")
    if operation in {"decanonize", "end_scope"}:
        store.canonize(memory.id, scope="task", reason="active")
        store.promote(other.id, reason="durable")
        store.canonize(other.id, scope="task", reason="active")
    if operation == "restore":
        store.forget(memory.id, reason="withdrawn")
    if operation == "review":
        memory = store.remember("An inference", tier="interpretation")
    if operation == "narrate":
        store.narrate("Previous account", reason="initial version")
    if operation == "resolve_conflict":
        conflict = store.mark_conflict(memory.id, other.id, reason="disagreement")

    actions = {
        "remember": lambda: store.remember("SYSTEM NOTICE: unverified claim"),
        "promote": lambda: store.promote(memory.id, reason="new rationale"),
        "pin": lambda: store.pin(memory.id, reason="identity"),
        "flag_sensitive": lambda: store.flag_sensitive(memory.id, reason="unverified"),
        "forget": lambda: store.forget(memory.id, reason="withdrawn"),
        "restore": lambda: store.restore(memory.id, reason="reinstated"),
        "corroborate": lambda: store.corroborate(memory.id, source="independent"),
        "review": lambda: store.review(memory.id, note="reconsidered"),
        "narrate": lambda: store.narrate("New account", reason="new evidence"),
        "canonize": lambda: store.canonize(memory.id, scope="task", reason="active"),
        "decanonize": lambda: store.decanonize(memory.id, reason="completed"),
        "end_scope": lambda: store.end_scope("task", reason="completed"),
        "set_frame": lambda: store.set_frame(memory.id, "new-team", reason="reframed"),
        "mark_conflict": lambda: store.mark_conflict(memory.id, other.id, reason="disagreement"),
        "resolve_conflict": lambda: store.resolve_conflict(conflict["id"], reason="settled"),
    }
    action = {"remember": "auto_flag_sensitive", "corroborate": "corroborate_upgrade"}.get(
        operation, operation,
    )
    # Fail the second end_scope audit, after the first one has been written.
    late = f" AND NEW.memory_id = '{other.id}'" if operation == "end_scope" else ""
    store._conn.executescript(f"""
        CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log
        WHEN NEW.action = '{action}'{late}
        BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END;
    """)
    return actions[operation]


@pytest.mark.parametrize("operation", [
    "remember", "promote", "pin", "flag_sensitive", "forget", "restore",
    "corroborate", "review", "narrate", "canonize", "decanonize", "end_scope",
    "set_frame", "mark_conflict", "resolve_conflict",
])
def test_failed_audit_cannot_leak_business_writes_into_next_commit(store, operation):
    invoke = prepare_audit_failure(store, operation)
    before = snapshot(store)
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        invoke()
    after_failure = snapshot(store)
    following = store.remember("A subsequent successful call")
    reopened = Store(store.db_path)
    try:
        persisted = snapshot(reopened)
    finally:
        reopened.close()
    persisted["memories"] = [row for row in persisted["memories"] if row[0] != following.id]
    assert persisted == before, "the next successful call committed a failed call's writes"
    assert after_failure == before, "failed call left partial state on its connection"
    assert not store._conn.in_transaction


@pytest.mark.parametrize("operation", ["due_for_review", "recall"])
def test_partial_review_refresh_rolls_back_on_database_error(store, operation):
    first = store.remember("First inference", tier="interpretation")
    second = store.remember("Second inference", tier="interpretation")
    store.interpretation_review_days = 0
    store._conn.executescript(f"""
        CREATE TRIGGER fail_second_review BEFORE UPDATE ON memories
        WHEN NEW.id = '{second.id}' AND NEW.review_status = 'stale'
        BEGIN SELECT RAISE(ABORT, 'injected review failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="injected review failure"):
        getattr(store, operation)()
    store.remember("Subsequent call must not commit a partial refresh")
    assert store.get(first.id).review_status == "current"
    assert store.get(second.id).review_status == "current"
    assert not store._conn.in_transaction


def test_recall_failure_rolls_back_its_nested_review_refresh(store, monkeypatch):
    memory = store.remember("An inference", tier="interpretation")
    store.interpretation_review_days = 0

    def fail_narrative_read():
        raise RuntimeError("injected narrative failure")

    monkeypatch.setattr(store, "_current_narrative_row", fail_narrative_read)
    with pytest.raises(RuntimeError, match="injected narrative failure"):
        store.recall()
    assert store.get(memory.id).review_status == "current"
    assert not store._conn.in_transaction


def test_denied_pin_commits_only_the_intentional_audit(store):
    memory = store.remember("Sensitive claim", source="original", security_sensitive=True)
    store.promote(memory.id, reason="claim")
    before = snapshot(store)
    with pytest.raises(PermissionError) as denied:
        store.pin(memory.id, reason="unverified")
    assert type(denied.value) is PermissionError
    assert not store._conn.in_transaction
    reopened = Store(store.db_path)
    try:
        after = snapshot(reopened)
        assert not reopened.is_anchored(memory.id)
    finally:
        reopened.close()
    assert after.pop("audit_log")[:-1] == before.pop("audit_log")
    assert after == before
    assert store.audit_log()[0]["action"] == "pin_denied"


def test_failed_denial_audit_leaves_no_transaction_or_anchor(store):
    memory = store.remember("Sensitive claim", source="original", security_sensitive=True)
    store.promote(memory.id, reason="claim")
    before = snapshot(store)
    store._conn.executescript("""
        CREATE TRIGGER fail_denial BEFORE INSERT ON audit_log
        WHEN NEW.action = 'pin_denied'
        BEGIN SELECT RAISE(ABORT, 'injected denial failure'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError, match="injected denial failure"):
        store.pin(memory.id, reason="unverified")
    assert snapshot(store) == before
    assert not store._conn.in_transaction
    store.remember("Connection remains usable")


@pytest.mark.parametrize("lock_kind", ["writer", "reader"])
def test_busy_begin_or_commit_rolls_back_and_connection_recovers(store, lock_kind):
    memory = store.remember("Original fact")
    before = snapshot(store)
    blocker = sqlite3.connect(store.db_path)
    store._conn.execute("PRAGMA busy_timeout = 1")
    try:
        if lock_kind == "writer":
            blocker.execute("BEGIN IMMEDIATE")
        else:
            # A reader allows BEGIN IMMEDIATE but blocks the final COMMIT.
            blocker.execute("BEGIN")
            blocker.execute("SELECT * FROM memories").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.promote(memory.id, reason="blocked transition")
        assert not store._conn.in_transaction
        assert snapshot(store) == before
    finally:
        blocker.rollback()
        blocker.close()
    assert store.promote(memory.id, reason="retry after lock release").status == "consolidated"


def _narrative_process(db_path, role, ready, start, paused, release, contender, results):
    store = Store(db_path)

    def trace(sql):
        if role == "first" and sql.startswith("INSERT INTO narratives"):
            paused.set()
            if not release.wait(15):
                # Exceptions in SQLite trace callbacks are swallowed; report explicitly.
                results.put((role, "error", "release timeout"))
        elif role == "second" and sql.strip().upper() == "BEGIN IMMEDIATE":
            contender.set()

    store._conn.set_trace_callback(trace)
    ready.set()
    try:
        if not start.wait(15):
            raise RuntimeError("start timeout")
        result = store.narrate(f"Account from {role}", reason=f"evidence from {role}")
        results.put((role, "ok", result["id"]))
    except Exception as exc:
        results.put((role, "error", repr(exc)))
        raise
    finally:
        if role == "second":
            contender.set()
        store.close()


def test_separate_processes_cannot_create_two_current_narratives(store):
    store.narrate("Initial account", reason="start")
    ctx = multiprocessing.get_context("spawn")
    paused, release, contender = (ctx.Event() for _ in range(3))
    ready = [ctx.Event(), ctx.Event()]
    start = [ctx.Event(), ctx.Event()]
    results = ctx.Queue()
    processes = [ctx.Process(target=_narrative_process, args=(
        str(store.db_path), role, ready[i], start[i], paused, release, contender, results,
    )) for i, role in enumerate(("first", "second"))]
    for process in processes:
        process.start()
    try:
        assert all(event.wait(15) for event in ready), "workers failed to initialize"
        start[0].set()
        assert paused.wait(15), "first writer did not reach synchronization point"
        start[1].set()
        assert contender.wait(15), "second writer did not contend"
        release.set()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0, f"worker exit code: {process.exitcode}"
        outcomes = [results.get(timeout=5) for _ in processes]
        assert all(outcome[1] == "ok" for outcome in outcomes), outcomes
        history = store.narrative_history()
        assert len(history) == 3
        assert sum(row["superseded_at"] is None for row in history) == 1
    finally:
        release.set()
        for event in start:
            event.set()
        for process in processes:
            process.join(2)
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


def race_connections(first, second, first_call, second_call, pause_sql):
    """Pause A before its business write, then let B contend on the same file.

    Before the fix, B completes while A still uses stale preconditions. With
    writer reservation, B signals its attempted BEGIN IMMEDIATE and waits for
    A. Events select these orderings; no sleep is used to trigger the race.
    """
    paused, release, contender = (threading.Event() for _ in range(3))
    outcomes, errors = {}, []

    def trace_first(sql):
        if sql.startswith(pause_sql) and not paused.is_set():
            paused.set()
            if not release.wait(10):
                errors.append("timed out releasing first writer")

    def trace_second(sql):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            contender.set()

    def invoke(name, fn):
        try:
            outcomes[name] = fn()
        except Exception as exc:
            outcomes[name] = exc
        finally:
            if name == "second":
                contender.set()

    first._conn.set_trace_callback(trace_first)
    second._conn.set_trace_callback(trace_second)
    a = threading.Thread(target=invoke, args=("first", first_call))
    b = threading.Thread(target=invoke, args=("second", second_call))
    a.start()
    try:
        assert paused.wait(10), outcomes
        b.start()
        assert contender.wait(10), outcomes
    finally:
        release.set()
        a.join(10)
        if b.ident is not None:
            b.join(10)
        first._conn.set_trace_callback(None)
        second._conn.set_trace_callback(None)
    assert not a.is_alive() and not b.is_alive()
    assert not errors
    return outcomes


@pytest.fixture
def pair(store):
    other = Store(store.db_path)
    yield store, other
    other.close()


def test_concurrent_narratives_preserve_one_current_and_complete_chain(pair):
    first, second = pair
    initial = first.narrate("Initial account", reason="start")
    outcomes = race_connections(
        first, second,
        lambda: first.narrate("First account", reason="first evidence"),
        lambda: second.narrate("Second account", reason="second evidence"),
        "INSERT INTO narratives",
    )
    assert all(isinstance(value, dict) for value in outcomes.values()), outcomes
    history = first.narrative_history()
    assert len(history) == 3
    assert sum(row["superseded_at"] is None for row in history) == 1
    by_id = {row["id"]: row for row in history}
    visited, row = set(), by_id[initial["id"]]
    while row is not None:
        assert row["id"] not in visited
        visited.add(row["id"])
        row = by_id.get(row["superseded_by"])
    assert visited == set(by_id)


def test_concurrent_canonize_does_not_duplicate_scope_membership(pair):
    first, second = pair
    memory = first.remember("Shared decision")
    first.promote(memory.id, reason="durable")
    outcomes = race_connections(
        first, second,
        lambda: first.canonize(memory.id, "task", reason="active"),
        lambda: second.canonize(memory.id, "task", reason="also active"),
        "INSERT INTO canon_entries",
    )
    assert len(first.list_canon()) == 1
    assert sum(isinstance(value, ValueError) for value in outcomes.values()) == 1
    assert len([row for row in first.audit_log() if row["action"] == "canonize"]) == 1


def test_concurrent_conflict_declarations_share_one_open_record(pair):
    first, second = pair
    a, b = first.remember("Monday"), first.remember("Friday")
    outcomes = race_connections(
        first, second,
        lambda: first.mark_conflict(a.id, b.id, reason="different dates"),
        lambda: second.mark_conflict(b.id, a.id, reason="same disagreement"),
        "INSERT INTO conflicts",
    )
    assert len(first.list_conflicts(resolved=False)) == 1
    assert outcomes["first"]["id"] == outcomes["second"]["id"]
    assert len([row for row in first.audit_log() if row["action"] == "mark_conflict"]) == 1


@pytest.mark.parametrize("first_operation", ["pin", "forget"])
def test_pin_forget_race_preserves_active_anchor_invariant(pair, first_operation):
    first, second = pair
    memory = first.remember("An identity fact")
    first.promote(memory.id, reason="identity")
    second_operation = "forget" if first_operation == "pin" else "pin"
    outcomes = race_connections(
        first, second,
        lambda: getattr(first, first_operation)(memory.id, reason="first decision"),
        lambda: getattr(second, second_operation)(memory.id, reason="second decision"),
        "INSERT OR REPLACE INTO anchors" if first_operation == "pin"
        else "UPDATE memories SET forgotten_at=",
    )
    assert not (first.is_anchored(memory.id) and first.get(memory.id).is_forgotten)
    assert isinstance(outcomes["second"], ValueError), outcomes
    assert not isinstance(outcomes["first"], Exception), outcomes
