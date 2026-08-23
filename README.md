# 🏦 개인 차트 분석 시스템 (coin_chart)

코인 / 미국주식 / 한국주식을 한 화면에서 분석하는 개인용 웹 차트 툴입니다.
사용자가 올린 두 개의 TradingView Pine Script —

- `🏦 기관급 BTC/ETH 신호 시스템` (EMA·VWAP·볼린저·ADX·MACD·OBV다이버전스·피보나치 기반 매수/매도 점수 대시보드)
- `Range Filter + Fibonacci BB`

— 의 로직을 그대로 Python으로 이식하고, 여기에 **캔들/차트 패턴 자동 탐지**와
**사용자 정의 패턴(나만의 패턴) 기능**을 얹었습니다.

## 왜 이런 구조인가

- 외부 차트 라이브러리(CDN)를 쓰지 않고 프론트엔드를 **순수 HTML/CSS/JS + Canvas**로 직접 그렸습니다.
  (개발 환경 네트워크 정책상 CDN 접근이 막혀 있어 자체 구현이 더 안전하고, 배포 시에도 외부 의존성이 없어 가볍습니다.)
- 백엔드는 FastAPI + numpy 로, Pine Script의 `ta.ema/ta.rma/ta.vwap/ta.macd/ta.stoch/ta.bb` 등을 1:1로 재현했습니다.

## 기능

- **코인 거래소 선택**: 바이낸스(USDT) · 업비트(KRW) · 빗썸(KRW) · 코인베이스(USD) 중에서
  시세를 가져올 곳을 직접 고를 수 있습니다. 국내 거래소는 원화 기준이라 해외 거래소와
  가격이 다르며(김치프리미엄), 화면에도 출처와 통화를 함께 표시합니다. 선택한 거래소가
  해당 간격이나 종목을 지원하지 않으면 다른 거래소로 넘어가되 어디서 가져왔는지 알려줍니다.
  (빗썸은 15분·4시간·주봉, 코인베이스는 30분·4시간·주봉을 지원하지 않습니다.)
- **마켓 3종 통합**: 코인(바이낸스/업비트/빗썸/코인베이스/야후), 미국주식(**토스증권 공식 Open API** → 야후 폴백), 한국주식(**토스증권 공식 Open API** → 토스 비공식 API → 야후 폴백)
  - 이 개발 샌드박스는 조직 보안 정책으로 바이낸스/야후/토스 등 외부 API 아웃바운드가 전부 막혀 있어,
    라이브 연동 코드는 정확히 작성했지만 **지금 이 환경에서는 항상 데모(합성) 데이터로 폴백**합니다.
    실제 PC/서버에 배포하면 자동으로 라이브 데이터를 받아옵니다. (`backend/data_providers.py` 참고)
  - **토스증권 공식 Open API** (2026-08 정식 출시, `openapi.tossinvest.com`, OAuth2 Client Credentials 방식)를
    쓰려면 배포 환경에 `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` 두 환경변수를 설정하세요
    (`.env.example` 참고 — **절대 실제 값을 코드나 git에 커밋하지 마세요**, `.env`는 이미 `.gitignore`에 포함되어 있습니다).
    이 API는 `interval=1m` 또는 `1d`만 지원하며, 한국/미국 주식을 모두 커버합니다. 두 환경변수가 없으면
    자동으로 다음 폴백(비공식 API → 야후 → 데모)으로 넘어갑니다.
  - 토스증권 비공식 API(`wts-info-api.tossinvest.com`)는 공식 문서가 없는 리버스엔지니어링 API라
    엔드포인트가 바뀌면 깨질 수 있습니다 — 실패 시 자동으로 야후로 폴백합니다.
- **기관급 매수/매도 점수 대시보드**: 원본 Pine Script의 22점 만점 스코어링, TP/SL 자동계산, 강한매수/매수/매도/강한매도 라벨을 그대로 재현
- **Range Filter + Fibonacci BB**: 토글로 켜고 끌 수 있는 보조 오버레이
- **캔들스틱 패턴 자동 탐지**: 도지·해머·행잉맨·슈팅스타·장악형·관통형·흑운형·모닝스타·이브닝스타·적삼병·흑삼병
- **차트 패턴 자동 탐지**: 더블탑/바텀, 트리플탑/바텀, 헤드앤숄더/역헤드앤숄더, 상승/하락/대칭 삼각형, 상승/하락 쐐기형
  - 방식: 스윙 고점/저점(피벗) 추출 → 교과서적 정의를 기하학적 허용오차로 매칭 (실제 상용 차트패턴 툴들이 쓰는 방식과 동일)
  - ⚠️ 라벨링된 이미지로 학습한 딥러닝 분류기가 아닙니다. 그 방식은 대량의 라벨 데이터와 GPU 학습 인프라가 필요해
    개인 프로젝트 범위를 벗어나며, 규칙/기하 기반 방식이 실무에서도 널리 쓰이고 훨씬 해석 가능합니다.
- **사용자 정의 패턴 (나만의 패턴)**:
  1. **규칙 빌더** — 캔들 여러 개(상대 오프셋)에 대해 시가/종가/고가/저가/거래량/몸통/꼬리 조건을 조합해
     나만의 캔들 패턴을 안전하게(임의 코드 실행 없이) 정의
  2. **모양 그리기** — 차트에서 원하는 구간을 드래그로 선택 → 정규화된 가격 곡선을 템플릿으로 저장 →
     전체 히스토리를 슬라이딩 윈도우로 스캔해 상관계수 기반으로 유사한 구간을 자동으로 찾아줌

