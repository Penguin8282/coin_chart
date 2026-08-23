"""스윙 고점/저점(피벗) 탐지. 헤드앤숄더/더블탑 등 '기하학적' 차트 패턴은
모두 이 피벗 시퀀스 위에서 판별한다 (실제 상용 차트-패턴 툴들이 쓰는 방식과 동일:
프랙탈 피벗 추출 → ZigZag 노이즈 제거 → 기하 규칙 매칭)."""
from __future__ import annotations
import numpy as np


def find_pivots(high: np.ndarray, low: np.ndarray, order: int = 3, atr: np.ndarray | None = None,
                 min_move_atr: float = 0.8) -> list[dict]:
    """order: 좌우 몇 봉 안에서 최고/최저여야 피벗으로 인정할지.
    min_move_atr: ZigZag 필터 — 직전 피벗 대비 ATR*이 값 이상 움직여야 새 피벗으로 인정."""
    n = len(high)
    raw: list[dict] = []
    for i in range(order, n - order):
        window_h = high[i - order : i + order + 1]
        window_l = low[i - order : i + order + 1]
        if high[i] == np.max(window_h):
            raw.append({"idx": i, "price": float(high[i]), "type": "high"})
        if low[i] == np.min(window_l):
            raw.append({"idx": i, "price": float(low[i]), "type": "low"})
    raw.sort(key=lambda p: p["idx"])

    if not raw:
        return []

    # 같은 지점에서 high/low 동시 발생 등 중복 정리 + ZigZag 스타일로 병합
    pivots: list[dict] = [raw[0]]
    for p in raw[1:]:
        last = pivots[-1]
        if p["type"] == last["type"]:
            # 같은 타입 연속 → 더 극단적인 값으로 교체
            better = (p["price"] > last["price"]) if p["type"] == "high" else (p["price"] < last["price"])
            if better:
                pivots[-1] = p
            continue
        # 타입이 바뀜 → 최소 변동폭 체크
        if atr is not None:
            thr = float(atr[p["idx"]]) * min_move_atr if not np.isnan(atr[p["idx"]]) else 0.0
            if thr > 0 and abs(p["price"] - last["price"]) < thr:
                # 너무 작은 되돌림 → 노이즈로 보고, 더 유리한 쪽만 유지
                if p["type"] == "high" and p["price"] > last["price"]:
                    continue
                if p["type"] == "low" and p["price"] < last["price"]:
                    continue
        pivots.append(p)

    return pivots
