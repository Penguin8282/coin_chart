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
# ── 심볼 프리셋 ──────────────────────────────────────────────────────────
# aliases는 검색 전용이다. 한글로 "이더리움", 티커로 "ETH" 어느 쪽으로 찾아도
# 걸리도록 별칭을 넣어둔다. 목록에 없는 종목도 티커를 직접 입력해 쓸 수 있으므로
# 여기 있는 것이 지원 종목의 전부는 아니다.
DEFAULT_SYMBOLS = {
    "crypto": [
        {"symbol": "BTCUSDT", "name": "비트코인", "binance": "BTCUSDT", "yahoo": "BTC-USD", "aliases": ["Bitcoin", "BTC"]},
        {"symbol": "ETHUSDT", "name": "이더리움", "binance": "ETHUSDT", "yahoo": "ETH-USD", "aliases": ["Ethereum", "ETH"]},
        {"symbol": "XRPUSDT", "name": "리플", "binance": "XRPUSDT", "yahoo": "XRP-USD", "aliases": ["Ripple", "XRP"]},
        {"symbol": "SOLUSDT", "name": "솔라나", "binance": "SOLUSDT", "yahoo": "SOL-USD", "aliases": ["Solana", "SOL"]},
        {"symbol": "BNBUSDT", "name": "BNB", "binance": "BNBUSDT", "yahoo": "BNB-USD", "aliases": ["Binance Coin", "BNB"]},
        {"symbol": "DOGEUSDT", "name": "도지코인", "binance": "DOGEUSDT", "yahoo": "DOGE-USD", "aliases": ["Dogecoin", "DOGE"]},
        {"symbol": "ADAUSDT", "name": "에이다", "binance": "ADAUSDT", "yahoo": "ADA-USD", "aliases": ["Cardano", "ADA"]},
        {"symbol": "AVAXUSDT", "name": "아발란체", "binance": "AVAXUSDT", "yahoo": "AVAX-USD", "aliases": ["Avalanche", "AVAX"]},
        {"symbol": "LINKUSDT", "name": "체인링크", "binance": "LINKUSDT", "yahoo": "LINK-USD", "aliases": ["Chainlink", "LINK"]},
        {"symbol": "DOTUSDT", "name": "폴카닷", "binance": "DOTUSDT", "yahoo": "DOT-USD", "aliases": ["Polkadot", "DOT"]},
        {"symbol": "TRXUSDT", "name": "트론", "binance": "TRXUSDT", "yahoo": "TRX-USD", "aliases": ["TRON", "TRX"]},
        {"symbol": "LTCUSDT", "name": "라이트코인", "binance": "LTCUSDT", "yahoo": "LTC-USD", "aliases": ["Litecoin", "LTC"]},
        {"symbol": "BCHUSDT", "name": "비트코인캐시", "binance": "BCHUSDT", "yahoo": "BCH-USD", "aliases": ["Bitcoin Cash", "BCH"]},
        {"symbol": "ATOMUSDT", "name": "코스모스", "binance": "ATOMUSDT", "yahoo": "ATOM-USD", "aliases": ["Cosmos", "ATOM"]},
        {"symbol": "UNIUSDT", "name": "유니스왑", "binance": "UNIUSDT", "yahoo": "UNI-USD", "aliases": ["Uniswap", "UNI"]},
        {"symbol": "NEARUSDT", "name": "니어프로토콜", "binance": "NEARUSDT", "yahoo": "NEAR-USD", "aliases": ["NEAR Protocol", "NEAR"]},
        {"symbol": "APTUSDT", "name": "앱토스", "binance": "APTUSDT", "yahoo": "APT-USD", "aliases": ["Aptos", "APT"]},
        {"symbol": "ARBUSDT", "name": "아비트럼", "binance": "ARBUSDT", "yahoo": "ARB-USD", "aliases": ["Arbitrum", "ARB"]},
        {"symbol": "OPUSDT", "name": "옵티미즘", "binance": "OPUSDT", "yahoo": "OP-USD", "aliases": ["Optimism", "OP"]},
        {"symbol": "SUIUSDT", "name": "수이", "binance": "SUIUSDT", "yahoo": "SUI-USD", "aliases": ["Sui", "SUI"]},
        {"symbol": "FILUSDT", "name": "파일코인", "binance": "FILUSDT", "yahoo": "FIL-USD", "aliases": ["Filecoin", "FIL"]},
        {"symbol": "ETCUSDT", "name": "이더리움클래식", "binance": "ETCUSDT", "yahoo": "ETC-USD", "aliases": ["Ethereum Classic", "ETC"]},
        {"symbol": "SHIBUSDT", "name": "시바이누", "binance": "SHIBUSDT", "yahoo": "SHIB-USD", "aliases": ["Shiba Inu", "SHIB"]},
        {"symbol": "SANDUSDT", "name": "샌드박스", "binance": "SANDUSDT", "yahoo": "SAND-USD", "aliases": ["The Sandbox", "SAND"]},
        {"symbol": "AAVEUSDT", "name": "에이브", "binance": "AAVEUSDT", "yahoo": "AAVE-USD", "aliases": ["Aave", "AAVE"]},
    ],
    "us": [
        {"symbol": "AAPL", "name": "애플", "yahoo": "AAPL", "aliases": ["Apple", "AAPL"]},
        {"symbol": "MSFT", "name": "마이크로소프트", "yahoo": "MSFT", "aliases": ["Microsoft", "MSFT"]},
        {"symbol": "NVDA", "name": "엔비디아", "yahoo": "NVDA", "aliases": ["NVIDIA", "NVDA"]},
        {"symbol": "GOOGL", "name": "알파벳(구글)", "yahoo": "GOOGL", "aliases": ["Alphabet", "Google", "GOOGL"]},
        {"symbol": "AMZN", "name": "아마존", "yahoo": "AMZN", "aliases": ["Amazon", "AMZN"]},
        {"symbol": "META", "name": "메타(페이스북)", "yahoo": "META", "aliases": ["Meta Platforms", "Facebook", "META"]},
        {"symbol": "TSLA", "name": "테슬라", "yahoo": "TSLA", "aliases": ["Tesla", "TSLA"]},
        {"symbol": "AVGO", "name": "브로드컴", "yahoo": "AVGO", "aliases": ["Broadcom", "AVGO"]},
        {"symbol": "NFLX", "name": "넷플릭스", "yahoo": "NFLX", "aliases": ["Netflix", "NFLX"]},
        {"symbol": "AMD", "name": "AMD", "yahoo": "AMD", "aliases": ["Advanced Micro Devices", "AMD"]},
        {"symbol": "INTC", "name": "인텔", "yahoo": "INTC", "aliases": ["Intel", "INTC"]},
        {"symbol": "QCOM", "name": "퀄컴", "yahoo": "QCOM", "aliases": ["Qualcomm", "QCOM"]},
        {"symbol": "ORCL", "name": "오라클", "yahoo": "ORCL", "aliases": ["Oracle", "ORCL"]},
        {"symbol": "CRM", "name": "세일즈포스", "yahoo": "CRM", "aliases": ["Salesforce", "CRM"]},
        {"symbol": "ADBE", "name": "어도비", "yahoo": "ADBE", "aliases": ["Adobe", "ADBE"]},
        {"symbol": "PLTR", "name": "팔란티어", "yahoo": "PLTR", "aliases": ["Palantir", "PLTR"]},
        {"symbol": "COIN", "name": "코인베이스", "yahoo": "COIN", "aliases": ["Coinbase", "COIN"]},
        {"symbol": "MSTR", "name": "마이크로스트래티지", "yahoo": "MSTR", "aliases": ["MicroStrategy", "Strategy", "MSTR"]},
        {"symbol": "UBER", "name": "우버", "yahoo": "UBER", "aliases": ["Uber", "UBER"]},
        {"symbol": "DIS", "name": "디즈니", "yahoo": "DIS", "aliases": ["Disney", "DIS"]},
        {"symbol": "JPM", "name": "JP모건", "yahoo": "JPM", "aliases": ["JPMorgan Chase", "JPM"]},
        {"symbol": "V", "name": "비자", "yahoo": "V", "aliases": ["Visa", "V"]},
        {"symbol": "WMT", "name": "월마트", "yahoo": "WMT", "aliases": ["Walmart", "WMT"]},
        {"symbol": "KO", "name": "코카콜라", "yahoo": "KO", "aliases": ["Coca-Cola", "KO"]},
        {"symbol": "BA", "name": "보잉", "yahoo": "BA", "aliases": ["Boeing", "BA"]},
        {"symbol": "SPY", "name": "S&P500 ETF", "yahoo": "SPY", "aliases": ["SPDR S&P 500", "SPY"]},
        {"symbol": "QQQ", "name": "나스닥100 ETF", "yahoo": "QQQ", "aliases": ["Invesco QQQ", "QQQ"]},
        {"symbol": "VOO", "name": "뱅가드 S&P500 ETF", "yahoo": "VOO", "aliases": ["Vanguard", "VOO"]},
        {"symbol": "SCHD", "name": "슈드 배당 ETF", "yahoo": "SCHD", "aliases": ["Schwab Dividend", "SCHD"]},
        {"symbol": "TQQQ", "name": "나스닥100 3배 ETF", "yahoo": "TQQQ", "aliases": ["ProShares UltraPro QQQ", "TQQQ"]},
        {"symbol": "SOXL", "name": "반도체 3배 ETF", "yahoo": "SOXL", "aliases": ["Direxion Semiconductor", "SOXL"]},
    ],
    "kr": [
        {"symbol": "005930", "name": "삼성전자", "yahoo": "005930.KS", "toss": "A005930", "aliases": ["Samsung Electronics"]},
        {"symbol": "000660", "name": "SK하이닉스", "yahoo": "000660.KS", "toss": "A000660", "aliases": ["SK Hynix"]},
        {"symbol": "373220", "name": "LG에너지솔루션", "yahoo": "373220.KS", "toss": "A373220", "aliases": ["LG Energy Solution", "엘지에너지솔루션"]},
        {"symbol": "207940", "name": "삼성바이오로직스", "yahoo": "207940.KS", "toss": "A207940", "aliases": ["Samsung Biologics"]},
        {"symbol": "005380", "name": "현대차", "yahoo": "005380.KS", "toss": "A005380", "aliases": ["Hyundai Motor", "현대자동차"]},
        {"symbol": "000270", "name": "기아", "yahoo": "000270.KS", "toss": "A000270", "aliases": ["Kia"]},
        {"symbol": "068270", "name": "셀트리온", "yahoo": "068270.KS", "toss": "A068270", "aliases": ["Celltrion"]},
        {"symbol": "005490", "name": "POSCO홀딩스", "yahoo": "005490.KS", "toss": "A005490", "aliases": ["포스코", "POSCO"]},
        {"symbol": "035420", "name": "NAVER", "yahoo": "035420.KS", "toss": "A035420", "aliases": ["네이버", "Naver"]},
        {"symbol": "035720", "name": "카카오", "yahoo": "035720.KS", "toss": "A035720", "aliases": ["Kakao"]},
        {"symbol": "051910", "name": "LG화학", "yahoo": "051910.KS", "toss": "A051910", "aliases": ["LG Chem", "엘지화학"]},
        {"symbol": "006400", "name": "삼성SDI", "yahoo": "006400.KS", "toss": "A006400", "aliases": ["Samsung SDI"]},
        {"symbol": "012330", "name": "현대모비스", "yahoo": "012330.KS", "toss": "A012330", "aliases": ["Hyundai Mobis"]},
        {"symbol": "028260", "name": "삼성물산", "yahoo": "028260.KS", "toss": "A028260", "aliases": ["Samsung C&T"]},
        {"symbol": "018260", "name": "삼성에스디에스", "yahoo": "018260.KS", "toss": "A018260", "aliases": ["Samsung SDS", "삼성SDS"]},
        {"symbol": "032830", "name": "삼성생명", "yahoo": "032830.KS", "toss": "A032830", "aliases": ["Samsung Life"]},
        {"symbol": "105560", "name": "KB금융", "yahoo": "105560.KS", "toss": "A105560", "aliases": ["KB Financial"]},
        {"symbol": "055550", "name": "신한지주", "yahoo": "055550.KS", "toss": "A055550", "aliases": ["Shinhan"]},
        {"symbol": "086790", "name": "하나금융지주", "yahoo": "086790.KS", "toss": "A086790", "aliases": ["Hana Financial"]},
        {"symbol": "010130", "name": "고려아연", "yahoo": "010130.KS", "toss": "A010130", "aliases": ["Korea Zinc"]},
        {"symbol": "015760", "name": "한국전력", "yahoo": "015760.KS", "toss": "A015760", "aliases": ["KEPCO", "한전"]},
        {"symbol": "034020", "name": "두산에너빌리티", "yahoo": "034020.KS", "toss": "A034020", "aliases": ["Doosan Enerbility", "두산중공업"]},
        {"symbol": "042660", "name": "한화오션", "yahoo": "042660.KS", "toss": "A042660", "aliases": ["Hanwha Ocean", "대우조선해양"]},
        {"symbol": "009540", "name": "HD한국조선해양", "yahoo": "009540.KS", "toss": "A009540", "aliases": ["HD Korea Shipbuilding", "한국조선해양"]},
        {"symbol": "011200", "name": "HMM", "yahoo": "011200.KS", "toss": "A011200", "aliases": ["에이치엠엠", "현대상선"]},
        {"symbol": "003670", "name": "포스코퓨처엠", "yahoo": "003670.KS", "toss": "A003670", "aliases": ["POSCO Future M", "포스코케미칼"]},
        {"symbol": "000100", "name": "유한양행", "yahoo": "000100.KS", "toss": "A000100", "aliases": ["Yuhan"]},
        {"symbol": "128940", "name": "한미약품", "yahoo": "128940.KS", "toss": "A128940", "aliases": ["Hanmi Pharm"]},
        {"symbol": "139480", "name": "이마트", "yahoo": "139480.KS", "toss": "A139480", "aliases": ["Emart"]},
        {"symbol": "097950", "name": "CJ제일제당", "yahoo": "097950.KS", "toss": "A097950", "aliases": ["CJ CheilJedang"]},
        {"symbol": "271560", "name": "오리온", "yahoo": "271560.KS", "toss": "A271560", "aliases": ["Orion"]},
        {"symbol": "247540", "name": "에코프로비엠", "yahoo": "247540.KQ", "toss": "A247540", "aliases": ["EcoPro BM"]},
        {"symbol": "086520", "name": "에코프로", "yahoo": "086520.KQ", "toss": "A086520", "aliases": ["EcoPro"]},
        {"symbol": "196170", "name": "알테오젠", "yahoo": "196170.KQ", "toss": "A196170", "aliases": ["Alteogen"]},
        {"symbol": "058470", "name": "리노공업", "yahoo": "058470.KQ", "toss": "A058470", "aliases": ["Leeno Industrial"]},
        {"symbol": "328130", "name": "루닛", "yahoo": "328130.KQ", "toss": "A328130", "aliases": ["Lunit"]},
        {"symbol": "069500", "name": "KODEX 200", "yahoo": "069500.KS", "toss": "A069500", "aliases": ["코덱스200", "KOSPI200 ETF"]},
        {"symbol": "122630", "name": "KODEX 레버리지", "yahoo": "122630.KS", "toss": "A122630", "aliases": ["코덱스 레버리지"]},
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


# ── 1-2) 코인 거래소 선택 ────────────────────────────────────────────────
# 같은 코인이라도 거래소마다 가격·표시통화가 다르다. 국내 거래소는 원화(KRW),
# 해외 거래소는 달러(USDT/USD) 기준이라 김치프리미엄만큼 차이가 난다.
# 사용자가 어디 시세를 볼지 직접 고를 수 있게 어댑터를 나눠 둔다.
CRYPTO_EXCHANGES = {
    "binance":  {"label": "바이낸스",   "currency": "USDT", "region": "해외"},
    "upbit":    {"label": "업비트",     "currency": "KRW",  "region": "국내"},
    "bithumb":  {"label": "빗썸",       "currency": "KRW",  "region": "국내"},
    "coinbase": {"label": "코인베이스", "currency": "USD",  "region": "해외"},
}
DEFAULT_CRYPTO_EXCHANGE = "binance"


def crypto_base(symbol: str) -> str:
    """거래 페어 심볼에서 기초자산만 뽑는다. BTCUSDT → BTC, KRW-BTC → BTC."""
    s = symbol.upper().replace("-", "").replace("_", "")
    for prefix in ("KRW", "USDT", "USDC", "BUSD", "USD"):
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
            break
    for quote in ("USDT", "USDC", "BUSD", "KRW", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


# ── Upbit (국내, KRW) ────────────────────────────────────────────────────
_UPBIT_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def fetch_upbit(base: str, interval: str, limit: int = 500) -> list[Candle]:
    market_code = f"KRW-{base}"
    if interval in _UPBIT_MINUTES:
        url = f"https://api.upbit.com/v1/candles/minutes/{_UPBIT_MINUTES[interval]}"
    elif interval == "1d":
        url = "https://api.upbit.com/v1/candles/days"
    elif interval == "1w":
        url = "https://api.upbit.com/v1/candles/weeks"
    else:
        raise ValueError(f"업비트가 지원하지 않는 간격입니다: {interval}")

    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
        r = client.get(url, params={"market": market_code, "count": min(limit, 200)})
        r.raise_for_status()
        rows = r.json()
    if not isinstance(rows, list):
        raise ValueError("업비트 응답 형식이 예상과 다릅니다")

    out = []
    for row in rows:
        out.append(Candle(
            t=int(row["timestamp"]),
            o=float(row["opening_price"]), h=float(row["high_price"]),
            l=float(row["low_price"]), c=float(row["trade_price"]),
            v=float(row.get("candle_acc_trade_volume", 0.0)),
        ))
    out.sort(key=lambda x: x.t)   # 업비트는 최신순으로 주므로 뒤집는다
    return out


# ── Bithumb (국내, KRW) ──────────────────────────────────────────────────
_BITHUMB_INTERVAL = {"1m": "1m", "5m": "5m", "30m": "30m", "1h": "1h", "1d": "24h"}


def fetch_bithumb(base: str, interval: str, limit: int = 500) -> list[Candle]:
    bi = _BITHUMB_INTERVAL.get(interval)
    if not bi:
        raise ValueError(f"빗썸이 지원하지 않는 간격입니다: {interval}")
    url = f"https://api.bithumb.com/public/candlestick/{base}_KRW/{bi}"
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
        r = client.get(url)
        r.raise_for_status()
        data = r.json()
    if data.get("status") != "0000":
        raise ValueError(f"빗썸 오류 응답: {data.get('status')}")

    out = []
    for row in data.get("data", []):
        # [기준시각(ms), 시가, 종가, 고가, 저가, 거래량] — 종가가 두 번째임에 유의
        out.append(Candle(
            t=int(row[0]), o=float(row[1]), h=float(row[3]),
            l=float(row[4]), c=float(row[2]), v=float(row[5]),
        ))
    out.sort(key=lambda x: x.t)
    return out[-limit:]


# ── Coinbase (해외, USD) ─────────────────────────────────────────────────
_COINBASE_GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}


def fetch_coinbase(base: str, interval: str, limit: int = 500) -> list[Candle]:
    gran = _COINBASE_GRAN.get(interval)
    if not gran:
        raise ValueError(f"코인베이스가 지원하지 않는 간격입니다: {interval}")
    url = f"https://api.exchange.coinbase.com/products/{base}-USD/candles"
    with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
        r = client.get(url, params={"granularity": gran})
        r.raise_for_status()
        rows = r.json()
    if not isinstance(rows, list):
        raise ValueError("코인베이스 응답 형식이 예상과 다릅니다")

    out = []
    for row in rows:
        # [시각(초), 저가, 고가, 시가, 종가, 거래량]
        out.append(Candle(
            t=int(row[0]) * 1000, o=float(row[3]), h=float(row[2]),
            l=float(row[1]), c=float(row[4]), v=float(row[5]),
        ))
    out.sort(key=lambda x: x.t)
    return out[-limit:]


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
def get_candles(market: Market, symbol: str, interval: str = "1d", limit: int = 500,
                exchange: str | None = None) -> dict:
    """market/symbol에 맞는 프로바이더 체인을 순서대로 시도하고, 전부 실패하면
    데모 데이터로 폴백한다.

    코인은 exchange로 거래소를 지정할 수 있다. 지정한 거래소가 해당 간격이나
    종목을 지원하지 않으면 다른 거래소로 넘어가되, 어디서 가져왔는지를 source와
    note로 정확히 알려준다 — 원화 거래소와 달러 거래소는 가격 자체가 다르므로
    출처를 숨기면 안 된다.

    반환: {"candles": [...], "source": str, "currency": str, "note": str|None}
    """
    preset = next((p for p in DEFAULT_SYMBOLS.get(market, []) if p["symbol"] == symbol), None)

    tries: list[tuple[str, object]] = []
    requested_label = None

    if market == "crypto":
        base = crypto_base(preset["symbol"] if preset else symbol)
        binance_sym = preset["binance"] if preset else (symbol if symbol.upper().endswith("USDT") else base + "USDT")
        yahoo_sym = preset["yahoo"] if preset else (base + "-USD")

        builders = {
            "binance":  lambda: fetch_binance(binance_sym, interval, limit),
            "upbit":    lambda: fetch_upbit(base, interval, limit),
            "bithumb":  lambda: fetch_bithumb(base, interval, limit),
            "coinbase": lambda: fetch_coinbase(base, interval, limit),
        }
        chosen = exchange if exchange in builders else DEFAULT_CRYPTO_EXCHANGE
        requested_label = CRYPTO_EXCHANGES[chosen]["label"]

        # 고른 거래소를 먼저, 나머지는 기본 순서대로 뒤에 붙인다
        order = [chosen] + [k for k in ("binance", "upbit", "bithumb", "coinbase") if k != chosen]
        tries = [(k, builders[k]) for k in order]
        tries.append(("yahoo", lambda: fetch_yahoo(yahoo_sym, interval, limit)))

    else:
        # 토스증권 공식 Open API — 한국/미국 주식 모두 커버.
        # interval이 1m/1d 이고 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET가 설정된 경우에만 시도.
        if interval in ("1m", "1d") and toss_openapi.is_configured():
            toss_sym = symbol.upper() if market == "us" else symbol
            tries.append(("toss_openapi", lambda: [Candle(**c) for c in toss_openapi.fetch_candles(toss_sym, interval, limit)]))

        if market == "us":
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
                note = None
                if market == "crypto" and name != (exchange or DEFAULT_CRYPTO_EXCHANGE):
                    used = CRYPTO_EXCHANGES.get(name, {}).get("label", name)
                    note = f"{requested_label}에서 가져오지 못해 {used} 시세로 대신 표시합니다"
                return {
                    "candles": [asdict(c) for c in candles],
                    "source": name,
                    "currency": _source_currency(market, name),
                    "note": note,
                }
        except Exception as e:  # noqa: BLE001 - 폴백 체인이므로 모든 실패를 흡수
            last_err = e
            continue

    demo = generate_demo(symbol, interval, limit)
    return {"candles": [asdict(c) for c in demo], "source": "demo",
            "currency": _source_currency(market, "demo"),
            "note": "실시간 데이터 연결 실패로 데모 데이터를 표시합니다"
                    + (f" ({last_err})" if last_err else "")}


def _source_currency(market: Market, source: str) -> str:
    if source in CRYPTO_EXCHANGES:
        return CRYPTO_EXCHANGES[source]["currency"]
    if market == "kr":
        return "KRW"
    if market == "crypto":
        return "USD"
    return "USD"