## 실행 방법

```bash
cd backend
pip install -r requirements.txt
cd ..

# (선택) 토스증권 공식 Open API로 실시간 시세를 받고 싶다면:
cp .env.example .env
# .env 파일을 열어 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 값을 채워넣기 (git에는 올라가지 않음)

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 접속.

## 웹에 배포하기 (링크로 어디서나 접속)

이 저장소에는 [Render](https://render.com) 배포 청사진(`render.yaml`)이 포함되어 있어서,
계정만 연결하면 `https://<서비스이름>.onrender.com` 형태의 고정 주소가 발급됩니다.

1. https://render.com 에 GitHub 계정으로 가입/로그인
2. 대시보드에서 **New → Blueprint** 선택
3. `Penguin8282/coin_chart` 저장소를 선택하면 `render.yaml`을 자동으로 읽어옵니다
4. 환경변수 입력창이 뜨면 아래를 채우고 **Apply**
   - `APP_USER` / `APP_PASSWORD` — **꼭 설정하세요.** 배포된 주소는 링크를 아는 누구나
     접속할 수 있고, 설정하지 않으면 아무나 커스텀 패턴·관심종목을 수정할 수 있습니다.
     설정하면 브라우저 기본 로그인창으로 사이트 전체가 보호됩니다.
   - `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` — (선택) 한국·미국 주식 실시간 시세용.
     비워두면 야후 파이낸스로 자동 폴백됩니다.
5. 첫 배포는 몇 분 걸립니다. 완료되면 상단에 나오는 주소가 바로 접속 링크입니다.

참고사항:
- 배포 플랫폼용 헬스체크는 `/healthz` 입니다. 이 경로만 접근 보호에서 제외되어
  있습니다 — 헬스체크는 로그인 정보를 보내지 않기 때문에, 보호를 걸면 401만
  돌려받고 서비스가 죽은 것으로 판정해 배포가 실패합니다. 다른 곳에 배포하실
  때도 헬스체크 경로는 `/healthz`로 지정하세요.
- **무료 플랜은 15분간 접속이 없으면 잠들었다가**, 다음 접속 시 다시 깨어나느라
  30초 정도 걸립니다. 항상 즉시 뜨게 하려면 유료 플랜으로 올리면 됩니다.
- **무료 플랜에서는 저장한 커스텀 패턴/관심종목이 재시작 시 초기화됩니다.**
  무료 플랜은 영구 디스크를 지원하지 않기 때문이고, 위에서 말한 대로 잠들었다 깨는
  일이 잦아 재시작도 자주 일어납니다. 패턴을 계속 보관하려면 유료 인스턴스로 올린 뒤
  `render.yaml`의 `plan:`을 `starter`로 바꾸고 파일 아래쪽 `disk:` / `DATA_DIR`
  블록의 주석을 해제하면 됩니다.
- Render 외 다른 곳에 올리셔도 됩니다. 필요한 건 `pip install -r backend/requirements.txt`
  후 `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` 를 실행하는 것뿐입니다.

## 폴더 구조

```
backend/
  main.py                 FastAPI 앱, 라우팅
  data_providers.py        바이낸스/야후/토스(공식+비공식) 연동 + 데모 데이터 생성 + 폴백 체인
  toss_openapi.py           토스증권 공식 Open API (OAuth2 client_credentials, /api/v1/candles)
  indicators.py             EMA/RMA/SMA/RSI/MACD/Stoch/ADX/VWAP/OBV/BB 등 지표 (numpy)
  scoring.py                기관급 매수/매도 점수 엔진 (institutional_btc_eth_signals.pine 이식)
  range_filter_fbb.py       Range Filter + Fibonacci BB (range_filter_fibonacci_bb.pine 이식)
  db.py                     SQLite (커스텀 패턴 / 관심종목 저장)
  patterns/
    pivots.py               스윙 고점/저점(피벗) 탐지
    candlestick.py          캔들스틱 패턴 탐지
    chart_patterns.py       차트 패턴(헤드앤숄더 등) 탐지
    custom_engine.py        사용자 정의 패턴 엔진 (규칙 + 모양템플릿)
frontend/
  index.html / styles.css / app.js   순수 JS Canvas 차트 (외부 라이브러리 없음)
pinescript/                원본 Pine Script 2개 (참고용 원본 보존)
data/                       SQLite DB 파일 위치 (git 제외)
```

## API 요약

- `GET /api/markets` — 마켓별 기본 심볼 목록
- `GET /api/candles?market=&symbol=&interval=&limit=` — OHLCV 캔들
- `GET /api/analysis?market=&symbol=&interval=&limit=` — 지표/점수/패턴 탐지 전체 결과
- `GET/POST/DELETE /api/patterns/custom` — 사용자 정의 패턴 CRUD (`/rule`, `/shape` 하위 경로)
- `GET/POST/DELETE /api/watchlist` — 관심종목

## 알려진 한계 (정직하게 밝힙니다)

- 차트 패턴 탐지는 딥러닝이 아니라 **피벗 기반 기하 규칙**입니다. 노이즈가 많은 구간(특히 데모 합성 데이터처럼
  자잘한 등락이 잦은 경우)에서는 후보가 다소 많이 잡힐 수 있습니다 — 신뢰도(confidence) 점수로 우선순위를 매겨 표시합니다.
- 토스증권 API는 비공식이라 향후 응답 스펙이 바뀌면 조정이 필요할 수 있습니다.
- 실시간(웹소켓) 시세는 지원하지 않고, 새로고침 시점 기준 스냅샷을 가져옵니다.
