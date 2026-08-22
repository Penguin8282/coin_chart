"""기관급 매수/매도 점수 엔진 — institutional_btc_eth_signals.pine 이식."""
from __future__ import annotations
import numpy as np
from . import indicators as ind


def compute_signals(o, h, l, c, v, vol_len=20, fib_len=100, adx_thr=25.0):
    o, h, l, c, v = map(lambda a: np.asarray(a, dtype=float), (o, h, l, c, v))
    n = len(c)
    hlc3 = (h + l + c) / 3.0

    atr = ind.atr(h, l, c, 14)
    vol_ma = ind.sma(v, vol_len)
    vol_ratio = np.divide(v, vol_ma, out=np.full(n, np.nan), where=vol_ma != 0)
    vol_spike = (vol_ratio > 2.0) & (v > np.roll(v, 1))
    vol_spike[0] = False

    body = np.abs(c - o)
    upper_wick = h - np.maximum(c, o)
    lower_wick = np.minimum(c, o) - l

    e9, e21, e55, e200 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 55), ind.ema(c, 200)
    ema_bull = (e9 > e21) & (e21 > e55) & (c > e200)
    ema_bear = (e9 < e21) & (e21 < e55) & (c < e200)
    sideways = np.abs(e9 - e21) < atr * 0.12

    adx, plus_di, minus_di = ind.adx_di(h, l, c, 14)
    strong_trend = adx > adx_thr

    rsi = ind.rsi(c, 14)
    macd_line, sig_line, macd_hist = ind.macd(c, 12, 26, 9)
    co = ind.crossover(macd_line, sig_line)
    cu = ind.crossunder(macd_line, sig_line)
    hist_prev = np.roll(macd_hist, 1)
    macd_bull = co & (macd_line < 0) & (macd_hist > hist_prev)
    macd_bear = cu & (macd_line > 0) & (macd_hist < hist_prev)
    macd_bull[0] = macd_bear[0] = False
    macd_up = macd_line > sig_line

    stoch_k = ind.stoch(c, h, l, 14)
    stoch_d = ind.sma(stoch_k, 3)
    stoch_buy = (stoch_k < 20) & ind.crossover(stoch_k, stoch_d)
    stoch_sell = (stoch_k > 80) & ind.crossunder(stoch_k, stoch_d)

    vwap = ind.vwap_session(hlc3, v)
    vwap_std = ind.stdev(hlc3, 20)
    vwap_up1, vwap_up2 = vwap + vwap_std, vwap + vwap_std * 2
    vwap_dn1, vwap_dn2 = vwap - vwap_std, vwap - vwap_std * 2
    vwap_buy = (c < vwap_dn2) & (c > o)
    vwap_sell = (c > vwap_up2) & (c < o)

    obv = ind.obv(c, v)
    obv_ma = ind.sma(obv, 20)
    obv_prev = np.roll(obv, 1)
    obv_up = (obv > obv_ma) & (obv > obv_prev)
    obv_dn = (obv < obv_ma) & (obv < obv_prev)
    obv_up[0] = obv_dn[0] = False

    c_shift5 = np.roll(c, 5)
    obv_shift5 = np.roll(obv, 5)
    valid5 = np.arange(n) >= 5
    obv_bull_div = (c < c_shift5) & (obv > obv_shift5) & vol_spike & valid5
    obv_bear_div = (c > c_shift5) & (obv < obv_shift5) & vol_spike & valid5

    bb_mid, bb_upper, bb_lower = ind.bollinger(c, 20, 2.0)
    bb_width = np.divide(bb_upper - bb_lower, bb_mid, out=np.zeros(n), where=bb_mid != 0)
    bb_width_prev10 = np.roll(bb_width, 10)
    bb_squeeze = bb_width < bb_width_prev10 * 0.75
    bb_squeeze[:10] = False
    bb_buy = (c < bb_lower) & (c > o)
    bb_sell = (c > bb_upper) & (c < o)

    bull_vol = np.where(c > o, v, 0.0)
    bear_vol = np.where(c < o, v, 0.0)
    cvd = ind.sma(bull_vol - bear_vol, 14)
    cvd1, cvd2 = np.roll(cvd, 1), np.roll(cvd, 2)
    valid2 = np.arange(n) >= 2
    cvd_bull = (cvd > 0) & (cvd > cvd1) & (cvd1 > cvd2) & valid2
    cvd_bear = (cvd < 0) & (cvd < cvd1) & (cvd1 < cvd2) & valid2

    fib_high = ind.highest(h, fib_len)
    fib_low = ind.lowest(l, fib_len)
    fib_range = fib_high - fib_low
    fib_236 = fib_high - fib_range * 0.236
    fib_382 = fib_high - fib_range * 0.382
    fib_500 = fib_high - fib_range * 0.500
    fib_618 = fib_high - fib_range * 0.618
    fib_786 = fib_high - fib_range * 0.786
    fib_tol = atr * 0.6
    fib_support = (np.abs(c - fib_618) < fib_tol) | (np.abs(c - fib_786) < fib_tol)
    fib_resist = (np.abs(c - fib_382) < fib_tol) | (np.abs(c - fib_500) < fib_tol)

    whale_accum = vol_spike & (lower_wick > body * 1.8) & (lower_wick > upper_wick * 2.0) & (c > o) & (c > e21) & (rsi < 45)
    whale_distrib = vol_spike & (upper_wick > body * 1.8) & (upper_wick > lower_wick * 2.0) & (c < o) & (c < e21) & (rsi > 55)
    flush = vol_spike & (lower_wick > body * 2.5) & (c > o)

    swing_low20 = ind.lowest(l, 20)
    swing_high20 = ind.highest(h, 20)
    swing_low20_prev = np.roll(swing_low20, 1)
    swing_high20_prev = np.roll(swing_high20, 1)
    stop_hunt_dn = (l < swing_low20_prev) & (c > swing_low20_prev) & vol_spike
    stop_hunt_up = (h > swing_high20_prev) & (c < swing_high20_prev) & vol_spike

    o1, o2, o3 = np.roll(o, 1), np.roll(o, 2), np.roll(o, 3)
    c1, c2, c3 = np.roll(c, 1), np.roll(c, 2), np.roll(c, 3)
    valid3 = np.arange(n) >= 3
    bear_reversal = (c1 < o1) & (c2 < o2) & (c3 < o3) & (c > o) & valid3
    bull_reversal = (c1 > o1) & (c2 > o2) & (c3 > o3) & (c < o) & valid3

    def b(cond, pts):
        return np.where(cond, pts, 0)

    buy_raw = (b(whale_accum, 3) + b(obv_bull_div, 3) + b(macd_bull, 3) + b(flush, 2) +
               b(stop_hunt_dn, 2) + b(vwap_buy, 2) + b(obv_up, 2) + b(fib_support, 2) +
               b(stoch_buy, 2) + b(cvd_bull, 2) + b(ema_bull, 2) +
               np.where(rsi < 30, 2, np.where(rsi < 40, 1, 0)) +
               b(bb_buy, 1) + b(bear_reversal, 1) + b(bb_squeeze, 1))

    sell_raw = (b(whale_distrib, 3) + b(obv_bear_div, 3) + b(macd_bear, 3) + b(stop_hunt_up, 2) +
                b(vwap_sell, 2) + b(obv_dn, 2) + b(fib_resist, 2) + b(stoch_sell, 2) +
                b(cvd_bear, 2) + b(ema_bear, 2) +
                np.where(rsi > 70, 2, np.where(rsi > 60, 1, 0)) +
                b(bb_sell, 1) + b(bull_reversal, 1) + b(bb_squeeze, 1))

    buy_score = np.where(sideways, 0, np.where(strong_trend, buy_raw, np.round(buy_raw * 0.6))).astype(int)
    sell_score = np.where(sideways, 0, np.where(strong_trend, sell_raw, np.round(sell_raw * 0.6))).astype(int)

    strong_buy = buy_score >= 7
    normal_buy = (buy_score >= 5) & (buy_score < 7)
    strong_sell = sell_score >= 7
    normal_sell = (sell_score >= 5) & (sell_score < 7)

    tp_buy = np.where(strong_buy, c * 1.025, c * 1.020)
    sl_buy = np.where(strong_buy, c * 0.988, c * 0.991)
    tp_sell = np.where(strong_sell, c * 0.975, c * 0.980)
    sl_sell = np.where(strong_sell, c * 1.012, c * 1.009)

    series = {
        "ema9": e9, "ema21": e21, "ema55": e55, "ema200": e200,
        "vwap": vwap, "vwap_up1": vwap_up1, "vwap_up2": vwap_up2,
        "vwap_dn1": vwap_dn1, "vwap_dn2": vwap_dn2,
        "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "fib_236": fib_236, "fib_382": fib_382, "fib_500": fib_500,
        "fib_618": fib_618, "fib_786": fib_786,
        "rsi": rsi, "adx": adx, "macd_line": macd_line, "macd_signal": sig_line,
        "macd_hist": macd_hist, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "obv": obv, "cvd": cvd, "vol_ratio": vol_ratio,
        "buy_score": buy_score.astype(float), "sell_score": sell_score.astype(float),
    }
    events = {
        "strong_buy": strong_buy, "normal_buy": normal_buy,
        "strong_sell": strong_sell, "normal_sell": normal_sell,
        "obv_bull_div": obv_bull_div, "obv_bear_div": obv_bear_div,
        "whale_accum": whale_accum, "whale_distrib": whale_distrib,
        "flush": flush, "stop_hunt_dn": stop_hunt_dn, "stop_hunt_up": stop_hunt_up,
        "bb_squeeze": bb_squeeze, "vol_spike": vol_spike,
    }

    last = -1
    dashboard = {
        "price": float(c[last]),
        "buy_score": int(buy_score[last]), "sell_score": int(sell_score[last]),
        "signal": ("strong_buy" if strong_buy[last] else "normal_buy" if normal_buy[last]
                   else "strong_sell" if strong_sell[last] else "normal_sell" if normal_sell[last]
                   else "sideways" if sideways[last] else "monitor"),
        "adx": float(adx[last]), "strong_trend": bool(strong_trend[last]),
        "rsi": float(rsi[last]),
        "macd_state": "golden_cross" if macd_bull[last] else "dead_cross" if macd_bear[last] else ("up" if macd_up[last] else "down"),
        "obv_state": "bull_div" if obv_bull_div[last] else "bear_div" if obv_bear_div[last] else ("up" if obv_up[last] else "down"),
        "vwap_state": ("above_2sigma" if c[last] > vwap_up2[last] else "above_1sigma" if c[last] > vwap_up1[last]
                       else "above" if c[last] > vwap[last] else "below_2sigma" if c[last] < vwap_dn2[last] else "below"),
        "ema_state": "bull_stack" if ema_bull[last] else "bear_stack" if ema_bear[last] else "neutral",
        "bb_state": "squeeze" if bb_squeeze[last] else ("break_upper" if c[last] > bb_upper[last] else "break_lower" if c[last] < bb_lower[last] else "inside"),
        "vol_spike": bool(vol_spike[last]), "vol_ratio": float(vol_ratio[last]) if not np.isnan(vol_ratio[last]) else 0.0,
        "tp_buy": float(tp_buy[last]), "sl_buy": float(sl_buy[last]),
        "tp_sell": float(tp_sell[last]), "sl_sell": float(sl_sell[last]),
    }

    return {"series": series, "events": events, "dashboard": dashboard}
