"""Shared connection pool.

The pool is created ONCE at application startup (see app/__init__.py) and every
request borrows a connection from it with `with pool.connection() as conn:`.
"""
import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool: ConnectionPool | None = None


def init_pool(conninfo: str | None = None) -> ConnectionPool:
    """Create and open the global pool (min=2, max=8). Idempotent."""
    global pool
    if pool is not None:
        return pool
    pool = ConnectionPool(
        conninfo=conninfo or os.environ["DATABASE_URL"],
        min_size=2,
        max_size=8,
        timeout=5.0,                       # PoolTimeout if no free connection in 5s
        kwargs={"row_factory": dict_row},  # rows come back as dicts
        open=False,
    )
    pool.open()  # does not block: if the DB is down, requests fail with 503
    return pool


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None
