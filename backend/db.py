"""사용자 정의 패턴 / 관심종목 저장소 (SQLite, 표준 라이브러리만 사용)."""
from __future__ import annotations
import json
import os
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

# 저장 위치는 DATA_DIR 환경변수로 바꿀 수 있다. 클라우드 배포 시 영구 디스크를
# 마운트한 경로(예: /var/data)를 지정하면 재시작해도 커스텀 패턴이 유지된다.
# 미지정 시 저장소 안의 data/ 폴더를 사용한다.
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
DB_PATH = _DATA_DIR / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS custom_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,            -- 'rule' | 'shape'
                direction TEXT NOT NULL DEFAULT 'neutral',
                definition TEXT NOT NULL,      -- JSON
                created_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(market, symbol)
            )"""
        )


def create_custom_pattern(name: str, type_: str, direction: str, definition: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO custom_patterns (name, type, direction, definition, created_at) VALUES (?,?,?,?,?)",
            (name, type_, direction, json.dumps(definition, ensure_ascii=False), int(time.time())),
        )
        return cur.lastrowid


def list_custom_patterns() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM custom_patterns ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["definition"] = json.loads(d["definition"])
        out.append(d)
    return out


def get_custom_pattern(pattern_id: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM custom_patterns WHERE id=?", (pattern_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["definition"] = json.loads(d["definition"])
    return d


def delete_custom_pattern(pattern_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM custom_patterns WHERE id=?", (pattern_id,))
        return cur.rowcount > 0


def add_watchlist(market: str, symbol: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (market, symbol, created_at) VALUES (?,?,?)",
            (market, symbol, int(time.time())),
        )


def remove_watchlist(market: str, symbol: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE market=? AND symbol=?", (market, symbol))


def list_watchlist() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]
