from __future__ import annotations

import base64
import math
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # .env 파일이 있으면 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 등을 환경변수로 로드 (git 제외 대상)

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from . import db
from .data_providers import (get_candles, probe_providers, DEFAULT_SYMBOLS,
                             CRYPTO_EXCHANGES, DEFAULT_CRYPTO_EXCHANGE)
from .scoring import compute_signals
from .range_filter_fbb import compute_range_filter, compute_fibonacci_bb
from .patterns import find_pivots, detect_candlestick_patterns, detect_chart_patterns
from .patterns.custom_engine import (
    validate_rule_definition, scan_rule_pattern, make_shape_template, scan_shape_pattern,
)
from .indicators import atr as atr_fn
from .screener import resolve_targets, run_screen, MAX_TARGETS
from .backtest import run_backtest
from .models import (RulePatternCreate, ShapePatternCreate, WatchlistItem,
                     RegisterBody, LoginBody, TradeCreate)
from . import auth

app = FastAPI(title="개인 차트 분석 시스템")

# 프론트엔드는 백엔드와 같은 오리진에서 서빙되므로 기본적으로 CORS 개방이 필요 없다.
# 외부 도메인에서 이 API를 직접 호출해야 한다면 ALLOWED_ORIGINS에 콤마로 구분해 지정한다.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])


# ── 선택적 접근 보호 ──────────────────────────────────────────────────────
# 공개 URL로 배포하면 링크를 아는 누구나 커스텀 패턴/관심종목을 수정할 수 있다.
# APP_PASSWORD 환경변수를 설정하면 브라우저 기본 로그인창으로 전체 사이트를 보호한다.
# (미설정 시 보호 없음 — 로컬에서 혼자 쓸 때는 그대로 두면 된다.)
_APP_USER = os.environ.get("APP_USER", "admin")
_APP_PASSWORD = os.environ.get("APP_PASSWORD")

# 헬스체크는 인증에서 제외한다. 호스팅 플랫폼(Render 등)의 헬스체크는
# Authorization 헤더를 보내지 않으므로, 보호를 걸면 401만 돌려받고
# 서비스가 죽은 것으로 판정해 배포가 실패한다.
HEALTH_PATH = "/healthz"

if _APP_PASSWORD:
    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if request.url.path == HEALTH_PATH:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"{_APP_USER}:{_APP_PASSWORD}".encode()).decode()
        if not secrets.compare_digest(header, expected):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="chart"'},
                content="인증이 필요합니다",
            )
        return await call_next(request)


@app.get(HEALTH_PATH)
def healthz():
    """배포 플랫폼용 헬스체크. 인증 없이 200을 돌려주며 민감한 정보를 노출하지 않는다."""
    return {"ok": True}


db.init_db()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _clean(v):
    """NaN/Infinity → None (JSON 직렬화 안전화), numpy 스칼라 → 파이썬 스칼라."""
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_clean(x) for x in v]
    return v


def _tp_params(tp_mode: str, atr_tp_mult: float, atr_sl_mult: float) -> dict:
    """손절·목표 계산 방식 파라미터 검증. 잘못된 값은 조용히 기본값으로
    바꾸지 않고 400으로 알린다 — 사용자가 고른 값이 무시되면 더 혼란스럽다."""
    if tp_mode not in ("pct", "atr"):
        raise HTTPException(400, "tp_mode는 pct 또는 atr이어야 합니다")
    if not (0.2 <= atr_tp_mult <= 10):
        raise HTTPException(400, "ATR 목표 배수는 0.2~10 사이여야 합니다")
    if not (0.2 <= atr_sl_mult <= 5):
        raise HTTPException(400, "ATR 손절 배수는 0.2~5 사이여야 합니다")
    return {"tp_mode": tp_mode, "atr_tp_mult": atr_tp_mult, "atr_sl_mult": atr_sl_mult}


@app.get("/api/markets")
def markets():
    return _clean({
        "symbols": DEFAULT_SYMBOLS,
        "exchanges": [{"id": k, **v} for k, v in CRYPTO_EXCHANGES.items()],
        "default_exchange": DEFAULT_CRYPTO_EXCHANGE,
    })


@app.get("/api/candles")
def candles(market: str, symbol: str, interval: str = "1d", limit: int = 500,
            exchange: str | None = None):
    if market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    limit = max(60, min(limit, 1000))
    result = get_candles(market, symbol, interval, limit, exchange=exchange)
    return _clean(result)


