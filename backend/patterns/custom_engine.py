"""사용자 정의 패턴 엔진.

두 가지 방식을 지원한다:
  1) 규칙 빌더(rule)  — 캔들 몇 개(상대 오프셋)에 대해 필드 비교 조건을 조합해
     "나만의 캔들 패턴"을 정의. 임의 코드 실행(eval) 없이 안전한 구조화된
     조건만 허용한다.
  2) 모양 템플릿(shape) — 차트에서 원하는 구간을 드래그해 저장하면, 그 구간의
     정규화된 가격 곡선(shape)을 템플릿으로 저장. 이후 전체 히스토리를 슬라이딩
     윈도우로 스캔하여 상관계수가 높은(유사한 모양의) 구간을 자동으로 찾아준다.
"""
from __future__ import annotations
import numpy as np

_ALLOWED_FIELDS = {
    "open", "high", "low", "close", "volume",
    "body", "range", "upper_wick", "lower_wick", "body_pct",
}
_ALLOWED_OPS = {">", "<", ">=", "<=", "==", "!="}


def _bar_fields(o, h, l, c, v, i):
    body = abs(c[i] - o[i])
    rng = max(h[i] - l[i], 1e-9)
    return {
        "open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": v[i],
        "body": body, "range": rng,
        "upper_wick": h[i] - max(o[i], c[i]),
        "lower_wick": min(o[i], c[i]) - l[i],
        "body_pct": body / rng,
    }


def validate_rule_definition(definition: dict) -> list[str]:
    """구조 검증만 하고 임의 코드는 절대 실행하지 않는다. 에러 메시지 리스트 반환(없으면 정상)."""
    errors = []
    candles = definition.get("candles")
    if not isinstance(candles, list) or not (1 <= len(candles) <= 8):
        return ["candles는 1~8개의 캔들 규칙 리스트여야 합니다."]
    for ci, cnd in enumerate(candles):
        offset = cnd.get("offset")
        if not isinstance(offset, int) or offset > 0:
            errors.append(f"candles[{ci}].offset은 0 이하의 정수여야 합니다 (미래 캔들 참조 불가).")
        conditions = cnd.get("conditions", [])
        if not isinstance(conditions, list) or len(conditions) == 0:
            errors.append(f"candles[{ci}].conditions는 최소 1개 이상이어야 합니다.")
        for cond in conditions:
            if cond.get("field") not in _ALLOWED_FIELDS:
                errors.append(f"허용되지 않는 field: {cond.get('field')}")
            if cond.get("op") not in _ALLOWED_OPS:
                errors.append(f"허용되지 않는 연산자: {cond.get('op')}")
            vtype = cond.get("value_type", "number")
            if vtype not in ("number", "field"):
                errors.append("value_type은 'number' 또는 'field'여야 합니다.")
            if vtype == "field" and cond.get("value") not in _ALLOWED_FIELDS:
                errors.append(f"허용되지 않는 비교 field: {cond.get('value')}")
            if vtype == "number":
                try:
                    float(cond.get("value"))
                except (TypeError, ValueError):
                    errors.append("value_type이 number이면 value는 숫자여야 합니다.")
    return errors


def scan_rule_pattern(o, h, l, c, v, definition: dict) -> list[int]:
    o, h, l, c, v = map(lambda a: np.asarray(a, dtype=float), (o, h, l, c, v))
    n = len(c)
    candles = definition["candles"]
    min_offset = min(cnd["offset"] for cnd in candles)
    matches = []
    for i in range(-min_offset, n):
        ok = True
        for cnd in candles:
            idx = i + cnd["offset"]
            if idx < 0 or idx >= n:
                ok = False
                break
            fields = _bar_fields(o, h, l, c, v, idx)
            for cond in cnd["conditions"]:
                left = fields[cond["field"]]
                if cond.get("value_type") == "field":
                    ref_offset = cond.get("ref_offset")
                    if ref_offset is None:
                        ref_offset = cnd["offset"]
                    ref_idx = i + ref_offset
                    if ref_idx < 0 or ref_idx >= n:
                        ok = False
                        break
                    right = _bar_fields(o, h, l, c, v, ref_idx)[cond["value"]]
                else:
                    right = float(cond["value"])
                op = cond["op"]
                passed = {
                    ">": left > right, "<": left < right,
                    ">=": left >= right, "<=": left <= right,
                    "==": abs(left - right) < 1e-9, "!=": abs(left - right) >= 1e-9,
                }[op]
                if not passed:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            matches.append(i)
    return matches


# ── 모양 템플릿(shape) ────────────────────────────────────────────────────
def make_shape_template(close: np.ndarray, start_idx: int, end_idx: int, resample_len: int = 32) -> dict:
    close = np.asarray(close, dtype=float)
    window = close[start_idx : end_idx + 1]
    if len(window) < 3:
        raise ValueError("템플릿 구간이 너무 짧습니다 (최소 3봉 이상 선택하세요).")
    xs_src = np.linspace(0, 1, len(window))
    xs_dst = np.linspace(0, 1, resample_len)
    resampled = np.interp(xs_dst, xs_src, window)
    mean, std = float(np.mean(resampled)), float(np.std(resampled))
    normalized = ((resampled - mean) / std).tolist() if std > 0 else [0.0] * resample_len
    return {"shape": normalized, "length": int(end_idx - start_idx + 1), "resample_len": resample_len}


def scan_shape_pattern(close: np.ndarray, definition: dict, threshold: float = 0.85) -> list[dict]:
    close = np.asarray(close, dtype=float)
    template = np.asarray(definition["shape"], dtype=float)
    win_len = int(definition["length"])
    resample_len = int(definition.get("resample_len", len(template)))
    n = len(close)
    matches = []
    if win_len < 3 or win_len > n:
        return matches
    i = 0
    while i + win_len <= n:
        window = close[i : i + win_len]
        xs_src = np.linspace(0, 1, win_len)
        xs_dst = np.linspace(0, 1, resample_len)
        resampled = np.interp(xs_dst, xs_src, window)
        std = float(np.std(resampled))
        if std == 0:
            i += max(1, win_len // 4)
            continue
        normalized = (resampled - np.mean(resampled)) / std
        score = float(np.corrcoef(normalized, template)[0, 1])
        if score >= threshold:
            matches.append({"start_idx": i, "end_idx": i + win_len - 1, "score": round(score, 3)})
            i += max(1, win_len // 2)  # 겹치는 중복 매치 방지를 위해 절반만큼 건너뜀
        else:
            i += max(1, win_len // 6)
    return matches
