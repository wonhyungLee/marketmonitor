# Portfolio Engine (투자자산모음.csv 기반) 동작 원리

이 문서는 현재 프로젝트에 추가된 “추천 자산 배분(포트폴리오) 엔진”이 어떤 규칙으로 동작하는지, 그리고 매일 데이터를 업데이트했을 때 자동으로 추천을 만들 수 있는지 정리합니다.

## 1) 이 엔진은 무엇인가?

- “학습/예측 모델”이 아니라, **룰 기반(Deterministic)** 추천기입니다.
- 입력 데이터만 갱신되면, 같은 규칙으로 매일 **재현 가능한(reproducible)** 추천 결과를 다시 생성할 수 있습니다.

## 2) 입력 데이터

### (A) 자산 가격/지표: `지표데이터/투자자산모음.csv`

- 날짜 컬럼: `time`
- 자산 가격 컬럼(예시, 파일 헤더 기반 자동 탐지):
  - `close` (NASDAQ)
  - `XAUUSD...` (Gold)
  - `BTCUSD...` (Bitcoin)
  - `USDKRW...` (USD/KRW)
  - `COPPER...`, `REMX...`, `ALUMINUM...`, `URANIUM...`
- 금리 컬럼(있으면 반영):
  - `US10Y...` (미국 10년물 금리, %)
  - `US02Y...` 또는 `US2Y...` (미국 2년물 금리, %)

### (B) 매크로 상태: `data/market_states_daily.csv`

- `scripts/backtest_daily_states.py`가 생성합니다.
- 상태 값(예): `WARMUP`, `NORMAL`, `DEFCON2`, `DEFCON1`
- 포트폴리오 엔진은 이 값을 “옵션으로” 반영할 수 있습니다(아래 “매크로 가중치” 참고).

## 3) 채권(금리) 반영 방식: `US10Y/US02Y -> UST10Y/UST2Y`

`투자자산모음.csv`에는 채권 “가격”이 아니라 “금리(%)”가 들어있기 때문에, 엔진에서는 **금리 시계열을 채권 총수익 인덱스(가격 지수)로 근사 변환**합니다.

- 생성되는 가상 자산 ID:
  - `UST10Y` (10Y 금리 기반 채권 인덱스)
  - `UST2Y` (2Y 금리 기반 채권 인덱스)

근사식(일 단위):

- `dY = (Y_t - Y_{t-1}) / 100`  (금리 변화, 소수 단위)
- `carry_t = (Y_{t-1} / 100) / 252`  (옵션; 연이자를 일 단위로 나눈 캐리)
- `r_t ~= carry_t - duration * dY`
- `index_t = index_{t-1} * (1 + r_t)` (초기값 1.0)

관련 설정(기본값은 `.env.example` 참고):
- `PORTFOLIO_BOND10Y_DURATION` (기본 8.5)
- `PORTFOLIO_BOND2Y_DURATION` (기본 1.9)
- `PORTFOLIO_BOND_ADD_CARRY` (기본 true)

주의:
- 이 채권 인덱스는 “정확한 ETF/선물” 가격이 아니라 **duration 근사 모델**입니다(컨벡서티/롤다운/스프레드 등은 미포함).

## 4) 자산별 신호(Trend + Vol Target)

각 자산에 대해 매일 아래를 계산합니다.

### (A) 추세(Trend) 필터: MA200

- `price_t >= MA200_t` 이면 **투자 가능(ON)**
- 아니면 **비중 0(OFF)**

### (B) 변동성 타겟팅(Vol Target)

- 최근 `VOL_WINDOW_DAYS`(기본 20) 일간 수익률의 표준편차를 이용해 연율 변동성을 계산:
  - `vol_ann = std(returns_20d) * sqrt(252)`
- 자산별 원시 비중:
  - `weight_raw = TARGET_VOL_ANN / vol_ann`
  - `weight_raw`는 `PORTFOLIO_LEVERAGE_CAP`(기본 2.0)으로 상한 적용
- Trend가 OFF면 `weight_raw = 0`

## 5) 매크로 상태(DEFCON) 반영(옵션)

포트폴리오 엔진은 매크로 상태를 “방향결정”으로 강제하지 않고, **리스크 자산에만 배수(multipliers)로 반영**할 수 있도록 되어 있습니다.

- `PORTFOLIO_RISK_ASSETS`에 포함된 자산만 DEFCON에서 비중을 줄일 수 있음(예: NASDAQ/BTC/원자재 등)
- 상태별 배수:
  - `PORTFOLIO_MACRO_MULTIPLIER_NORMAL`
  - `PORTFOLIO_MACRO_MULTIPLIER_DEFCON2`
  - `PORTFOLIO_MACRO_MULTIPLIER_DEFCON1`

## 6) 전체 포트폴리오 스케일링(레버리지 캡)

자산별 `weight_raw`를 모두 합쳐서 총 익스포저(=gross exposure)를 계산한 뒤, 레버리지 상한을 넘으면 전체를 축소합니다.

- `gross = sum(weight_raw_i)`
- `gross > LEVERAGE_CAP`이면 `scale = LEVERAGE_CAP / gross`
- 최종 비중: `weight_i = weight_raw_i * scale`
- 현금 비중: `cash_weight = 1 - gross_exposure`
  - `gross_exposure > 1`인 경우 `cash_weight`가 음수가 될 수 있는데, 이는 **차입(레버리지)** 의미입니다.

## 7) 출력 데이터

### `data/portfolio_daily.csv`

`scripts/build_portfolio_daily.py`가 생성합니다.

- 주요 컬럼:
  - `date`
  - `state`
  - `gross_exposure`, `cash_weight`
  - `w_<ASSET>` (예: `w_NASDAQ`, `w_GOLD`, `w_UST10Y`, `w_UST2Y` ...)
  - `portfolio_ret`, `portfolio_curve`, `portfolio_dd` (백테스트용; 1일 랙, 비용 0 가정)

## 8) 매일 데이터 업로드 시 “자동 추천” 가능 여부

가능합니다. 다만 “자동”은 운영 방식에 따라 아래 두 가지 중 하나로 구현합니다.

### 운영 방식 A) CSV 기반(사이트/히스토리 갱신)

1) 매일 `지표데이터/*.csv` (특히 `투자자산모음.csv`, NASDAQ 지표 파일들) 업데이트/추가
2) 상태 산출:
   - `python scripts/backtest_daily_states.py`
3) 포트폴리오 산출:
   - `python scripts/build_portfolio_daily.py`
4) 사이트에서 확인:
   - `python -m http.server 8000`
   - `http://localhost:8000/site/`

### 운영 방식 B) DB(Webhook) 기반(라이브/알림)

1) TradingView webhook 등으로 지표를 DB에 적재(기존 WarRoom ingest)
2) 그날의 `투자자산모음.csv` 업데이트
3) 매일 실행:
   - `python scripts/run_daily.py`
4) 결과:
   - 오늘의 state + 오늘의 포트폴리오 추천이 함께 계산되어 Discord 리포트에도 포함될 수 있습니다(설정 시).

## 9) 관련 파일

- 포트폴리오 엔진(라이브): `app/portfolio.py`
- 포트폴리오 CSV 생성(배치): `scripts/build_portfolio_daily.py`
- 상태 산출(배치): `scripts/backtest_daily_states.py`
- 라이브 실행 엔트리: `scripts/run_daily.py`
- 사이트 시각화: `site/index.html`, `site/app.js`, `site/styles.css`