def _ohlcv_arrays(candle_rows: list[dict]):
    o = np.array([r["o"] for r in candle_rows], dtype=float)
    h = np.array([r["h"] for r in candle_rows], dtype=float)
    l = np.array([r["l"] for r in candle_rows], dtype=float)
    c = np.array([r["c"] for r in candle_rows], dtype=float)
    v = np.array([r["v"] for r in candle_rows], dtype=float)
    return o, h, l, c, v


@app.get("/api/analysis")
def analysis(request: Request, market: str, symbol: str, interval: str = "1d", limit: int = 500,
             vol_len: int = 20, fib_len: int = 100, adx_thr: float = 25.0,
             tp_mode: str = "pct", atr_tp_mult: float = 2.2, atr_sl_mult: float = 1.0,
             exchange: str | None = None):
    if market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    tp_kw = _tp_params(tp_mode, atr_tp_mult, atr_sl_mult)   # 시세 조회 전에 검증
    limit = max(60, min(limit, 1000))
    fetched = get_candles(market, symbol, interval, limit, exchange=exchange)
    rows = fetched["candles"]
    if len(rows) < 30:
        raise HTTPException(422, "분석하기엔 캔들 데이터가 너무 적습니다")

    o, h, l, c, v = _ohlcv_arrays(rows)
    signals = compute_signals(o, h, l, c, v, vol_len=vol_len, fib_len=min(fib_len, len(c)), adx_thr=adx_thr, **tp_kw)

    a = atr_fn(h, l, c, 14)
    pivots = find_pivots(h, l, order=4, atr=a, min_move_atr=1.2)
    chart_patterns = detect_chart_patterns(c, pivots, tol=0.025, confirm_window=30)
    candle_patterns = detect_candlestick_patterns(o, h, l, c)

    rf = compute_range_filter(c, period=20, mult=3.5)
    hlc3 = (h + l + c) / 3.0
    fbb = compute_fibonacci_bb(hlc3, v, length=min(200, len(c)), mult=3.0)

    custom_matches = []
    user = current_user(request)
    for pat in (db.list_custom_patterns(user["id"]) if user else []):
        try:
            if pat["type"] == "rule":
                idxs = scan_rule_pattern(o, h, l, c, v, pat["definition"])
                for idx in idxs:
                    custom_matches.append({"pattern_id": pat["id"], "name": pat["name"],
                                            "direction": pat["direction"], "index": idx, "kind": "custom_rule"})
            else:
                matches = scan_shape_pattern(c, pat["definition"], threshold=0.85)
                for m in matches:
                    custom_matches.append({"pattern_id": pat["id"], "name": pat["name"],
                                            "direction": pat["direction"], **m, "kind": "custom_shape"})
        except Exception:
            continue

    return _clean({
        "source": fetched["source"], "note": fetched.get("note"),
        "currency": fetched.get("currency", "USD"),
        "candles": rows,
        "series": signals["series"],
        "events": signals["events"],
        "dashboard": signals["dashboard"],
        "pivots": pivots,
        "chart_patterns": chart_patterns,
        "candle_patterns": candle_patterns,
        "custom_matches": custom_matches,
        "range_filter": rf,
        "fibonacci_bb": fbb,
    })


@app.get("/api/diag")
def diag(market: str = "crypto", symbol: str = "BTCUSDT", interval: str = "1h"):
    """데이터 소스별 연결 상태를 있는 그대로 보고한다.
    폴백 체인이 실패를 삼켜버리기 때문에, 배포 후 "왜 이 거래소가 안 잡히는지"를
    확인하려면 이 엔드포인트를 열어보면 된다. 접근 보호가 켜져 있으면 로그인이 필요하다."""
    if market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    return _clean({
        "market": market, "symbol": symbol, "interval": interval,
        "results": probe_providers(market, symbol, interval),
    })


@app.get("/api/screener")
def screener(request: Request, scope: str = "watchlist", interval: str = "1h", limit: int = 300,
             exchange: str | None = None, min_score: int = 0, direction: str = "any",
             vol_len: int = 20, fib_len: int = 100, adx_thr: float = 25.0):
    """여러 종목을 한 번에 훑어 신호 점수순으로 돌려준다."""
    if scope not in ("watchlist", "crypto", "us", "kr", "all"):
        raise HTTPException(400, "scope는 watchlist|crypto|us|kr|all 중 하나여야 합니다")
    if direction not in ("any", "buy", "sell"):
        raise HTTPException(400, "direction은 any|buy|sell 중 하나여야 합니다")

    user = current_user(request)
    my_watch = db.list_watchlist(user["id"]) if user else []
    targets = resolve_targets(scope, my_watch)
    result = run_screen(
        targets, interval=interval, limit=max(120, min(limit, 600)), exchange=exchange,
        params={"vol_len": vol_len, "fib_len": fib_len, "adx_thr": adx_thr},
        min_score=max(0, min(min_score, 22)), direction=direction,
    )
    result["scope"] = scope
    result["max_targets"] = MAX_TARGETS
    return _clean(result)


