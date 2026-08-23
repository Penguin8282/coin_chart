"""저장소 — 계정·세션·관심종목·커스텀 패턴·매매 기록.

두 가지 DB를 지원한다:
  DATABASE_URL 없음 → SQLite (로컬 개발·간단 배포). 표준 라이브러리만 쓴다.
  DATABASE_URL 있음 → Postgres (pg8000). Render 무료 플랜은 디스크가 임시라
                      재배포 때 SQLite 파일이 통째로 사라지므로, 계정과 개인
                      기록을 진짜로 보관하려면 서버 밖 DB(Neon·Supabase 등
                      무료 Postgres)에 연결해야 한다.

쿼리는 전부 '?' 플레이스홀더로 쓰고, Postgres일 때만 '%s'로 바꿔 실행한다.
문법이 갈리는 부분(자동증가 키, UPSERT)은 각 함수 안에서 분기한다 — ORM을
들이는 것보다 어디서 무엇이 다른지 눈에 보이는 쪽을 택했다.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PG = bool(DATABASE_URL)

# ── 연결 ─────────────────────────────────────────────────────────

_DATA_DIR = Path(os.environ.get("DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
DB_PATH = _DATA_DIR / "app.db"
if not IS_PG:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _parse_pg_url(url: str) -> dict:
    """postgres://user:pass@host:port/dbname?sslmode=require 형태를 pg8000 인자로."""
    m = re.match(
        r"^postgres(?:ql)?://(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?"
        r"@(?P<host>[^:/?]+)(?::(?P<port>\d+))?/(?P<database>[^?]+)",
        url,
    )
    if not m:
        raise ValueError("DATABASE_URL 형식을 해석할 수 없습니다")
    d = m.groupdict()
    import urllib.parse
    return {
        "user": urllib.parse.unquote(d["user"]),
        "password": urllib.parse.unquote(d["password"] or ""),
        "host": d["host"],
        "port": int(d["port"] or 5432),
        "database": d["database"],
    }


def to_pg_sql(sql: str) -> str:
    """'?' 플레이스홀더를 Postgres용 '%s'로 바꾼다. 따옴표 안은 건드리지 않는다."""
    out, in_str = [], False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _Conn:
    """sqlite3.Connection과 pg8000을 같은 모양으로 쓰기 위한 얇은 껍데기."""

    def __init__(self, raw, is_pg: bool):
        self._raw = raw
        self._pg = is_pg

    def execute(self, sql: str, params: tuple = ()):
        if self._pg:
            cur = self._raw.cursor()
            cur.execute(to_pg_sql(sql), params)
            return _Cursor(cur)
        return _Cursor(self._raw.execute(sql, params))

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


class _Cursor:
    def __init__(self, raw):
        self._raw = raw

    @property
    def rowcount(self):
        return self._raw.rowcount

    def _cols(self):
        return [d[0] for d in self._raw.description]

    def fetchone(self):
        r = self._raw.fetchone()
        return dict(zip(self._cols(), r)) if r is not None else None

    def fetchall(self):
        cols = None
        rows = self._raw.fetchall()
        if not rows:
            return []
        cols = self._cols()
        return [dict(zip(cols, r)) for r in rows]


@contextmanager
def get_conn():
    if IS_PG:
        import pg8000.dbapi
        import ssl
        kw = _parse_pg_url(DATABASE_URL)
        # Neon·Supabase 등 관리형 Postgres는 TLS가 필수다
        ctx = ssl.create_default_context()
        raw = pg8000.dbapi.Connection(**kw, ssl_context=ctx)
        conn = _Conn(raw, True)
    else:
        raw = sqlite3.connect(DB_PATH)
        raw.row_factory = sqlite3.Row
        conn = _Conn(raw, False)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── 스키마 ───────────────────────────────────────────────────────

_PK = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"


def init_db():
    with get_conn() as conn:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS users (
                id {_PK},
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS custom_patterns (
                id {_PK},
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'neutral',
                definition TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS watchlist (
                id {_PK},
                user_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, market, symbol)
            )"""
        )
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS portfolio_trades (
                id {_PK},
                user_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,           -- 'buy' | 'sell'
                qty REAL NOT NULL,
                price REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )"""
        )
        _migrate_legacy(conn)


