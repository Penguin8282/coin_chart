"""캔들스틱 패턴 (도지/해머/장악형/모닝스타/이브닝스타 등) — 규칙 기반 탐지."""
from __future__ import annotations
import numpy as np

CANDLESTICK_NAMES = {
    "doji": "도지",
    "hammer": "해머",
    "hanging_man": "행잉맨",
    "shooting_star": "슈팅스타",
    "inverted_hammer": "역해머",
    "bullish_engulfing": "상승장악형",
    "bearish_engulfing": "하락장악형",
    "piercing_line": "관통형",
    "dark_cloud_cover": "흑운형",
    "morning_star": "모닝스타",
    "evening_star": "이브닝스타",
    "three_white_soldiers": "적삼병",
    "three_black_crows": "흑삼병",
}


def _parts(o, h, l, c):
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    return body, rng, upper, lower


def detect_candlestick_patterns(o, h, l, c) -> list[dict]:
    o, h, l, c = map(lambda a: np.asarray(a, dtype=float), (o, h, l, c))
    n = len(c)
    body, rng, upper, lower = _parts(o, h, l, c)
    # 인과적(과거 데이터만 사용) 이동평균 — 미래 캔들을 참조하는 lookahead bias 방지
    avg_body = np.full(n, np.nan)
    win = 14
    csum = np.cumsum(np.insert(body, 0, 0.0))
    for i in range(n):
        start = max(0, i - win + 1)
        avg_body[i] = (csum[i + 1] - csum[start]) / (i + 1 - start)

    events: list[dict] = []

    def add(i, name, direction, strength=0.6):
        events.append({"index": int(i), "name": name, "name_kr": CANDLESTICK_NAMES.get(name, name),
                        "direction": direction, "strength": round(float(strength), 2), "kind": "candlestick"})

    for i in range(2, n):
        bi, ri, ui, li = body[i], rng[i], upper[i], lower[i]
        if ri <= 0:
            continue

        # 도지: 몸통이 전체 범위의 10% 미만
        if bi < ri * 0.1:
            add(i, "doji", "neutral", 0.4)

        # 해머 / 행잉맨: 아래꼬리 길고 위꼬리 짧음, 몸통 작음
        if lower[i] > bi * 2.0 and ui < bi * 0.5 and bi < ri * 0.35:
            prior_down = c[i - 1] < o[i - 1] if i >= 1 else False
            if prior_down or (c[max(0, i - 3):i].mean() < o[max(0, i - 3):i].mean() if i >= 3 else False):
                add(i, "hammer", "bullish", 0.65)
            else:
                add(i, "hanging_man", "bearish", 0.55)

        # 슈팅스타 / 역해머: 위꼬리 길고 아래꼬리 짧음, 몸통 작음
        if ui > bi * 2.0 and li < bi * 0.5 and bi < ri * 0.35:
            trend_up = c[i - 3] < c[i - 1] if i >= 3 else False
            if trend_up:
                add(i, "shooting_star", "bearish", 0.65)
            else:
                add(i, "inverted_hammer", "bullish", 0.5)

        # 장악형 (2봉)
        if i >= 1:
            prev_bear = c[i - 1] < o[i - 1]
            prev_bull = c[i - 1] > o[i - 1]
            cur_bull = c[i] > o[i]
            cur_bear = c[i] < o[i]
            if prev_bear and cur_bull and c[i] > o[i - 1] and o[i] < c[i - 1]:
                add(i, "bullish_engulfing", "bullish", 0.75)
            if prev_bull and cur_bear and o[i] > c[i - 1] and c[i] < o[i - 1]:
                add(i, "bearish_engulfing", "bearish", 0.75)

            # 관통형 / 흑운형
            mid_prev = (o[i - 1] + c[i - 1]) / 2
            if prev_bear and cur_bull and o[i] < c[i - 1] and c[i] > mid_prev and c[i] < o[i - 1]:
                add(i, "piercing_line", "bullish", 0.6)
            if prev_bull and cur_bear and o[i] > c[i - 1] and c[i] < mid_prev and c[i] > o[i - 1]:
                add(i, "dark_cloud_cover", "bearish", 0.6)

        # 모닝스타 / 이브닝스타 (3봉)
        if i >= 2:
            b0, b1, b2 = body[i - 2], body[i - 1], body[i]
            avg_ref = avg_body[i - 2] if avg_body[i - 2] > 0 else rng[i - 2] * 0.3
            first_bear = c[i - 2] < o[i - 2] and b0 > avg_ref * 0.7   # 의미 있는 크기의 하락 캔들
            first_bull = c[i - 2] > o[i - 2] and b0 > avg_ref * 0.7
            small_mid = b1 < avg_ref * 0.45 and b1 < b0 * 0.6        # 몸통이 뚜렷하게 작은 중간 캔들
            third_bull = c[i] > o[i] and b2 > b1
            third_bear = c[i] < o[i] and b2 > b1
            if first_bear and small_mid and third_bull and c[i] > (o[i - 2] + c[i - 2]) / 2:
                add(i, "morning_star", "bullish", 0.8)
            if first_bull and small_mid and third_bear and c[i] < (o[i - 2] + c[i - 2]) / 2:
                add(i, "evening_star", "bearish", 0.8)

            # 적삼병 / 흑삼병
            if (c[i - 2] > o[i - 2] and c[i - 1] > o[i - 1] and c[i] > o[i]
                    and c[i - 1] > c[i - 2] and c[i] > c[i - 1]
                    and b0 > ri * 0.3 and b1 > ri * 0.3 and body[i] > ri * 0.3):
                add(i, "three_white_soldiers", "bullish", 0.7)
            if (c[i - 2] < o[i - 2] and c[i - 1] < o[i - 1] and c[i] < o[i]
                    and c[i - 1] < c[i - 2] and c[i] < c[i - 1]
                    and b0 > ri * 0.3 and b1 > ri * 0.3 and body[i] > ri * 0.3):
                add(i, "three_black_crows", "bearish", 0.7)

    return events
