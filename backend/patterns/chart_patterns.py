"""고전 차트 패턴(헤드앤숄더/더블탑·바텀/삼각수렴/쐐기형 등) — 피벗 기반 기하 규칙 탐지.

방식: (1) pivots.find_pivots 로 스윙 고점/저점을 뽑고 (2) 교대(high/low) 시퀀스가
되도록 정리한 뒤 (3) 각 패턴의 교과서적 정의를 기하학적 허용오차(tolerance)로
매칭한다. 이는 실제 상용 차트-패턴 인식 툴 다수가 쓰는 방식과 동일한 접근이며,
라벨링된 이미지로 학습한 딥러닝 분류기는 아니다 — 그 방식은 대량의 라벨 데이터와
GPU 학습 인프라가 필요해 이 프로젝트 범위를 벗어난다."""
from __future__ import annotations
import numpy as np

CHART_PATTERN_NAMES = {
    "double_top": "더블탑(이중천장)",
    "double_bottom": "더블바텀(이중바닥)",
    "head_and_shoulders": "헤드앤숄더",
    "inverse_head_and_shoulders": "역헤드앤숄더",
    "triple_top": "트리플탑",
    "triple_bottom": "트리플바텀",
    "ascending_triangle": "상승삼각형",
    "descending_triangle": "하락삼각형",
    "symmetrical_triangle": "대칭삼각형",
    "rising_wedge": "상승쐐기형",
    "falling_wedge": "하락쐐기형",
}


def _alternate(pivots: list[dict]) -> list[dict]:
    if not pivots:
        return []
    out = [pivots[0]]
    for p in pivots[1:]:
        if p["type"] == out[-1]["type"]:
            better = (p["price"] > out[-1]["price"]) if p["type"] == "high" else (p["price"] < out[-1]["price"])
            if better:
                out[-1] = p
        else:
            out.append(p)
    return out


def _close(a: float, b: float, tol: float) -> bool:
    ref = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / ref <= tol


def _slope_pct(idxs, prices) -> float:
    """가격 스케일에 무관하도록 정규화한 기울기(봉당 변화율)."""
    idxs = np.asarray(idxs, dtype=float)
    prices = np.asarray(prices, dtype=float)
    if len(idxs) < 2 or np.ptp(idxs) == 0:
        return 0.0
    A = np.vstack([idxs, np.ones(len(idxs))]).T
    slope, _ = np.linalg.lstsq(A, prices, rcond=None)[0]
    return float(slope / max(np.mean(prices), 1e-9))


def _dedup(events: list[dict]) -> list[dict]:
    """같은 종류의 패턴이 겹치는 구간에서 중복 검출되는 걸 억제 (신뢰도 높은 것만 유지)."""
    events = sorted(events, key=lambda e: (-e["confidence"], e["start_idx"]))
    kept: list[dict] = []
    for e in events:
        overlap = False
        for k in kept:
            if k["name"] == e["name"] and not (e["end_idx"] < k["start_idx"] or e["start_idx"] > k["end_idx"]):
                overlap = True
                break
        if not overlap:
            kept.append(e)
    return kept