def _migrate_legacy(conn):
    """user_id 없던 시절(v1) SQLite 테이블 처리.

    옛 스키마의 watchlist는 UNIQUE(market,symbol)이라 계정별 분리와 충돌한다.
    배포본(무료 플랜)은 어차피 재시작 때 DB가 사라져 남길 데이터가 없고,
    로컬 개발 데이터는 데모 수준이므로 복잡한 이관 대신 legacy_ 이름으로
    비켜두기만 한다 — 지우지는 않아서 필요하면 직접 꺼내볼 수 있다."""
    if IS_PG:
        return  # Postgres는 처음부터 v2 스키마로 시작한다
    for table in ("watchlist", "custom_patterns"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and "user_id" not in cols:
            conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            # init_db 의 CREATE TABLE IF NOT EXISTS 가 다음 실행에서 새로 만든다
    # 이름을 비켜둔 직후 같은 트랜잭션에서 새 테이블을 만들어 준다
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS custom_patterns (
            id {_PK},
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'neutral',
            definition TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS watchlist (
            id {_PK},
            user_id INTEGER NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, market, symbol)
        )"""
    )


def _insert_returning_id(conn, sql: str, params: tuple) -> int:
    """자동증가 PK를 돌려받는 INSERT. sqlite는 lastrowid, pg는 RETURNING."""
    if IS_PG:
        cur = conn.execute(sql + " RETURNING id", params)
        return cur.fetchone()["id"]
    cur = conn.execute(sql, params)
    return cur._raw.lastrowid


# ── 계정 ─────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str) -> int | None:
    """만들면 id, 이미 있으면 None."""
    with get_conn() as conn:
        exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            return None
        return _insert_returning_id(
            conn,
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (username, password_hash, int(time.time())),
        )


def get_user_by_name(username: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username=?",
            (username,),
        ).fetchone()


def get_user_by_id(user_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, username, created_at FROM users WHERE id=?", (user_id,)
        ).fetchone()


# ── 세션 ─────────────────────────────────────────────────────────

def create_session(token_hash: str, user_id: int, ttl_seconds: int):
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token_hash, user_id, now, now + ttl_seconds),
        )


def get_session_user(token_hash: str) -> dict | None:
    now = int(time.time())
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash=? AND s.expires_at > ?""",
            (token_hash, now),
        ).fetchone()
        # 만료된 세션은 눈에 띄었을 때 치운다 (별도 청소 작업 없이도 쌓이지 않게)
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return row


def delete_session(token_hash: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


# ── 커스텀 패턴 (계정별) ─────────────────────────────────────────

def create_custom_pattern(user_id: int, name: str, type_: str, direction: str, definition: dict) -> int:
    with get_conn() as conn:
        return _insert_returning_id(
            conn,
            "INSERT INTO custom_patterns (user_id, name, type, direction, definition, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, name, type_, direction, json.dumps(definition, ensure_ascii=False), int(time.time())),
        )


def list_custom_patterns(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_patterns WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    for d in rows:
        d["definition"] = json.loads(d["definition"])
    return rows


def get_custom_pattern(user_id: int, pattern_id: int) -> dict | None:
    with get_conn() as conn:
        d = conn.execute(
            "SELECT * FROM custom_patterns WHERE id=? AND user_id=?", (pattern_id, user_id)
        ).fetchone()
    if not d:
        return None
    d["definition"] = json.loads(d["definition"])
    return d


def delete_custom_pattern(user_id: int, pattern_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM custom_patterns WHERE id=? AND user_id=?", (pattern_id, user_id)
        )
        return cur.rowcount > 0


# ── 관심종목 (계정별) ────────────────────────────────────────────

def add_watchlist(user_id: int, market: str, symbol: str):
    with get_conn() as conn:
        if IS_PG:
            conn.execute(
                "INSERT INTO watchlist (user_id, market, symbol, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT (user_id, market, symbol) DO NOTHING",
                (user_id, market, symbol, int(time.time())),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, market, symbol, created_at) VALUES (?,?,?,?)",
                (user_id, market, symbol, int(time.time())),
            )


def remove_watchlist(user_id: int, market: str, symbol: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id=? AND market=? AND symbol=?",
            (user_id, market, symbol),
        )


def list_watchlist(user_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM watchlist WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        ).fetchall()


# ── 매매 기록 (계정별) ───────────────────────────────────────────

def add_trade(user_id: int, market: str, symbol: str, side: str, qty: float,
              price: float, note: str = "") -> int:
    with get_conn() as conn:
        return _insert_returning_id(
            conn,
            "INSERT INTO portfolio_trades (user_id, market, symbol, side, qty, price, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, market, symbol, side, qty, price, note, int(time.time())),
        )


def list_trades(user_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM portfolio_trades WHERE user_id=? ORDER BY created_at ASC, id ASC",
            (user_id,),
        ).fetchall()


def delete_trade(user_id: int, trade_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM portfolio_trades WHERE id=? AND user_id=?", (trade_id, user_id)
        )
        return cur.rowcount > 0
