"""종목 스크리너 — 여러 종목을 한 번에 훑어 신호 점수순으로 정렬한다.

종목을 하나씩 눌러보지 않고도 "지금 어디에 신호가 떴는지"를 보기 위한 기능이다.
94개 종목을 순차로 조회하면 수십 초가 걸리므로 동시에 가져오되, 거래소를
때리지 않도록 동시 실행 수를 제한한다.

⚠️ 시세를 못 가져와 데모 데이터로 폴백한 종목은 점수가 아무 의미가 없다.
   그런 종목은 결과에서 빼고 몇 개가 빠졌는지 따로 알려준다 — 가짜 점수를
   진짜인 것처럼 순위에 섞으면 안 된다.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .data_providers import DEFAULT_SYMBOLS, get_candles
from .scoring import compute_signals

# 거래소 요청 한도에 걸리지 않도록 동시 실행을 제한한다
_MAX_WORKERS = 6
# 한 번에 훑는 종목 수 상한 — 무료 인스턴스에서 응답 시간이 너무 길어지는 것을 막는다
MAX_TARGETS = 40


@dataclass
class ScanTarget:
    market: str
    symbol: str


def resolve_targets(scope: str, watchlist: list[dict]) -> list[ScanTarget]:
    """scope에 따라 훑을 종목 목록을 정한다.
    watchlist  — 사용자가 담아둔 종목만 (기본값, 가장 빠름)
    crypto/us/kr — 해당 시장 프리셋 전체
    all        — 관심종목 + 전 시장 프리셋"""
    out: list[ScanTarget] = []
    seen: set[tuple[str, str]] = set()

    def add(market: str, symbol: str):
        key = (market, symbol)
        if key not in seen:
            seen.add(key)
            out.append(ScanTarget(market, symbol))

    if scope in ("watchlist", "all"):
        for w in watchlist:
            add(w["market"], w["symbol"])
    if scope in ("crypto", "us", "kr"):
        for p in DEFAULT_SYMBOLS.get(scope, []):
            add(scope, p["symbol"])
    if scope == "all":
        for market, presets in DEFAULT_SYMBOLS.items():
            for p in presets:
                add(market, p["symbol"])

    return out[:MAX_TARGETS]


def _scan_one(target: ScanTarget, interval: str, limit: int, exchange: str | None,
              params: dict) -> dict:
    """한 종목을 조회해 요약 한 줄을 만든다. 실패는 예외 없이 결과에 담아 돌려준다."""
    row: dict = {"market": target.market, "symbol": target.symbol}
    try:
        fetched = get_candles(target.market, target.symbol, interval, limit, exchange=exchange)
        source = fetched["source"]
        if source == "demo":
            # 실제 시세가 아니면 점수가 무의미하다 — 순위에 섞지 않는다
            row.update(ok=False, skipped="시세를 가져오지 못함")
            return row

        rows = fetched["candles"]
        if len(rows) < 60:
            row.update(ok=False, skipped=f"캔들이 {len(rows)}개뿐이라 분석 불가")
            return row

        o = np.array([r["o"] for r in rows], dtype=float)
        h = np.array([r["h"] for r in rows], dtype=float)
        l = np.array([r["l"] for r in rows], dtype=float)
        c = np.array([r["c"] for r in rows], dtype=float)
        v = np.array([r["v"] for r in rows], dtype=float)

        sig = compute_signals(
            o, h, l, c, v,
            vol_len=params.get("vol_len", 20),
            fib_len=min(params.get("fib_len", 100), len(c)),
            adx_thr=params.get("adx_thr", 25.0),
        )
        dash = sig["dashboard"]
        prev = float(c[-2]) if len(c) >= 2 else float(c[-1])
        change = ((dash["price"] - prev) / prev * 100.0) if prev else 0.0

        row.update(
            ok=True,
            source=source,
            currency=fetched.get("currency", "USD"),
            price=dash["price"],
            change_pct=round(change, 2),
            buy_score=dash["buy_score"],
            sell_score=dash["sell_score"],
            signal=dash["signal"],
            rsi=round(dash["rsi"], 1),
            adx=round(dash["adx"], 1),
            strong_trend=dash["strong_trend"],
            vol_ratio=round(dash["vol_ratio"], 2),
            ema_state=dash["ema_state"],
            whale=bool(sig["events"]["whale_accum"][-1] or sig["events"]["whale_distrib"][-1]),
        )
    except Exception as e:  # noqa: BLE001 - 한 종목 실패가 전체 스캔을 막으면 안 된다
        row.update(ok=False, skipped=f"{type(e).__name__}: {e}")
    return row


def run_screen(targets: list[ScanTarget], interval: str = "1h", limit: int = 300,
               exchange: str | None = None, params: dict | None = None,
               min_score: int = 0, direction: str = "any") -> dict:
    """여러 종목을 동시에 훑고 점수순으로 정렬해 돌려준다."""
    params = params or {}
    started = time.time()

    if not targets:
        return {"rows": [], "scanned": 0, "skipped": [], "elapsed_ms": 0,
                "note": "훑을 종목이 없습니다. 관심종목을 담거나 시장을 선택하세요."}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(
            lambda t: _scan_one(t, interval, limit, exchange, params), targets))

    rows = [r for r in results if r.get("ok")]
    skipped = [{"symbol": r["symbol"], "market": r["market"], "reason": r["skipped"]}
               for r in results if not r.get("ok")]

    # 방향 필터: 매수/매도 중 관심 있는 쪽만
    if direction == "buy":
        rows = [r for r in rows if r["buy_score"] >= r["sell_score"]]
    elif direction == "sell":
        rows = [r for r in rows if r["sell_score"] > r["buy_score"]]

    if min_score > 0:
        rows = [r for r in rows if max(r["buy_score"], r["sell_score"]) >= min_score]

    # 우세한 쪽 점수가 높은 순으로. 같으면 ADX(추세 강도)가 높은 쪽 먼저.
    rows.sort(key=lambda r: (max(r["buy_score"], r["sell_score"]), r["adx"]), reverse=True)

    note = None
    if skipped:
        note = f"{len(skipped)}개 종목은 시세를 가져오지 못해 결과에서 제외했습니다"

    return {
        "rows": rows,
        "scanned": len(targets),
        "skipped": skipped,
        "elapsed_ms": int((time.time() - started) * 1000),
        "note": note,
    }
