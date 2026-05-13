"""In-memory SQLite SessionStore — isolated per test, no file I/O."""
from __future__ import annotations
import sqlite3


def make_test_session():
    """Return a fully initialised SessionMemory backed by :memory:."""
    from memory.session import SessionMemory

    store = SessionMemory.__new__(SessionMemory)
    store.db_path = ":memory:"
    store.conn = sqlite3.connect(":memory:", check_same_thread=False)
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.row_factory = sqlite3.Row
    store._create_schema()
    return store
