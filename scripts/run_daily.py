import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from zoneinfo import ZoneInfo

from app import db
from app.exporter import (
    backup_daily_states_csv,
    export_cycles_csv,
    export_fear_euphoria_csv,
    export_fear_euphoria_calendar,
    export_asset_universe_csv,
    export_daily_states_csv,
    export_nasdaq_1d_csv,
    export_portfolio_recent_csv,
    export_webhook_records_csv,
)
from app.engine import evaluate, EngineResult
from app.job_control import job_lock, read_last_run_ts, write_last_run_ts
from app.notifier import notify
from app.settings import get_settings
from app.fear_euphoria import load_fear_euphoria_snapshot

logger = logging.getLogger("warroom.job")


def _load_observations_frame(conn, tz: ZoneInfo) -> Optional[pd.DataFrame]:
    rows = db.fetch_observations(conn)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "interval", "value", "received_at"])
    settings = get_settings()
    if bool(getattr(settings, "use_received_at_for_latest", False)):
        ts = pd.to_datetime(df["received_at"], utc=True, errors="coerce")
        # Fallback to time_utc_ms when received_at is missing/invalid.
        fallback = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
        df["timestamp"] = ts.fillna(fallback)
    else:
        df["timestamp"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
    df["as_of_date"] = df["timestamp"].dt.tz_convert(tz).dt.date
    return df


def run_daily_job(
    window_days: int = 30,
    min_interval_sec: Optional[int] = None,
    force: bool = False,
    trigger: str = "manual",
    job_name: str = "daily",
) -> Optional[EngineResult]:
    """
    모듈화된 데일리 통합 작업 파이프라인.
    """
    settings = get_settings()
    logger.info(
        "Starting consolidated daily job (v2.1)... trigger=%s window_days=%s",
        trigger,
        window_days,
    )
    
    result = None
    did_run = False
    try:
        with job_lock(job_name) as lock:
            if lock is None:
                logger.info("Daily job already running. Skipping. trigger=%s", trigger)
                return None

            if not force and min_interval_sec is not None:
                last_ts = read_last_run_ts(job_name)
                if last_ts is not None:
                    elapsed = time.time() - float(last_ts)
                    if elapsed < float(min_interval_sec):
                        logger.info(
                            "Daily job throttled. trigger=%s elapsed=%.1fs min_interval=%ss",
                            trigger,
                            elapsed,
                            min_interval_sec,
                        )
                        return None

            did_run = True
            with db.db_session(settings.db_path) as conn:
                tz = ZoneInfo(settings.as_of_tz)
                obs_df = _load_observations_frame(conn, tz)
                if obs_df is None or obs_df.empty:
                    logger.warning("No observations found; skipping daily job.")
                    return None
                end_date = obs_df["as_of_date"].max()
                start_date = end_date - timedelta(days=max(1, int(window_days)) - 1)

                # 0. 기존 daily_states CSV 백업 (안정 운영용)
                try:
                    backup_daily_states_csv()
                except Exception as e:
                    logger.warning(f"Failed to backup daily states CSV: {e}")

                # 1. 최근 N일 시장 국면 평가
                cur_date = start_date
                while cur_date <= end_date:
                    result = evaluate(conn, as_of_date=cur_date, data_frame=obs_df)
                    cur_date += timedelta(days=1)
                
                # 2. 데이터 내보내기 (실패해도 알림은 시도)
                try:
                    export_webhook_records_csv(conn)
                    export_daily_states_csv(conn)
                    export_nasdaq_1d_csv(conn)
                    export_cycles_csv(conn)
                    export_fear_euphoria_csv(conn)
                    export_fear_euphoria_calendar(conn)
                    export_asset_universe_csv(conn)
                    # Portfolio export depends on cycle / fear-euphoria snapshots.
                    export_portfolio_recent_csv(conn, days=window_days, end_date=end_date)
                except Exception as e:
                    logger.error(f"Export failed but continuing to notify: {e}")
                
                # 3. 디스코드 알림 발송
                if result:
                    # Attach Fear/Euphoria snapshot (exported just above) to the reasons for Discord/site.
                    try:
                        fe = load_fear_euphoria_snapshot(result.as_of_date)
                        if fe is not None:
                            result.reasons = dict(result.reasons or {})
                            result.reasons["fear_euphoria"] = {
                                "months_until_fear": fe.months_until_fear,
                                "months_until_euphoria": fe.months_until_euphoria,
                                "confidence": fe.confidence,
                                "fear_window_24m": fe.fear_window_24m,
                                "fear_window_36m": fe.fear_window_36m,
                                "euphoria_window_24m": fe.euphoria_window_24m,
                                "euphoria_window_36m": fe.euphoria_window_36m,
                                "fear_trigger": fe.fear_trigger,
                                "fear_level": fe.fear_level,
                                "euphoria_level": fe.euphoria_level,
                                "euphoria_trigger": fe.euphoria_trigger,
                            }
                    except Exception:
                        logger.exception("Failed to load fear/euphoria snapshot")

                    # NOTE: Discord 전송 실패는 전체 작업 실패로 취급하지 않는다.
                    try:
                        sent = notify(result)
                        if not sent:
                            logger.warning("Discord notification not sent (see notifier logs)")
                    except Exception:
                        logger.exception("Discord notification raised an unexpected error; continuing")
                    
                    logger.info(f"Daily job completed successfully for {result.as_of_date}")
                
                return result
            
    except Exception as e:
        logger.exception(f"Critical failure in daily job: {str(e)}")
        return None
    finally:
        if did_run:
            try:
                write_last_run_ts(job_name, time.time())
            except Exception:
                pass


if __name__ == "__main__":
    # CLI 환경에서의 직접 실행 지원
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30, help="Recompute range (days)")
    args = ap.parse_args()

    run_daily_job(window_days=args.window_days, force=True, trigger="cli")
