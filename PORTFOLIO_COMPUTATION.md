# Portfolio Recommendation Computation (현재 구현)

이 문서는 “현재 포트폴리오가 어떤 연산으로 추천되는지”를 수식/절차 중심으로 정리합니다.

관련 코드:
- 배치 생성(사이트용): `scripts/build_portfolio_daily.py`
- 라이브 추천(엔진): `app/portfolio.py`

---

## 0) 입력 데이터

1) 자산 데이터: `지표데이터/투자자산모음.csv`
- `time` (YYYY-MM-DD)
- 가격 컬럼들(예):
  - NASDAQ: `close`
  - Gold: `XAUUSD...`
  - BTC: `BTCUSD...`
  - USDKRW: `USDKRW...`
  - 기타: `COPPER...`, `REMX...`, `ALUMINUM...`, `URANIUM...`
- 금리 컬럼들(있으면 채권으로 변환):
  - `US10Y...` (10Y yield, %)
  - `US02Y...` 또는 `US2Y...` (2Y yield, %)

2) (옵션) 매크로 상태: `data/market_states_daily.csv`
- `date/as_of_date` 기준으로 같은 날짜에 state(`NORMAL/DEFCON2/DEFCON1`)를 붙여 사용합니다.

---

## 1) (채권) 금리(%) → 채권 총수익 인덱스(가상 가격) 변환

원본 파일에는 채권 “가격” 대신 “금리(%)”가 있으므로, 엔진은 이를 `UST10Y`, `UST2Y`라는 **synthetic bond index**로 바꿔서 “가격 자산”처럼 취급합니다.

기본 근사식(일 단위):

- 금리 변화(소수 단위):  
  `dY_t = (Y_t - Y_{t-1}) / 100`

- 캐리(옵션, on by default):  
  `carry_t = (Y_{t-1} / 100) / 252`

- duration 기반 채권 수익률 근사:  
  `r_t ≈ carry_t - duration * dY_t`

- 인덱스 업데이트(초기값 1.0):  
  `Index_t = Index_{t-1} * (1 + r_t)`

설정값:
- 10Y duration: `PORTFOLIO_BOND10Y_DURATION` (기본 8.5)
- 2Y duration: `PORTFOLIO_BOND2Y_DURATION` (기본 1.9)
- 캐리 포함 여부: `PORTFOLIO_BOND_ADD_CARRY` (기본 true)

---

## 2) 자산별 Trend(추세) 필터: MA200

각 자산(가격/채권 인덱스)에 대해:

- 이동평균: `MA200_t = mean(P_{t-199} ... P_t)`
- 추세 ON/OFF:
  - `TrendUp_t = (P_t >= MA200_t)`
  - TrendUp이 아니면 해당 자산 비중은 0으로 고정합니다.

---

## 3) 자산별 변동성(20일) 계산 → 변동성 타겟팅 비중 산출

각 자산에 대해:

- 일간 수익률: `ret_t = P_t / P_{t-1} - 1`
- 연율 변동성: `vol_ann_t = std(ret_{t-19}...ret_t) * sqrt(252)`
- 원시 비중(Vol Target):
  - `w_raw_t = TARGET_VOL_ANN / vol_ann_t`
  - 상한: `w_raw_t = min(w_raw_t, LEVERAGE_CAP)`
  - Trend가 OFF면 `w_raw_t = 0`

기본 설정값:
- `PORTFOLIO_VOL_WINDOW_DAYS` (기본 20)
- `PORTFOLIO_TARGET_VOL_ANN` (기본 0.35)
- `PORTFOLIO_LEVERAGE_CAP` (기본 2.0)

---

## 4) (옵션) 매크로 상태(DEFCON) 반영: “리스크 자산만” 배수 적용

state를 같은 날짜로 조인한 뒤, `PORTFOLIO_RISK_ASSETS`에 포함된 자산만 상태별 배수를 곱합니다.

- `w_raw_t(asset) = w_raw_t(asset) * M(state_t)`  (단, asset이 risk_assets에 포함될 때만)

기본값(현재 기본은 모두 1.0이라 실제 영향 없음):
- `PORTFOLIO_MACRO_MULTIPLIER_NORMAL`
- `PORTFOLIO_MACRO_MULTIPLIER_DEFCON2`
- `PORTFOLIO_MACRO_MULTIPLIER_DEFCON1`

---

## 5) 전체 레버리지 캡(총합) 스케일링

자산별로 산출된 `w_raw`는 자산별 상한만 적용된 값이라, 합계가 cap을 넘을 수 있습니다. 그래서 마지막에 전체를 스케일링합니다.

- `gross_t = sum_i w_raw_t(i)`
- if `gross_t > LEVERAGE_CAP`:
  - `scale_t = LEVERAGE_CAP / gross_t`
  - `w_t(i) = w_raw_t(i) * scale_t`
- else:
  - `w_t(i) = w_raw_t(i)`

현금(또는 레버리지 차입) 비중:
- `cash_weight_t = 1 - gross_exposure_t`
- `gross_exposure_t > 1`이면 `cash_weight_t < 0`가 될 수 있는데, 이는 **레버리지(차입)** 의미입니다.

---

## 6) 출력

배치 스크립트 출력: `data/portfolio_daily.csv`
- `w_<ASSET>` 컬럼으로 날짜별 추천 비중 저장
  - 예: `w_NASDAQ`, `w_GOLD`, `w_UST10Y`, `w_UST2Y`, ...
- 요약:
  - `gross_exposure`, `cash_weight`

참고(백테스트용):
- `portfolio_ret/curve/dd`는 “1일 랙(전일 비중으로 다음날 수익)”으로 계산되어 있습니다.
- 거래비용/슬리피지/이자비용은 포함하지 않습니다.

