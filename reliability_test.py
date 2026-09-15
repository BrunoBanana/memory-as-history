#!/usr/bin/env python3
"""Reliability / robustness test battery for memory_as_history.storage.Store.

Covers:
  1. Thread-safety (concurrent calls against a single Store instance)
  2. Multi-process concurrency (two Store instances, same sqlite file)
  3. Persistence across process/connection restarts
  4. Scale (bulk inserts + recall performance)
  5. Edge cases (long content, unicode, SQL-injection-shaped strings,
     negative/zero limits, nonexistent ids, empty strings)

Prints a PASS/FAIL summary per test. Non-zero exit code if any test fails.
"""
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from memory_as_history.storage import Store  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def test_thread_safety(tmpdir):
    """Many threads hammering ONE Store instance concurrently."""
    db = os.path.join(tmpdir, "thread.db")
    store = Store(db)
    errors = []
    N_THREADS = 20
    N_OPS = 25

    def worker(i):
        try:
            for j in range(N_OPS):
                m = store.remember(f"thread {i} memory {j}", source=f"t{i}")
                store.promote(m.id, reason=f"promoted by thread {i}")
                store.recall(limit=5)
        except Exception as e:
            errors.append((i, repr(e)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = N_THREADS * N_OPS
    actual = store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    ok = not errors and actual == expected
    detail = f"errors={len(errors)} expected_rows={expected} actual_rows={actual}"
    if errors[:3]:
        detail += f" sample_errors={errors[:3]}"
    record("thread_safety_single_store", ok, detail)
    store.close()


def _mp_worker(db_path, idx, n_ops, err_queue):
    try:
        store = Store(db_path)
        for j in range(n_ops):
            m = store.remember(f"proc {idx} memory {j}")
            store.promote(m.id, reason=f"promoted by proc {idx}")
        store.close()
    except Exception as e:
        err_queue.put((idx, repr(e)))


def test_multiprocess_concurrency(tmpdir):
    """Multiple OS processes, each with its own sqlite3 connection, same file."""
    db = os.path.join(tmpdir, "multiproc.db")
    Store(db).close()  # create schema first

    N_PROCS = 6
    N_OPS = 15
    err_queue = multiprocessing.Queue()
    procs = [
        multiprocessing.Process(target=_mp_worker, args=(db, i, N_OPS, err_queue))
        for i in range(N_PROCS)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)

    errors = []
    while not err_queue.empty():
        errors.append(err_queue.get())

    store = Store(db)
    actual = store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    store.close()
    expected = N_PROCS * N_OPS
    ok = actual == expected
    detail = f"errors={errors[:5]} expected_rows={expected} actual_rows={actual}"
    record("multiprocess_concurrency_same_sqlite_file", ok, detail)


def test_persistence_across_restart(tmpdir):
    db = os.path.join(tmpdir, "persist.db")
    s1 = Store(db)
    m = s1.remember("persisted fact")
    s1.promote(m.id, reason="persistence test")
    s1.pin(m.id, reason="anchor persistence test")
    s1.close()

    s2 = Store(db)  # simulate process restart: fresh connection, same file
    fetched = s2.get(m.id)
    anchors = s2.list_anchors()
    ok = (
        fetched is not None
        and fetched.status == "consolidated"
        and any(a["id"] == m.id for a in anchors)
    )
    detail = f"fetched={fetched.to_dict() if fetched else None} anchors_count={len(anchors)}"
    record("persistence_across_connection_restart", ok, detail)
    s2.close()


def test_scale(tmpdir):
    db = os.path.join(tmpdir, "scale.db")
    store = Store(db)
    N = 5000
    t0 = time.time()
    ids = []
    for i in range(N):
        m = store.remember(f"bulk memory number {i} with some searchable_token_{i % 100}")
        ids.append(m.id)
        if i % 500 == 0:
            store.promote(m.id, reason="bulk promoted sample")
    insert_time = time.time() - t0

    t1 = time.time()
    result = store.recall(query="searchable_token_42", limit=20)
    recall_time = time.time() - t1

    count = store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    ok = count == N and recall_time < 2.0 and len(result["memories"]) > 0
    detail = (
        f"n={N} insert_time={insert_time:.2f}s recall_time={recall_time:.3f}s "
        f"count={count} recall_hits={len(result['memories'])}"
    )
    record("scale_5000_memories_and_recall_perf", ok, detail)
    store.close()


def test_edge_cases(tmpdir):
    db = os.path.join(tmpdir, "edge.db")
    store = Store(db)
    failures = []

    # 1. Very long content (1MB)
    try:
        long_content = "x" * (1024 * 1024)
        m = store.remember(long_content)
        fetched = store.get(m.id)
        if fetched is None or len(fetched.content) != len(long_content):
            failures.append("long_content_roundtrip_mismatch")
    except Exception as e:
        failures.append(f"long_content_exception: {e!r}")

    # 2. Unicode / emoji / RTL text
    try:
        unicode_content = "记忆 🧠 مرحبا שלום café naïve Ω≈ç√∫"
        m = store.remember(unicode_content)
        fetched = store.get(m.id)
        if fetched is None or fetched.content != unicode_content:
            failures.append("unicode_roundtrip_mismatch")
    except Exception as e:
        failures.append(f"unicode_exception: {e!r}")

    # 3. SQL-injection-shaped content (should be safely parameterized)
    try:
        injection = "'; DROP TABLE memories; --"
        m = store.remember(injection)
        # table should still exist and be queryable
        count = store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fetched = store.get(m.id)
        if fetched is None or fetched.content != injection or count < 1:
            failures.append("sql_injection_shaped_content_broke_table")
    except Exception as e:
        failures.append(f"sql_injection_exception: {e!r}")

    # 4. Injection via reason field of promote()
    try:
        m = store.remember("target for injection via reason")
        store.promote(m.id, reason="x'); DELETE FROM memories WHERE '1'='1")
        count_after = store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if count_after < 1:
            failures.append("sql_injection_via_reason_deleted_rows")
    except Exception as e:
        failures.append(f"injection_via_reason_exception: {e!r}")

    # 5. promote() with empty/whitespace reason -> should raise ValueError
    try:
        m = store.remember("needs a reason")
        try:
            store.promote(m.id, reason="   ")
            failures.append("promote_empty_reason_did_not_raise")
        except ValueError:
            pass
    except Exception as e:
        failures.append(f"promote_empty_reason_unexpected_exception: {e!r}")

    # 6. promote() on nonexistent id -> KeyError, not crash
    try:
        try:
            store.promote("nonexistent-id-1234", reason="x")
            failures.append("promote_nonexistent_id_did_not_raise")
        except KeyError:
            pass
    except Exception as e:
        failures.append(f"promote_nonexistent_unexpected_exception: {e!r}")

    # 7. recall() with negative / zero limit -> should not crash
    try:
        r0 = store.recall(limit=0)
        rneg = store.recall(limit=-5)
        if not isinstance(r0, dict) or not isinstance(rneg, dict):
            failures.append("recall_zero_or_negative_limit_bad_return_type")
    except Exception as e:
        failures.append(f"recall_zero_or_negative_limit_exception: {e!r}")

    # 8. remember() with empty content -> should raise ValueError, not silently store
    try:
        try:
            store.remember("")
            failures.append("remember_empty_content_did_not_raise")
        except ValueError:
            pass
    except Exception as e:
        failures.append(f"remember_empty_content_unexpected_exception: {e!r}")

    # 9. pin() on nonexistent id -> KeyError
    try:
        try:
            store.pin("nonexistent-id-xyz", reason="x")
            failures.append("pin_nonexistent_id_did_not_raise")
        except KeyError:
            pass
    except Exception as e:
        failures.append(f"pin_nonexistent_unexpected_exception: {e!r}")

    # 10. forget() then double-forget (idempotent update, not crash)
    try:
        m = store.remember("to forget twice")
        store.forget(m.id, reason="first")
        store.forget(m.id, reason="second")
        fetched = store.get(m.id)
        if fetched is None or fetched.forgotten_reason != "second":
            failures.append("double_forget_unexpected_state")
    except Exception as e:
        failures.append(f"double_forget_exception: {e!r}")

    ok = not failures
    record("edge_cases_battery", ok, f"failures={failures}" if failures else "all 10 sub-checks passed")
    store.close()


def test_db_path_auto_create(tmpdir):
    """Store should create parent directories automatically."""
    nested = os.path.join(tmpdir, "a", "b", "c", "nested.db")
    try:
        store = Store(nested)
        store.remember("nested path works")
        store.close()
        ok = os.path.exists(nested)
    except Exception as e:
        ok = False
        traceback.print_exc()
    record("db_path_nested_dir_auto_create", ok)


def main():
    tmpdir = tempfile.mkdtemp(prefix="mah_reliability_")
    try:
        test_thread_safety(tmpdir)
        test_multiprocess_concurrency(tmpdir)
        test_persistence_across_restart(tmpdir)
        test_scale(tmpdir)
        test_edge_cases(tmpdir)
        test_db_path_auto_create(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    n_total = len(RESULTS)
    print(f"SUMMARY: {n_pass}/{n_total} passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} — {detail}")
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
