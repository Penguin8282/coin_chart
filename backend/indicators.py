"""
기관급 BTC/ETH 신호 시스템 Pine Script 로직을 Python(numpy)으로 이식.
입력: OHLCV numpy 배열 (open, high, low, close, volume) — 시간순 오름차순
출력: 각 지표의 시계열(numpy array)과, 마지막 봉 기준 스코어/신호 딕셔너리
"""
from __future__ import annotations
import numpy as np


def ema(x: np.ndarray, length: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        prev = out[i - 1] if not np.isnan(out[i - 1]) else x[i - 1]
        out[i] = alpha * x[i] + (1 - alpha) * prev
    return out


def sma(x: np.ndarray, length: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    csum = np.cumsum(np.insert(x, 0, 0.0))
    for i in range(length - 1, len(x)):
        out[i] = (csum[i + 1] - csum[i + 1 - length]) / length
    return out


def rma(x: np.ndarray, length: int) -> np.ndarray:
    """Wilder's smoothing (TradingView ta.rma)."""
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    if len(x) == 0:
        return out
    alpha = 1.0 / length
    seed = np.nanmean(x[:length]) if len(x) >= length else x[0]
    out[min(length - 1, len(x) - 1)] = seed
    start = min(length, len(x))
    for i in range(start, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def stdev(x: np.ndarray, length: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(length - 1, len(x)):
        window = x[i - length + 1 : i + 1]
        out[i] = float(np.std(window, ddof=0))
    return out


def true_range(high, low, close):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return tr


def atr(high, low, close, length=14):
    tr = true_range(high, low, close)
    return rma(tr, length)


def rsi(close, length=14):
    close = np.asarray(close, dtype=float)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.nan), where=avg_loss != 0)
    out = 100 - 100 / (1 + rs)
    out = np.where(avg_loss == 0, 100.0, out)
    return out


def macd(close, fast=12, slow=26, signal=9):
    close = np.asarray(close, dtype=float)
    macd_line = ema(close, fast) - ema(close, slow)
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


def stoch(close, high, low, length=14):
    close = np.asarray(close, dtype=float)
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    out = np.full_like(close, np.nan)
    for i in range(length - 1, len(close)):
        hh = np.max(high[i - length + 1 : i + 1])
        ll = np.min(low[i - length + 1 : i + 1])
        out[i] = 100.0 if hh == ll else 100.0 * (close[i] - ll) / (hh - ll)
    return out


def adx_di(high, low, close, length=14):
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm[0] = 0.0
    minus_dm[0] = 0.0
    tr = true_range(high, low, close)
    tr_r = rma(tr, length)
    plus_di = 100.0 * np.divide(rma(plus_dm, length), tr_r, out=np.zeros_like(tr_r), where=tr_r != 0)
    minus_di = 100.0 * np.divide(rma(minus_dm, length), tr_r, out=np.zeros_like(tr_r), where=tr_r != 0)
    dx_denom = plus_di + minus_di
    dx = np.where(dx_denom > 0, 100.0 * np.abs(plus_di - minus_di) / np.where(dx_denom == 0, 1, dx_denom), 0.0)
    adx = rma(dx, length)
    return adx, plus_di, minus_di


def vwap_session(hlc3, volume, session_breaks: np.ndarray | None = None):
    """세션(하루) 기준 VWAP. session_breaks: 새 세션 시작 지점이 True인 bool 배열.
    미제공 시 전체 구간을 단일 누적 VWAP으로 계산."""
    hlc3 = np.asarray(hlc3, dtype=float)
    volume = np.asarray(volume, dtype=float)
    n = len(hlc3)
    out = np.full(n, np.nan)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        if session_breaks is not None and session_breaks[i]:
            cum_pv = 0.0
            cum_v = 0.0
        cum_pv += hlc3[i] * volume[i]
        cum_v += volume[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else hlc3[i]
    return out


def obv(close, volume):
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume, dtype=float)
    direction = np.sign(np.diff(close, prepend=close[0]))
    direction[0] = 0
    out = np.cumsum(direction * volume)
    return out


def bollinger(close, length=20, mult=2.0):
    close = np.asarray(close, dtype=float)
    mid = sma(close, length)
    sd = stdev(close, length)
    upper = mid + mult * sd
    lower = mid - mult * sd
    return mid, upper, lower


def vwma(src, volume, length):
    src = np.asarray(src, dtype=float)
    volume = np.asarray(volume, dtype=float)
    num = sma(src * volume, length) * length
    den = sma(volume, length) * length
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    return out


def highest(x, length):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(length - 1, len(x)):
        out[i] = np.max(x[i - length + 1 : i + 1])
    return out


def lowest(x, length):
    x = np.asarray(x, dtype=float)
    out = np.full_like(x, np.nan)
    for i in range(length - 1, len(x)):
        out[i] = np.min(x[i - length + 1 : i + 1])
    return out


def crossover(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def crossunder(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return out