@app.get("/api/backtest")
def backtest(market: str, symbol: str, interval: str = "1h", limit: int = 600,
             exchange: str | None = None, min_score: int = 7, max_bars: int = 48,
             fee_pct: float = 0.1, vol_len: int = 20, fib_len: int = 100,
             adx_thr: float = 25.0, tp_mode: str = "pct",
             atr_tp_mult: float = 2.2, atr_sl_mult: float = 1.0):
    """이 신호를 그대로 따랐다면 어땠을지 과거 데이터로 계산한다."""
    if market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    tp_kw = _tp_params(tp_mode, atr_tp_mult, atr_sl_mult)   # 시세 조회 전에 검증

    fetched = get_candles(market, symbol, interval, max(200, min(limit, 1000)), exchange=exchange)
    rows = fetched["candles"]
    if fetched["source"] == "demo":
        raise HTTPException(422, "실시간 시세를 가져오지 못해 백테스트를 할 수 없습니다. "
                                 "데모 데이터로 계산한 성적은 아무 의미가 없습니다.")

    o, h, l, c, v = _ohlcv_arrays(rows)
    times = [r["t"] for r in rows]
    result = run_backtest(
        o, h, l, c, v, times,
        min_score=max(1, min(min_score, 22)), max_bars=max(4, min(max_bars, 240)),
        fee_pct=max(0.0, min(fee_pct, 2.0)),
        vol_len=vol_len, fib_len=fib_len, adx_thr=adx_thr,
        **tp_kw,
    )
    if "error" in result:
        raise HTTPException(422, result["error"])
    result.update(market=market, symbol=symbol, interval=interval, tp_basis=tp_mode,
                  source=fetched["source"], currency=fetched.get("currency", "USD"),
                  candles=len(rows))
    return _clean(result)


# ── 계정 ──────────────────────────────────────────────────────────────────
import re as _re

_USERNAME_RE = _re.compile(r"^[a-z0-9_.-]{2,20}$")


def current_user(request: Request) -> dict | None:
    """세션 쿠키로 로그인한 사용자를 찾는다. 없으면 None."""
    return auth.resolve_session(request.cookies.get(auth.SESSION_COOKIE))


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def _set_session_cookie(response: Response, token: str):
    response.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=auth.secure_cookies(), path="/",
    )


