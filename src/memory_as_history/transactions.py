"""Shared Store locking and atomic transaction boundaries."""
import functools


def _read_snapshot(method):
    """Read several tables from one SQLite snapshot, reusing an outer write."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._conn.in_transaction:
                return method(self, *args, **kwargs)
            self._conn.execute('BEGIN')
            try:
                result = method(self, *args, **kwargs)
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise
    return wrapper


def _locked(method):
    """Serialize all access to a Store's sqlite connection. sqlite3
    connections (even with check_same_thread=False) are not safe for
    concurrent use from multiple threads — a single Store instance may be
    shared across an MCP server's concurrent tool-call handlers, so every
    public method takes this instance-level lock before touching self._conn."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class _AuditedDenial(PermissionError):
    """Internal signal: the guard wrote only a deliberate denial audit."""


def _transactional(method):
    """Own one transaction per outer state-changing call.

    Reserve SQLite's writer before reading preconditions, so other Store
    connections/processes cannot change the state between a guard and its
    write. Nested calls (recall -> due_for_review) share the outer boundary.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._transaction_active:
                return method(self, *args, **kwargs)

            self._conn.execute("BEGIN IMMEDIATE")
            self._transaction_active = True
            denial = None
            try:
                try:
                    result = method(self, *args, **kwargs)
                except _AuditedDenial as exc:
                    # Guards raise this only before any business mutation.
                    # Ordinary exceptions, including failed audit inserts,
                    # must never take this commit path.
                    denial = exc
                    result = None
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._transaction_active = False

            if denial is not None:
                raise PermissionError(str(denial)) from None
            return result

    return wrapper
