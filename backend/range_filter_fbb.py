"""Range Filter (DonovanWall) + Fibonacci Bollinger Bands — Pine → Python 이식."""
from __future__ import annotations
import numpy as np
from .indicators import ema, stdev, vwma


def range_size(x: np.ndarray, qty: float, n: int) -> np.ndarray:
    wper = n * 2 - 1
    diff = np.abs(x - np.roll(x, 1))
    diff[0] = 0.0
    avrng = ema(diff, n)
    return ema(avrng, wper) * qty


def range_filter(x: np.ndarray, rng: np.ndarray, n: int):
    n_bars = len(x)
    filt = np.zeros(n_bars)
    hi_band = np.zeros(n_bars)
    lo_band = np.zeros(n_bars)
    filt[0] = x[0]
    for i in range(1, n_bars):
        r = rng[i] if not np.isnan(rng[i]) else 0.0
        prev = filt[i - 1]
        val = prev
        if x[i] - r > prev:
            val = x[i] - r
        if x[i] + r < prev:
            val = x[i] + r
        filt[i] = val
        hi_band[i] = val + r
        lo_band[i] = val - r
    hi_band[0] = filt[0]
    lo_band[0] = filt[0]
    return hi_band, lo_band, filt


def compute_range_filter(close: np.ndarray, period: int = 20, mult: float = 3.5):
    rng = range_size(close, mult, period)
    hi_band, lo_band, filt = range_filter(close, rng, period)

    n = len(close)
    fdir = np.zeros(n)
    for i in range(1, n):
        if filt[i] > filt[i - 1]:
            fdir[i] = 1
        elif filt[i] < filt[i - 1]:
            fdir[i] = -1
        else:
            fdir[i] = fdir[i - 1]
    upward = fdir == 1
    downward = fdir == -1

    long_cond = np.zeros(n, dtype=bool)
    short_cond = np.zeros(n, dtype=bool)
    for i in range(1, n):
        above = close[i] > filt[i]
        below = close[i] < filt[i]
        long_cond[i] = above and upward[i] and (close[i] > close[i - 1] or close[i] < close[i - 1])
        short_cond[i] = below and downward[i] and (close[i] < close[i - 1] or close[i] > close[i - 1])

    cond_ini = np.zeros(n, dtype=int)
    for i in range(1, n):
        if long_cond[i]:
            cond_ini[i] = 1
        elif short_cond[i]:
            cond_ini[i] = -1
        else:
            cond_ini[i] = cond_ini[i - 1]

    long_condition = np.zeros(n, dtype=bool)
    short_condition = np.zeros(n, dtype=bool)
    for i in range(1, n):
        long_condition[i] = long_cond[i] and cond_ini[i - 1] == -1
        short_condition[i] = short_cond[i] and cond_ini[i - 1] == 1

    return {
        "filter": filt, "hi_band": hi_band, "lo_band": lo_band,
        "upward": upward, "downward": downward,
        "buy_signal": long_condition, "sell_signal": short_condition,
    }


def compute_fibonacci_bb(hlc3: np.ndarray, volume: np.ndarray, length: int = 200, mult: float = 3.0):
    basis = vwma(hlc3, volume, length)
    dev = mult * stdev(hlc3, length)
    ratios = [0.236, 0.382, 0.5, 0.618, 0.764, 1.0]
    upper = {f"u{int(r*1000)}": basis + r * dev for r in ratios}
    lower = {f"l{int(r*1000)}": basis - r * dev for r in ratios}
    return {"basis": basis, **upper, **lower}
