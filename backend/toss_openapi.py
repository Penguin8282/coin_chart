"""토스증권 공식 Open API 연동 (2026-08 정식 출시, https://openapi.tossinvest.com).

⚠️ 자격증명(client_id / client_secret)은 코드에 절대 하드코딩하지 않는다.
   반드시 환경변수 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 로만 주입한다.
   (.env 파일을 쓸 경우 .gitignore에 반드시 포함시킬 것 — 이 저장소는 이미 .env를 무시하도록 설정됨)

인증: OAuth2 Client Credentials Grant
  POST /oauth2/token  (application/x-www-form-urlencoded)
    grant_type=client_credentials&client_id=...&client_secret=...
  → { access_token, token_type: "Bearer", expires_in }
  이후 모든 요청에 Authorization: Bearer <access_token> 헤더 사용.

시세 엔드포인트 (공개 GitHub 비공식 SDK github.com/nbsp1221/tossinvest-openapi 의
generated OpenAPI 스펙 기준 — 공식 문서(developers.tossinvest.com)는 이 샌드박스에서
아웃바운드가 막혀 있어 직접 열람하지 못했으므로, 실제 배포 환경에서 최초 연동 시
공식 문서와 대조해 확인할 것을 권장한다):
  GET /api/v1/candles?symbol=&interval=(1m|1d)&count=(<=200)&before=&adjusted=
  GET /api/v1/prices?symbols=콤마구분

symbol 형식: KRX는 6자리 숫자(예: 005930), 미국은 영문 티커(예: AAPL) — 즉 이 API 하나로
한국주식과 미국주식을 동시에 커버할 수 있다.

⚠️ interval은 "1m"과 "1d"만 문서상 확인됨. 그 외 간격(5m/15m/30m/1h/4h/1w) 요청은
   이 어댑터에서 지원하지 않아 예외를 던지고, 상위 폴백 체인이 야후 등으로 넘어간다.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

BASE_URL = "https://openapi.tossinvest.com"
_HTTP_TIMEOUT = 8.0
_SUPPORTED_INTERVALS = {"1m", "1d"}


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # unix epoch seconds


_token_cache: _CachedToken | None = None


def _get_credentials() -> tuple[str, str] | None:
    client_id = os.environ.get("TOSS_CLIENT_ID")
    client_secret = os.environ.get("TOSS_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def _get_access_token() -> str:
    global _token_cache
    if _token_cache and _token_cache.expires_at > time.time():
        return _token_cache.access_token

    creds = _get_credentials()
    if not creds:
        raise RuntimeError(
            "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수가 설정되지 않았습니다. "
            "토스증권 Open API 콘솔에서 발급받은 두 값을 배포 환경변수로 설정하세요."
        )
    client_id, client_secret = creds

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.post(
            f"{BASE_URL}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        )
        r.raise_for_status()
        data = r.json()

    token = data["access_token"]
    expires_in = float(data.get("expires_in", 300))
    _token_cache = _CachedToken(access_token=token, expires_at=time.time() + expires_in - 30)
    return token


def fetch_candles(symbol: str, interval: str, limit: int = 200) -> list[dict]:
    """반환: [{t(ms), o, h, l, c, v}, ...] 시간순 오름차순."""
    if interval not in _SUPPORTED_INTERVALS:
        raise ValueError(f"토스 Open API는 interval={interval}을(를) 지원하지 않습니다 (1m/1d만 지원)")

    token = _get_access_token()
    out: list[dict] = []
    before = None
    remaining = min(limit, 1000)
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        while remaining > 0:
            params = {"symbol": symbol, "interval": interval, "count": min(remaining, 200)}
            if before:
                params["before"] = before
            r = client.get(
                f"{BASE_URL}/api/v1/candles",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            page = r.json()
            candles = page.get("candles", [])
            if not candles:
                break
            for row in candles:
                ts = row["timestamp"]
                # ISO8601 문자열 또는 epoch 초/밀리초 모두 방어적으로 처리
                if isinstance(ts, str):
                    from datetime import datetime
                    t_ms = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
                else:
                    t_ms = int(ts) if ts > 10**12 else int(ts) * 1000
                out.append({
                    "t": t_ms, "o": float(row["openPrice"]), "h": float(row["highPrice"]),
                    "l": float(row["lowPrice"]), "c": float(row["closePrice"]), "v": float(row.get("volume", 0)),
                })
            remaining -= len(candles)
            before = page.get("nextBefore")
            if not before:
                break

    out.sort(key=lambda x: x["t"])
    return out[-limit:]


def is_configured() -> bool:
    return _get_credentials() is not None