@app.post("/api/auth/register")
def register(body: RegisterBody, response: Response):
    username = body.username.strip().lower()
    if not _USERNAME_RE.match(username):
        raise HTTPException(400, "아이디는 영문 소문자·숫자·-_. 으로 2~20자여야 합니다")
    if len(body.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    if len(body.password) > 200:
        raise HTTPException(400, "비밀번호가 너무 깁니다")
    uid = db.create_user(username, auth.hash_password(body.password))
    if uid is None:
        raise HTTPException(409, "이미 사용 중인 아이디입니다")
    _set_session_cookie(response, auth.issue_session(uid))
    return {"id": uid, "username": username}


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    username = body.username.strip().lower()
    wait = auth.login_blocked(username)
    if wait:
        raise HTTPException(429, f"로그인 시도가 너무 많습니다. {wait}초 후에 다시 해보세요")
    user = db.get_user_by_name(username)
    # 아이디가 없어도 비밀번호가 틀려도 같은 문구를 준다 — 어느 아이디가
    # 존재하는지 답에서 새어 나가면 안 된다.
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        auth.record_login_failure(username)
        raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않습니다")
    auth.clear_login_failures(username)
    _set_session_cookie(response, auth.issue_session(user["id"]))
    return {"id": user["id"], "username": user["username"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.revoke_session(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": user}


# ── 사용자 정의 패턴 ──────────────────────────────────────────────────────
@app.get("/api/patterns/custom")
def list_custom_patterns(request: Request):
    user = current_user(request)
    return _clean(db.list_custom_patterns(user["id"]) if user else [])


@app.post("/api/patterns/custom/rule")
def create_rule_pattern(body: RulePatternCreate, request: Request):
    user = require_user(request)
    definition = {"candles": [c.model_dump() for c in body.candles]}
    errors = validate_rule_definition(definition)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    pid = db.create_custom_pattern(user["id"], body.name, "rule", body.direction, definition)
    return {"id": pid}


@app.post("/api/patterns/custom/shape")
def create_shape_pattern(body: ShapePatternCreate, request: Request):
    user = require_user(request)
    if body.market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    fetched = get_candles(body.market, body.symbol, body.interval, body.limit)
    rows = fetched["candles"]
    close = np.array([r["c"] for r in rows], dtype=float)
    if not (0 <= body.start_idx < body.end_idx < len(close)):
        raise HTTPException(400, "start_idx/end_idx가 캔들 범위를 벗어났습니다")
    try:
        definition = make_shape_template(close, body.start_idx, body.end_idx)
    except ValueError as e:
        raise HTTPException(400, str(e))
    pid = db.create_custom_pattern(user["id"], body.name, "shape", body.direction, definition)
    return {"id": pid}


@app.delete("/api/patterns/custom/{pattern_id}")
def delete_custom_pattern(pattern_id: int, request: Request):
    user = require_user(request)
    ok = db.delete_custom_pattern(user["id"], pattern_id)
    if not ok:
        raise HTTPException(404, "패턴을 찾을 수 없습니다")
    return {"deleted": True}


# ── 관심종목 ──────────────────────────────────────────────────────────────
@app.get("/api/watchlist")
def get_watchlist(request: Request):
    user = current_user(request)
    return _clean(db.list_watchlist(user["id"]) if user else [])


@app.post("/api/watchlist")
def post_watchlist(item: WatchlistItem, request: Request):
    user = require_user(request)
    db.add_watchlist(user["id"], item.market, item.symbol)
    return {"ok": True}


@app.delete("/api/watchlist")
def delete_watchlist(market: str, symbol: str, request: Request):
    user = require_user(request)
    db.remove_watchlist(user["id"], market, symbol)
    return {"ok": True}


# ── 매매 기록 (포트폴리오) ────────────────────────────────────────────────
# 화면은 아직 없지만 저장소와 API를 먼저 갖춰 둔다. 증권사·거래소 잔고를
# 자동으로 읽어올 권한은 개인에게 열려 있지 않으므로 직접 입력이 기본이다.

@app.get("/api/portfolio")
def get_portfolio(request: Request):
    user = require_user(request)
    trades = db.list_trades(user["id"])
    return _clean({"trades": trades, "positions": _positions(trades)})


@app.post("/api/portfolio/trades")
def post_trade(body: TradeCreate, request: Request):
    user = require_user(request)
    if body.market not in ("crypto", "us", "kr"):
        raise HTTPException(400, "market은 crypto|us|kr 중 하나여야 합니다")
    if body.side not in ("buy", "sell"):
        raise HTTPException(400, "side는 buy|sell 중 하나여야 합니다")
    if not (body.qty > 0 and math.isfinite(body.qty)):
        raise HTTPException(400, "수량은 0보다 커야 합니다")
    if not (body.price > 0 and math.isfinite(body.price)):
        raise HTTPException(400, "가격은 0보다 커야 합니다")
    tid = db.add_trade(user["id"], body.market, body.symbol.strip(),
                       body.side, body.qty, body.price, body.note.strip()[:200])
    return {"id": tid}


@app.delete("/api/portfolio/trades/{trade_id}")
def delete_trade(trade_id: int, request: Request):
    user = require_user(request)
    if not db.delete_trade(user["id"], trade_id):
        raise HTTPException(404, "기록을 찾을 수 없습니다")
    return {"deleted": True}


def _positions(trades: list[dict]) -> list[dict]:
    """매매 기록을 보유 현황으로 접는다.
    평균단가는 매수의 가중평균. 매도는 수량만 줄이고 평균단가는 유지한다
    (실현손익 계산은 단순화를 위해 아직 하지 않는다 — 화면에 그렇게 적을 것)."""
    pos: dict[tuple, dict] = {}
    for t in trades:
        key = (t["market"], t["symbol"])
        p = pos.setdefault(key, {"market": t["market"], "symbol": t["symbol"],
                                 "qty": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            p["qty"] += t["qty"]
            p["cost"] += t["qty"] * t["price"]
        else:
            if p["qty"] > 0:
                # 평균단가 기준으로 원가를 줄인다
                avg = p["cost"] / p["qty"]
                sell_qty = min(t["qty"], p["qty"])
                p["qty"] -= sell_qty
                p["cost"] -= sell_qty * avg
            # 보유량보다 많이 파는 기록은 잘못 입력한 것 — 0에서 멈춘다
    out = []
    for p in pos.values():
        if p["qty"] <= 1e-12:
            continue
        out.append({"market": p["market"], "symbol": p["symbol"],
                    "qty": round(p["qty"], 8),
                    "avg_price": round(p["cost"] / p["qty"], 8)})
    return out


# ── 프론트엔드 정적 파일 서빙 ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
