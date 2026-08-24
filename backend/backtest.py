"""백테스트 — 이 신호를 그대로 따랐다면 어땠을지 과거 데이터로 계산한다.

목적은 "얼마 벌었나"보다 **점수가 높을수록 실제로 잘 맞는가**를 확인하는 데 있다.
점수대별 승률이 올라가지 않는다면 22점 체계 자체를 손봐야 한다는 뜻이다.

규칙:
  진입  신호가 뜬 봉의 종가에 들어간다 (그 봉이 마감돼야 신호를 알 수 있으므로,
        신호 봉의 종가가 현실적으로 가장 빠른 진입 시점이다)
  청산  TP/SL 중 먼저 닿는 쪽. 둘 다 안 닿으면 max_bars 후 종가에 정리
  판정  한 봉 안에서 TP와 SL이 모두 닿을 수 있는데, 봉 내부 순서는 알 수 없다.
        이때는 불리한 쪽(SL)으로 처리한다 — 백테스트가 실제보다 좋아 보이게
        만드는 것이 가장 위험한 오류이기 때문이다.

한계(정직하게):
  - 수수료·슬리피지를 비율로만 반영한다. 실제 체결가와는 차이가 난다.
  - 과거 성과가 미래를 보장하지 않는다.
  - 한 번에 한 포지션만 잡는 단순 모델이다.
"""
from __future__ import annotations

import numpy as np

from . import indicators as ind
from .scoring import compute_signals

DEFAULT_FEE_PCT = 0.1   # 진입+청산 왕복 수수료·슬리피지 (%)


def _first_touch(highs: np.ndarray, lows: np.ndarray, tp: float, sl: float,
                 is_long: bool) -> tuple[int | None, str | None]:
    """TP/SL 중 어느 쪽에 먼저 닿는지 찾는다. 같은 봉에서 둘 다 닿으면 SL로 본다."""
    for i in range(len(highs)):
        if is_long:
            hit_tp = highs[i] >= tp
            hit_sl = lows[i] <= sl
        else:
            hit_tp = lows[i] <= tp
            hit_sl = highs[i] >= sl
        if hit_sl:          # 같은 봉에 둘 다 닿아도 불리한 쪽을 택한다
            return i, "sl"
        if hit_tp:
            return i, "tp"
    return None, None


def run_backtest(o, h, l, c, v, times, *, min_score: int = 7, max_bars: int = 48,
                 fee_pct: float = DEFAULT_FEE_PCT, vol_len: int = 20,
                 fib_len: int = 100, adx_thr: float = 25.0,
                 tp_mode: str = "pct", atr_tp_mult: float = 2.2,
                 atr_sl_mult: float = 1.0) -> dict:
    o, h, l, c, v = map(lambda a: np.asarray(a, dtype=float), (o, h, l, c, v))
    n = len(c)
    if n < 120:
        return {"error": "백테스트를 하기엔 캔들이 너무 적습니다 (최소 120개 필요)"}

    sig = compute_signals(o, h, l, c, v, vol_len=vol_len,
                          fib_len=min(fib_len, n), adx_thr=adx_thr)
    atr = ind.atr(h, l, c, 14)
    S, E = sig["series"], sig["events"]
    buy_score, sell_score = S["buy_score"], S["sell_score"]

    trades: list[dict] = []
    i = 0
    while i < n - 2:
        is_buy = buy_score[i] >= min_score and buy_score[i] >= sell_score[i]
        is_sell = sell_score[i] >= min_score and sell_score[i] > buy_score[i]
        if not (is_buy or is_sell):
            i += 1
            continue

        entry = float(c[i])
        score = int(buy_score[i] if is_buy else sell_score[i])
        strong = score >= 7
        # 화면(scoring.py)과 반드시 같은 방식으로 계산해야 한다 — 화면은 ATR로
        # 보여주면서 백테스트는 퍼센트로 재면 성적표가 다른 전략 것이 된다.
        if tp_mode == "atr":
            a = float(atr[i]) if np.isfinite(atr[i]) and atr[i] > 0 else entry * 0.01
            if is_buy:
                tp, sl = entry + a * atr_tp_mult, entry - a * atr_sl_mult
            else:
                tp, sl = entry - a * atr_tp_mult, entry + a * atr_sl_mult
        elif is_buy:
            tp = entry * (1.025 if strong else 1.020)
            sl = entry * (0.988 if strong else 0.991)
        else:
            tp = entry * (0.975 if strong else 0.980)
            sl = entry * (1.012 if strong else 1.009)

        window = slice(i + 1, min(i + 1 + max_bars, n))
        idx, how = _first_touch(h[window], l[window], tp, sl, is_buy)

        if idx is None:
            exit_i = min(i + max_bars, n - 1)
            exit_px = float(c[exit_i])
            how = "timeout"
        else:
            exit_i = i + 1 + idx
            exit_px = tp if how == "tp" else sl

        gross = ((exit_px - entry) / entry) if is_buy else ((entry - exit_px) / entry)
        net = gross * 100.0 - fee_pct

        trades.append({
            "entry_idx": i, "exit_idx": int(exit_i),
            "entry_time": int(times[i]), "exit_time": int(times[exit_i]),
            "direction": "buy" if is_buy else "sell",
            "score": score, "entry": round(entry, 6), "exit": round(exit_px, 6),
            "exit_reason": how, "pnl_pct": round(net, 3),
            "bars_held": int(exit_i - i),
        })
        i = exit_i + 1     # 한 번에 한 포지션만 — 청산 후 다음 봉부터 다시 본다

    return _summarize(trades, times)