def detect_chart_patterns(close: np.ndarray, pivots: list[dict], tol: float = 0.028, confirm_window: int = 30) -> list[dict]:
    alt = _alternate(pivots)
    events: list[dict] = []
    n_close = len(close)

    def _confirmed(idx_from: int, cond) -> bool:
        window = close[idx_from : min(idx_from + confirm_window, n_close)]
        return bool(np.any(cond(window))) if len(window) else False

    def add(name, pts, direction, confidence, target=None, note=""):
        events.append({
            "kind": "chart", "name": name, "name_kr": CHART_PATTERN_NAMES.get(name, name),
            "direction": direction, "confidence": round(float(np.clip(confidence, 0, 1)), 2),
            "start_idx": int(pts[0]["idx"]), "end_idx": int(pts[-1]["idx"]),
            "points": [{"idx": int(p["idx"]), "price": round(float(p["price"]), 6)} for p in pts],
            "target": (round(float(target), 6) if target is not None else None),
            "note": note,
        })

    highs = [p for p in alt if p["type"] == "high"]
    lows = [p for p in alt if p["type"] == "low"]

    # ── 더블탑 / 더블바텀 (H,L,H / L,H,L 3점) ────────────────────────
    for i in range(len(alt) - 2):
        a, b_, c_ = alt[i], alt[i + 1], alt[i + 2]
        if a["type"] == "high" and b_["type"] == "low" and c_["type"] == "high":
            if _close(a["price"], c_["price"], tol):
                confirmed = _confirmed(c_["idx"], lambda w: w < b_["price"])
                conf = 0.55 + (0.25 if confirmed else 0) + max(0, 0.2 - abs(a["price"] - c_["price"]) / a["price"])
                target = b_["price"] - (max(a["price"], c_["price"]) - b_["price"])
                add("double_top", [a, b_, c_], "bearish", conf, target,
                    "넥라인(저점) 이탈 확인됨" if confirmed else "넥라인 이탈 대기")
        if a["type"] == "low" and b_["type"] == "high" and c_["type"] == "low":
            if _close(a["price"], c_["price"], tol):
                confirmed = _confirmed(c_["idx"], lambda w: w > b_["price"])
                conf = 0.55 + (0.25 if confirmed else 0) + max(0, 0.2 - abs(a["price"] - c_["price"]) / a["price"])
                target = b_["price"] + (b_["price"] - min(a["price"], c_["price"]))
                add("double_bottom", [a, b_, c_], "bullish", conf, target,
                    "넥라인(고점) 돌파 확인됨" if confirmed else "넥라인 돌파 대기")

    # ── 헤드앤숄더 / 역헤드앤숄더, 트리플탑/바텀 (5점) ────────────────
    for i in range(len(alt) - 4):
        p = alt[i : i + 5]
        types = [x["type"] for x in p]
        if types == ["high", "low", "high", "low", "high"]:
            s1, l1, head, l2, s2 = p
            if head["price"] > s1["price"] and head["price"] > s2["price"] and _close(s1["price"], s2["price"], tol * 1.6):
                neckline = (l1["price"] + l2["price"]) / 2
                confirmed = _confirmed(s2["idx"], lambda w: w < neckline)
                conf = 0.6 + (0.25 if confirmed else 0) + max(0, 0.15 - abs(s1["price"] - s2["price"]) / s1["price"])
                target = neckline - (head["price"] - neckline)
                add("head_and_shoulders", p, "bearish", conf, target,
                    "넥라인 이탈 확인됨" if confirmed else "넥라인 이탈 대기")
            elif _close(s1["price"], head["price"], tol) and _close(head["price"], s2["price"], tol) and _close(s1["price"], s2["price"], tol):
                # 세 고점이 거의 동일 → 트리플탑
                neckline = (l1["price"] + l2["price"]) / 2
                confirmed = _confirmed(s2["idx"], lambda w: w < neckline)
                conf = 0.55 + (0.25 if confirmed else 0)
                target = neckline - (max(s1["price"], head["price"], s2["price"]) - neckline)
                add("triple_top", p, "bearish", conf, target,
                    "넥라인 이탈 확인됨" if confirmed else "넥라인 이탈 대기")
        if types == ["low", "high", "low", "high", "low"]:
            s1, h1, head, h2, s2 = p
            if head["price"] < s1["price"] and head["price"] < s2["price"] and _close(s1["price"], s2["price"], tol * 1.6):
                neckline = (h1["price"] + h2["price"]) / 2
                confirmed = _confirmed(s2["idx"], lambda w: w > neckline)
                conf = 0.6 + (0.25 if confirmed else 0) + max(0, 0.15 - abs(s1["price"] - s2["price"]) / s1["price"])
                target = neckline + (neckline - head["price"])
                add("inverse_head_and_shoulders", p, "bullish", conf, target,
                    "넥라인 돌파 확인됨" if confirmed else "넥라인 돌파 대기")
            elif _close(s1["price"], head["price"], tol) and _close(head["price"], s2["price"], tol) and _close(s1["price"], s2["price"], tol):
                neckline = (h1["price"] + h2["price"]) / 2
                confirmed = _confirmed(s2["idx"], lambda w: w > neckline)
                conf = 0.55 + (0.25 if confirmed else 0)
                target = neckline + (neckline - min(s1["price"], head["price"], s2["price"]))
                add("triple_bottom", p, "bullish", conf, target,
                    "넥라인 돌파 확인됨" if confirmed else "넥라인 돌파 대기")

    # ── 삼각형 / 쐐기형 (최근 고점 3개 + 저점 3개, 추세선 기울기 비교) ──
    for i in range(len(highs) - 2):
        for j in range(len(lows) - 2):
            hh = highs[i : i + 3]
            ll = lows[j : j + 3]
            lo_idx = min(hh[0]["idx"], ll[0]["idx"])
            hi_idx = max(hh[-1]["idx"], ll[-1]["idx"])
            if hi_idx - lo_idx > (hh[-1]["idx"] - hh[0]["idx"] + ll[-1]["idx"] - ll[0]["idx"]) * 1.3 + 5:
                continue  # 두 추세선이 시간적으로 너무 동떨어지면 같은 패턴이 아님
            h_slope = _slope_pct([p["idx"] for p in hh], [p["price"] for p in hh])
            l_slope = _slope_pct([p["idx"] for p in ll], [p["price"] for p in ll])
            flat = 0.0006
            pts = sorted(hh + ll, key=lambda x: x["idx"])
            if abs(h_slope) < flat and l_slope > flat:
                add("ascending_triangle", pts, "bullish", 0.55 + min(0.3, l_slope * 80), note="저항선 수평 + 저점 상승")
            elif h_slope < -flat and abs(l_slope) < flat:
                add("descending_triangle", pts, "bearish", 0.55 + min(0.3, -h_slope * 80), note="지지선 수평 + 고점 하락")
            elif h_slope < -flat and l_slope > flat:
                add("symmetrical_triangle", pts, "neutral", 0.5, note="고점 하락 + 저점 상승 (수렴, 방향은 이탈 시 결정)")
            elif h_slope > flat and l_slope > flat and h_slope < l_slope * 0.85:
                add("rising_wedge", pts, "bearish", 0.5 + min(0.25, (l_slope - h_slope) * 60), note="상단·하단 모두 상승하며 수렴 (되돌림 경계)")
            elif h_slope < -flat and l_slope < -flat and l_slope < h_slope * 0.85:
                add("falling_wedge", pts, "bullish", 0.5 + min(0.25, (h_slope - l_slope) * 60), note="상단·하단 모두 하락하며 수렴 (반등 기대)")

    events = _dedup(events)
    events.sort(key=lambda e: e["end_idx"])
    return events
