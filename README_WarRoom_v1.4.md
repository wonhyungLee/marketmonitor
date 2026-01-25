# 🏛️ WarRoom v2.0 — TradingView Webhook → Engine → Discord (Backfill 지원)

미국 경기/리스크 레짐을 **TradingView 웹훅으로 수집한 지표 데이터**로 “누적해서 사고”하고,
매일 1회 **Score/State(WARMUP/NORMAL/DEFCON2/DEFCON1)** 를 산출해 **Discord Webhook(Embed + 색상 + 이모지)** 로 알리는 경량 시스템입니다.

- 운영: Oracle Cloud Ubuntu (RAM ~1GB)
- 목표: 실시간 트레이딩 봇이 아니라 **일일 전략 리포트**
- 데이터: TradingView 웹훅(운영) + TradingView CSV Export(초기 Backfill)

---

## 1) 아키텍처

### 데이터 흐름
1) TradingView Alert → 서버 Webhook(JSON)
2) 서버가 payload 검증 + dedupe 후 SQLite에 누적 저장(Event Sourcing)
3) 매일 1회 스케줄러(cron)가 스냅샷 생성 → 엔진 평가 → Discord 알림 전송

```
TradingView (Sensors) -> FastAPI Ingest -> SQLite (Event Store)
                                   -> Daily Engine (cron) -> Discord Webhook
```

## 2) v2.0 MUST 정책

### 2.1 Stale 판단 기준 통일
- `realtime_start`로 stale 판단 금지
- stale은 아래 중 하나로 판단:
  - `available_at` (권장: 수집 시각/가용 시각)
  - 또는 `obs_date + expected_lag + buffer`
- stale이면 해당 지표는 **점수 0점(무시)** 처리하고 health에 기록

### 2.2 Streak(연속 조건) 분리
연속 조건 카운터는 반드시 분리:
- `streak_ge_2` : score >= 2.0 연속 (DEFCON2 진입)
- `streak_ge_3_5` : score >= 3.5 연속 (DEFCON1 진입)
- `streak_lt_2` : score < 2.0 연속 (DEFCON2 → NORMAL)
- `streak_le_3` : score <= 3.0 연속 (DEFCON1 → DEFCON2)

### 2.3 DEFCON1 Exit
- DEFCON1 → DEFCON2: score <= 3 이 10일 연속
- DEFCON2 → NORMAL: score < 2.0 가 5일 연속

---

## 3) 센서(지표) 주기와 최소 누적치

| series_id | 권장 주기 | 최소 누적(관측치) | 비고 |
|---|---:|---:|---|
| T10Y2Y | 1D | 60 (권장 90+) | 역전 구간 체크 + MA5 cross |
| BAMLH0A0HYM2 | 1D | 60 (pctl 사용 시 252) | MA20 slope / 분위수 |
| COPPER_GOLD_RATIO | 1D | 205 | MA200 + 5일 연속 조건 |
| WEI | 1W | 4 (권장 12~26) | 최근 4주 trend/slope |
| SAHMREALTIME | 1M | 1 | Hard Trigger (>=0.5) |
| UMCSENT | 1M | 1 (권장 3~6) | 보조 지표 (65 미만) |

### Backfill 권장
- 1D: 최소 300~400 관측치(특히 COPPER_GOLD_RATIO)
- 1W: 최소 26주
- 1M: 최소 24개월

---

## 4) Webhook 프로토콜 (TradingView → Server)

### Endpoint
- `POST /webhook/<WEBHOOK_TOKEN>`

### Payload 표준
```json
{
  "schema_version": 1,
  "source": "tradingview",
  "series_id": "COPPER_GOLD_RATIO",
  "time_utc": 1706227200000,
  "interval": "1D",
  "value": -5.24,
  "timenow": 1706230800000
}
```

- `time_utc`, `timenow`: Unix epoch milliseconds
- Dedupe Key (DB PK): `(series_id, time_utc_ms, interval)`
- 권장: 봉 확정 값만 전송
  - `barstate.isconfirmed`
  - `alert.freq_once_per_bar_close`

---

## 5) 데이터베이스 (SQLite) 스키마

### market_observations (관측치 원장)
- columns: `series_id TEXT`, `time_utc_ms INTEGER`, `interval TEXT`, `value REAL`, `received_at TEXT`, `payload_json TEXT`
- PK: `(series_id, time_utc_ms, interval)`

### daily_states (일일 판정)
- columns: `as_of_date TEXT`, `state TEXT`, `score REAL NULL`, `reasons_json TEXT`, `health_json TEXT`, `created_at TEXT`

---

## 6) 엔진 판정(요약)

### Score(예시 가중치)
- SAHMREALTIME >= 0.50: Hard Trigger → 즉시 DEFCON1
- T10Y2Y: Cross Up (un-inversion) → +2
- BAMLH0A0HYM2: 위험(4.0%+, slope>0 또는 분위수 상단) → +1.5
- WEI: 침체 flag(최근4주 기반) → +1
- COPPER_GOLD_RATIO: 5일 연속 MA200 하회 → +0.5
- UMCSENT < 65: +0.5

### 상태 전이(Hysteresis)
- NORMAL → DEFCON2: score>=2.0 3일 연속
- DEFCON2 → DEFCON1: score>=3.5 3일 연속
- DEFCON2 → NORMAL: score<2.0 5일 연속
- DEFCON1 → DEFCON2: score<=3.0 10일 연속

### WARMUP (데이터 부족 안내)
Backfill이 없거나 일부 센서 누적이 부족하면 WARMUP을 사용할 수 있습니다.
Backfill을 완료했더라도, 운영 초기에 특정 센서가 비어 있다면 다음을 권장합니다.
- 센서별 have/need를 계산해서 `health_json`에 기록
- 핵심 센서 준비도가 낮을 때는 `state=WARMUP, score=NULL`로 Discord에 안내

---

## 7) Discord 알림(Embed + 색상 + 이모지)

상태별 시안성 규칙:
- WARMUP: 🟦 (0x3498db)
- NORMAL: 🟢 (0x2ecc71)
- DEFCON2: 🟠 (0xe67e22)
- DEFCON1: 🔴 (0xe74c3c)

권장 Embed 필드:
- Score
- As of
- Triggers(이유)
- Health(stale/not_ready)

---

## 8) Ubuntu 설치/운영

### 패키지
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip sqlite3
```

### (권장) Swap (RAM 1GB 안전장치)
```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### venv + 의존성
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 환경변수
```bash
cp .env.example .env
```

`.env` 예시:
```env
HOST=0.0.0.0
PORT=8000
WEBHOOK_TOKEN=change-me-long-random
DB_PATH=./warroom.db
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TIMEZONE=Asia/Seoul
AS_OF_TZ=America/New_York
```

### DB 초기화
```bash
python scripts/init_db.py
```

### 개발 실행
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 운영(권장)
- FastAPI: systemd로 상시 구동
- Daily Engine: cron으로 매일 1회 실행

---

## 9) 프로젝트 구조(권장)

```
warroom/
  app/
    main.py        # webhook ingest
    db.py          # sqlite helpers
    models.py      # payload schema
    snapshot.py    # DB -> features -> data_snapshot
    engine.py      # v2.0 core
    notifier.py    # discord embed sender
    settings.py    # env
  scripts/
    init_db.py
    run_daily.py
  systemd/
    warroom.service
  requirements.txt
  .env.example
```

---

## 10) requirements.txt 예시

```txt
fastapi
uvicorn
pydantic
python-dotenv
requests
pandas
numpy
```

---

## License
Private / internal use
