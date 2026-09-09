"""Adapters foundation — DB connection, generic Repository, BaseAdapter, and SQL loader."""

import os
import json
import warnings
import threading
import psycopg2
import psycopg2.pool
from pathlib import Path
from contextlib import contextmanager

_ADMIN_PATH = Path(__file__).parent / "db.json"
_SQL_DIR = Path(__file__).parent / "sql_queries"
_SQL_CACHE: dict[str, str] = {}


# ── Database Adapter ──────────────────────────────────────────────────────────

class DatabaseAdapter:
    """Thread-safe connection pool."""

    _pool = None
    _lock = threading.Lock()

    @classmethod
    def _get_pool(cls):
        if cls._pool is None or cls._pool.closed:
            with cls._lock:
                if cls._pool is None or cls._pool.closed:
                    conn_name = os.getenv("CLOUD_SQL_CONNECTION_NAME")
                    if conn_name:
                        kwargs = dict(
                            host=f"/cloudsql/{conn_name}",
                            dbname=os.environ["DB_NAME"],
                            user=os.environ["DB_USER"],
                            password=os.environ["DB_PASS"],
                        )
                    else:
                        kwargs = json.loads(_ADMIN_PATH.read_text(encoding="utf-8"))["db"]
                    cls._pool = psycopg2.pool.ThreadedConnectionPool(
                        minconn=1, maxconn=10, **kwargs
                    )
        return cls._pool

    @classmethod
    def get_conn(cls):
        return cls._get_pool().getconn()

    @classmethod
    def put_conn(cls, conn):
        pool = cls._pool
        if pool is not None and not pool.closed:
            pool.putconn(conn)


# ── SQL Loader ────────────────────────────────────────────────────────────────

def load_sql(folder: str, name: str, **kwargs) -> str:
    key = f"{folder}/{name}"
    if key not in _SQL_CACHE:
        path = _SQL_DIR / folder / f"{name}.sql"
        _SQL_CACHE[key] = path.read_text(encoding="utf-8").strip()
    sql = _SQL_CACHE[key]
    if kwargs:
        sql = sql.format(**kwargs)
    return sql


# ── Repository ────────────────────────────────────────────────────────────────

class Repository:
    """Generic SQL operations that work on any table."""

    def __init__(self, conn=None):
        self._owned = conn is None
        self.conn = conn or DatabaseAdapter.get_conn()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        if self._owned and self.conn and not self.conn.closed:
            warnings.warn(
                "Repository connection not explicitly closed — returned via __del__",
                ResourceWarning, stacklevel=2,
            )
            DatabaseAdapter.put_conn(self.conn)
            self.conn = None

    def add(self, table: str, data: dict) -> dict:
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING *"
        return self._exec_row(sql, list(data.values()))

    def get(self, table: str, id: int) -> dict | None:
        sql = f"SELECT * FROM {table} WHERE id = %s"
        return self._exec_row(sql, [id])

    def select(self, table: str, filters: dict | None = None,
               order_by: str | None = None, limit: int | None = None) -> list[dict]:
        sql = f"SELECT * FROM {table}"
        params = []
        if filters:
            clauses = []
            for col, val in filters.items():
                if isinstance(val, (list, tuple)):
                    phs = ", ".join(["%s"] * len(val))
                    clauses.append(f"{col} IN ({phs})")
                    params.extend(val)
                elif val is None:
                    clauses.append(f"{col} IS NULL")
                else:
                    clauses.append(f"{col} = %s")
                    params.append(val)
            sql += " WHERE " + " AND ".join(clauses)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        return self._exec_rows(sql, params)

    def update(self, table: str, id: int, data: dict) -> dict | None:
        if not data:
            return self.get(table, id)
        set_clause = ", ".join(f"{col} = %s" for col in data.keys())
        sql = (
            f"UPDATE {table} SET {set_clause}, "
            f"updated_at = (NOW() AT TIME ZONE 'UTC') "
            f"WHERE id = %s RETURNING *"
        )
        params = list(data.values()) + [id]
        return self._exec_row(sql, params)

    def update_no_timestamp(self, table: str, id: int, data: dict) -> dict | None:
        if not data:
            return self.get(table, id)
        set_clause = ", ".join(f"{col} = %s" for col in data.keys())
        sql = f"UPDATE {table} SET {set_clause} WHERE id = %s RETURNING *"
        params = list(data.values()) + [id]
        return self._exec_row(sql, params)

    def delete(self, table: str, id: int) -> bool:
        sql = f"DELETE FROM {table} WHERE id = %s RETURNING id"
        row = self._exec_row(sql, [id])
        return row is not None

    def execute(self, sql: str, params: list | None = None, fetch: str | None = None):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        elif fetch == "one_dict":
            row = cur.fetchone()
            if row is not None:
                cols = [desc[0] for desc in cur.description]
                result = dict(zip(cols, row))
        elif fetch == "all_dicts":
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            result = [dict(zip(cols, r)) for r in rows]
        self.conn.commit()
        cur.close()
        return result

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        if self._owned and self.conn:
            DatabaseAdapter.put_conn(self.conn)
            self.conn = None

    def _exec_row(self, sql: str, params: list) -> dict | None:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            self.conn.commit()
            cur.close()
            return None
        cols = [desc[0] for desc in cur.description]
        self.conn.commit()
        cur.close()
        return dict(zip(cols, row))

    def _exec_rows(self, sql: str, params: list) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        self.conn.commit()
        cur.close()
        return [dict(zip(cols, row)) for row in rows]


# ── BaseAdapter ───────────────────────────────────────────────────────────────

class BaseAdapter:
    """Subclasses set TABLE and get add/get/select/update/delete for free."""

    TABLE: str = None

    def __init__(self, repo: Repository):
        self.repo = repo

    def add(self, **kwargs) -> dict:
        return self.repo.add(self.TABLE, kwargs)

    def get(self, id: int) -> dict | None:
        return self.repo.get(self.TABLE, id)

    def select(self, order_by: str | None = None,
               limit: int | None = None, **filters) -> list[dict]:
        return self.repo.select(self.TABLE, filters or None, order_by, limit)

    def list(self, order_by: str | None = None) -> list[dict]:
        return self.repo.select(self.TABLE, order_by=order_by)

    def update(self, id: int, **kwargs) -> dict | None:
        return self.repo.update(self.TABLE, id, kwargs)

    def delete(self, id: int) -> bool:
        return self.repo.delete(self.TABLE, id)


# ── Request-scoped Repository ────────────────────────────────────────────────

def get_repo() -> Repository:
    try:
        from flask import g, has_request_context
        if has_request_context():
            if "repo" not in g:
                g.repo = Repository()
            return g.repo
    except ImportError:
        pass
    return Repository()
