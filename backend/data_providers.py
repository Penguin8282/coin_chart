"""
시세 데이터 공급자 모음.

이 프로젝트가 돌아가는 개발 샌드박스는 조직 보안 정책으로 바이낸스/업비트/야후/토스 등
외부 API 호스트로 나가는 요청이 전부 막혀 있습니다. 그래서 아래 라이브 어댑터들은
"실제 배포 환경(사용자 PC, 서버, 클라우드 등 일반적으로 아웃바운드가 열려 있는 곳)"에서
동작하도록 정확하게 작성했지만, 이 샌드박스 안에서는 호출 시 전부 실패하고
자동으로 데모(오프라인) 데이터로 폴백합니다. 배포 후에는 그대로 라이브 데이터를 받아옵니다.

제공 소스:
  - Binance 공개 REST API   (코인, 키 불필요)
  - Yahoo Finance chart API (코인/미국주식/한국주식 전부 커버, 키 불필요)
  - Toss Invest 비공식 API  (한국 주식 전용, 리버스엔지니어링 기반 — 토스 쪽에서
    엔드포인트/응답 스펙을 바꾸면 깨질 수 있음. 실패 시 자동으로 야후로 폴백)
  - 데모(합성) 데이터        (완전 오프라인, 결정론적 시드 기반 — 항상 동작)
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, asdict
from typing import Literal

import httpx
import numpy as np

from . import toss_openapi

Market = Literal["crypto", "us", "kr"]

_HTTP_TIMEOUT = 6.0
_UA = "Mozilla/5.0 (compatible; PersonalChartAnalyzer/1.0)"


@dataclass
class Candle:
    t: int  # unix ms
    o: float
    h: float
    l: float
    c: float
    v: float


INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}

# ── 심볼 프리셋 ──────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = {
    "crypto": [
        {"symbol": "BTCUSDT", "name": "Bitcoin", "binance": "BTCUSDT", "yahoo": "BTC-USD"},
        {"symbol": "ETHUSDT", "name": "Ethereum", "binance": "ETHUSDT", "yahoo": "ETH-USD"},
        {"symbol": "SOLUSDT", "name": "Solana", "binance": "SOLUSDT", "yahoo": "SOL-USD"},
        {"symbol": "XRPUSDT", "name": "Ripple", "binance": "XRPUSDT", "yahoo": "XRP-USD"},
        {"symbol": "DOGEUSDT", "name": "Dogecoin", "binance": "DOGEUSDT", "yahoo": "DOGE-USD"},
    ],
    "us": [
        {"symbol": "AAPL", "name": "Apple", "yahoo": "AAPL"},
        {"symbol": "TSLA", "name": "Tesla", "yahoo": "TSLA"},
        {"symbol": "NVDA", "name": "NVIDIA", "yahoo": "NVDA"},
        {"symbol": "MSFT", "name": "Microsoft", "yahoo": "MSFT"},
        {"symbol": "GOOGL", "name": "Alphabet", "yahoo": "GOOGL"},
        {"symbol": "SPY", "name": "S&P 500 ETF", "yahoo": "SPY"},
    ],
    "kr": [
        {"symbol": "005930", "name": "삼성전자", "yahoo": "005930.KS", "toss": "A005930"},
        {"symbol": "000660", "name": "SK하이닉스", "yahoo": "000660.KS", "toss": "A000660"},
        {"symbol": "035420", "name": "NAVER", "yahoo": "035420.KS", "toss": "A035420"},
        {"symbol": "035720", "name": "카카오", "yahoo": "035720.KS", "toss": "A035720"},
        {"symbol": "247540", "name": "에코프로비엠", "yahoo": "247540.KQ", "toss": "A247540"},
    ],
}


def _seed_from(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


# ── 1) Binance ───────────────────────────────────────────────────────────
_BINANCE_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
}


def fetch_binance(symbol: str, interval: str, limit: int = 500) -> list[Candle]:
    bi = _BINANCE_INTERVAL.get(interval)
    if not bi:
        raise ValueError(f"unsupported interval for binance: {interval}")
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": bi, "limit": min(limit, 1000)}
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        raw = r.json()
    out = []
    for row in raw:
        out.append(Candle(t=int(row[0]), o=float(row[1]), h=float(row[2]),
                           l=float(row[3]), c=float(row[4]), v=float(row[5])))
    return out


# ── 2) Yahoo Finance ─────────────────────────────────────────────────────
_YAHOO_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "4h": "60m", "1d": "1d", "1w": "1wk",
}
_YAHOO_RANGE = {
    "1m": "5d", "5m": "1mo", "15m": "1mo", "30m": "1mo",
    "1h": "3mo", "4h": "6mo", "1d": "2y", "1w": "5y",
}


def fetch_yahoo(symbol: str, interval: str, limit: int = 500) -> list[Candle]:
    yi = _YAHOO_INTERVAL.get(interval)
    yr = _YAHOO_RANGE.get(interval)
    if not yi or not yr:
        raise ValueError(f"unsupported interval for yahoo: {interval}")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": yr, "interval": yi, "includePrePost": "false"}
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):
            continue
        out.append(Candle(t=int(t) * 1000, o=float(o), h=float(h), l=float(l),
                           c=float(c), v=float(v or 0.0)))
    # 4h는 야후가 직접 지원하지 않아 1h를 4개씩 리샘플링
    if interval == "4h" and out:
        out = _resample(out, 4)
    return out[-limit:]


def _resample(candles: list[Candle], factor: int) -> list[Candle]:
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i : i + factor]
        out.append(Candle(
            t=chunk[0].t,
            o=chunk[0].o,
            h=max(x.h for x in chunk),
            l=min(x.l for x in chunk),
            c=chunk[-1].c,
            v=sum(x.v for x in chunk),
        ))
    return out


# ── 3) Toss Invest 비공식 API (한국 주식 전용, best-effort) ──────────────
# 참고: 공식 문서가 없는 리버스엔지니어링 API입니다. 응답 스펙/엔드포인트가
# 예고 없이 바뀔 수 있어 실패 시 예외를 던지고 상위에서 야후로 자동 폴백합니다.
_TOSS_PERIOD = {"1d": "day", "1w": "week"}


def fetch_toss(product_code: str, interval: str, limit: int = 500) -> list[Candle]:
    period = _TOSS_PERIOD.get(interval)
    if not period:
        raise ValueError(f"toss adapter only supports daily/weekly, got: {interval}")
    url = f"https://wts-info-api.tossinvest.com/api/v2/stock-prices/{product_code}/{period}"
    params = {"count": min(limit, 500)}
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    rows = data.get("result", data) if isinstance(data, dict) else data
    out = []
    for row in rows:
        # 토스 응답 필드명은 버전에 따라 baseDate/open/high/low/close/volume 등으로 제공됨
        t = row.get("date") or row.get("baseDate") or row.get("time")
        o = row.get("open") or row.get("openPrice")
        h = row.get("high") or row.get("highPrice")
        l = row.get("low") or row.get("lowPrice")
        c = row.get("close") or row.get("closePrice") or row.get("basePrice")
        v = row.get("volume") or row.get("tradingVolume") or 0
        if None in (t, o, h, l, c):
            continue
        ts = int(t) if isinstance(t, (int, float)) else int(time.mktime(time.strptime(str(t)[:10], "%Y-%m-%d")))
        ts_ms = ts * 1000 if ts < 10**12 else ts
        out.append(Candle(t=ts_ms, o=float(o), h=float(h), l=float(l), c=float(c), v=float(v)))
    out.sort(key=lambda x: x.t)
    return out[-limit:]


# ── 4) 데모(합성) 데이터 — 완전 오프라인, 항상 동작 ──────────────────────
def generate_demo(symbol: str, interval: str, limit: int = 500, base_price: float | None = None) -> list[Candle]:
    seed = _seed_from(symbol + interval)
    rng = np.random.RandomState(seed % (2**31 - 1))
    step_sec = INTERVAL_SECONDS.get(interval, 3600)
    now = int(time.time())
    now -= now % step_sec

    if base_price is None:
        # 심볼별로 그럴듯한 기준가 부여
        if symbol.upper().startswith(("BTC",)):
            base_price = 60000 + rng.uniform(-5000, 5000)
        elif symbol.upper().startswith(("ETH",)):
            base_price = 3000 + rng.uniform(-300, 300)
        elif symbol.isdigit():  # 한국 주식 코드
            base_price = rng.uniform(20000, 200000)
        else:
            base_price = rng.uniform(50, 500)

    n = limit
    # 레짐 스위칭 랜덤워크: 추세/횡보 구간을 섞어서 실제 시장처럼 보이게
    prices = [base_price]
    regime_len = max(15, n // 8)
    drift = 0.0
    vol = base_price * 0.006
    for i in range(1, n):
        if i % regime_len == 0:
            drift = rng.choice([-1, -0.3, 0, 0.3, 1]) * base_price * 0.0009
            vol = base_price * rng.uniform(0.003, 0.012)
        shock = rng.normal(drift, vol)
        new_p = max(prices[-1] + shock, base_price * 0.05)
        prices.append(new_p)

    # 교육 목적: 히스토리 중간에 헤드앤숄더 모양 하나를 심어서
    # 패턴 탐지 엔진이 실제로 잡아내는 걸 데모에서 보여줌
    if n > 120:
        _embed_head_and_shoulders(prices, start=n // 2 - 40, scale=base_price * 0.03)

    candles = []
    t = now - step_sec * (n - 1)
    for i in range(n):
        p0 = prices[i - 1] if i > 0 else prices[0]
        p1 = prices[i]
        o = p0
        c = p1
        wick = abs(p1 - p0) * rng.uniform(0.2, 0.9) + vol * rng.uniform(0.1, 0.5)
        h = max(o, c) + abs(wick) * rng.uniform(0.1, 1.0)
        l = min(o, c) - abs(wick) * rng.uniform(0.1, 1.0)
        base_vol = base_price * rng.uniform(50, 200)
        spike = 1.0
        if rng.random() < 0.05:
            spike = rng.uniform(2.2, 4.0)  # 가끔 거래량 스파이크
        v = base_vol * spike
        candles.append(Candle(t=(t + i * step_sec) * 1000, o=float(o), h=float(h),
                               l=float(l), c=float(c), v=float(v)))
    return candles


def _embed_head_and_shoulders(prices: list[float], start: int, scale: float):
    n = len(prices)
    if start < 5 or start + 40 > n:
        return
    pattern = [0, 0.6, 0.25, 1.0, 0.2, 0.65, 0.0]  # L숄더-넥라인-헤드-넥라인-R숄더
    xs = np.linspace(0, len(pattern) - 1, 36)
    ys = np.interp(xs, np.arange(len(pattern)), pattern)
    base = prices[start]
    for i, y in enumerate(ys):
        idx = start + i
        if idx < n:
            prices[idx] = base + y * scale


# ── 오케스트레이션 ────────────────────────────────────────────────────────
def get_candles(market: Market, symbol: str, interval: str = "1d", limit: int = 500) -> dict:
    """market/symbol에 맞는 프로바이더 체인을 순서대로 시도하고, 전부 실패하면
    데모 데이터로 폴백한다. 반환: {"candles": [...], "source": "binance|yahoo|toss|demo"}"""
    preset = next((p for p in DEFAULT_SYMBOLS.get(market, []) if p["symbol"] == symbol), None)

    tries = []
    # 토스증권 공식 Open API (openapi.tossinvest.com) — 한국/미국 주식 모두 커버.
    # interval이 1m/1d 이고 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 환경변수가 설정된 경우에만 시도.
    if market in ("us", "kr") and interval in ("1m", "1d") and toss_openapi.is_configured():
        toss_sym = symbol.upper() if market == "us" else symbol
        tries.append(("toss_openapi", lambda: [Candle(**c) for c in toss_openapi.fetch_candles(toss_sym, interval, limit)]))

    if market == "crypto":
        binance_sym = preset["binance"] if preset else (symbol if symbol.upper().endswith("USDT") else symbol.upper() + "USDT")
        yahoo_sym = preset["yahoo"] if preset else (symbol.upper().replace("USDT", "") + "-USD")
        tries += [("binance", lambda: fetch_binance(binance_sym, interval, limit)),
                  ("yahoo", lambda: fetch_yahoo(yahoo_sym, interval, limit))]
    elif market == "us":
        yahoo_sym = preset["yahoo"] if preset else symbol.upper()
        tries.append(("yahoo", lambda: fetch_yahoo(yahoo_sym, interval, limit)))
    elif market == "kr":
        toss_sym = preset["toss"] if preset else ("A" + symbol if symbol.isdigit() else symbol)
        yahoo_sym = preset["yahoo"] if preset else (symbol + ".KS")
        tries += [("toss_unofficial", lambda: fetch_toss(toss_sym, interval, limit)),
                  ("yahoo", lambda: fetch_yahoo(yahoo_sym, interval, limit))]

    last_err = None
    for name, fn in tries:
        try:
            candles = fn()
            if candles and len(candles) >= 10:
                return {"candles": [asdict(c) for c in candles], "source": name}
        except Exception as e:  # noqa: BLE001 - 폴백 체인이므로 모든 실패를 흡수
            last_err = e
            continue

    demo = generate_demo(symbol, interval, limit)
    return {"candles": [asdict(c) for c in demo], "source": "demo",
            "note": f"실시간 데이터 연결 실패로 데모 데이터를 표시합니다"
                    + (f" ({last_err})" if last_err else "")}