def _summarize(trades: list[dict], times) -> dict:
    if not trades:
        return {"trades": [], "summary": {"count": 0},
                "by_score": [], "equity": [],
                "note": "기준 점수를 만족하는 신호가 없어 거래가 발생하지 않았습니다. 점수 기준을 낮춰보세요."}

    pnls = np.array([t["pnl_pct"] for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    # 복리 누적 수익 곡선
    equity, cum = [], 100.0
    for t in trades:
        cum *= (1 + t["pnl_pct"] / 100.0)
        equity.append({"t": t["exit_time"], "value": round(cum, 3)})

    peak, mdd = -1e18, 0.0
    for e in equity:
        peak = max(peak, e["value"])
        mdd = min(mdd, (e["value"] - peak) / peak * 100.0)

    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss else 0.0

    summary = {
        "count": len(trades),
        "wins": int(len(wins)), "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "avg_pnl": round(float(pnls.mean()), 3),
        "avg_win": round(avg_win, 3), "avg_loss": round(avg_loss, 3),
        "payoff": round(payoff, 2),
        "total_return": round(cum - 100.0, 2),
        "mdd": round(mdd, 2),
        "best": round(float(pnls.max()), 2), "worst": round(float(pnls.min()), 2),
        "avg_bars": round(float(np.mean([t["bars_held"] for t in trades])), 1),
    }

    # 점수대별 성적 — 점수가 높을수록 승률이 올라가야 체계가 의미 있다
    buckets = [("12점 이상", 12, 99), ("9~11점", 9, 11), ("7~8점", 7, 8), ("5~6점", 5, 6)]
    by_score = []
    for label, lo, hi in buckets:
        sel = [t for t in trades if lo <= t["score"] <= hi]
        if not sel:
            continue
        p = np.array([t["pnl_pct"] for t in sel], dtype=float)
        by_score.append({
            "label": label, "count": len(sel),
            "win_rate": round(float((p > 0).sum()) / len(sel) * 100.0, 1),
            "avg_pnl": round(float(p.mean()), 3),
        })

    # 점수가 높을수록 잘 맞는지 한 줄로 판정
    verdict = None
    if len(by_score) >= 2:
        rates = [b["win_rate"] for b in by_score]      # 높은 점수대부터
        if all(rates[i] >= rates[i + 1] - 3 for i in range(len(rates) - 1)):
            verdict = "점수가 높을수록 승률이 함께 올라갑니다 — 점수 체계가 제 역할을 하고 있습니다."
        else:
            verdict = "점수가 높다고 승률이 더 좋지는 않습니다 — 점수 기준을 손볼 필요가 있습니다."

    return {"trades": trades[-120:], "summary": summary, "by_score": by_score,
            "equity": equity, "verdict": verdict}
