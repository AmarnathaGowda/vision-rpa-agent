"""In-memory SQLite SessionStore — isolated per test, no file I/O."""
from __future__ import annotations
import sqlite3


def make_test_session():
    """Return a fully initialised SessionStore backed by :memory:."""
    from memory.session import SessionStore

    store = SessionStore.__new__(SessionStore)
    store.db_path = ":memory:"
    store._conn = sqlite3.connect(":memory:", check_same_thread=False)
    store._conn.execute("PRAGMA journal_mode=WAL")
    store._conn.row_factory = sqlite3.Row
    store._init_schema()
    return store
