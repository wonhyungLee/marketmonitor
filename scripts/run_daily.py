import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from zoneinfo import ZoneInfo

from app import db
from app.exporter import (
    export_asset_universe_csv,
    export_daily_states_csv,
    export_nasdaq_1d_csv,
    export_portfolio_recent_csv,
    export_webhook_records_csv,
)
from app.engine import evaluate, EngineResult
from app.notifier import notify
from app.settings import get_settings

logger = logging.getLogger("warroom.job")


def _load_observations_frame(conn, tz: ZoneInfo) -> Optional[pd.DataFrame]:
    rows = db.fetch_observations(conn)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["series_id", "time_utc_ms", "interval", "value"])
    df["timestamp"] = pd.to_datetime(df["time_utc_ms"], unit="ms", utc=True)
    df["as_of_date"] = df["timestamp"].dt.tz_convert(tz).dt.date
    return df


def run_daily_job(window_days: int = 30) -> Optional[EngineResult]:
    """
    모듈화된 데일리 통합 작업 파이프라인.
    """
    settings = get_settings()
    logger.info("Starting consolidated daily job (v2.1)...")
    
    result = None
    try:
        with db.db_session(settings.db_path) as conn:
            tz = ZoneInfo(settings.as_of_tz)
            obs_df = _load_observations_frame(conn, tz)
            if obs_df is None or obs_df.empty:
                logger.warning("No observations found; skipping daily job.")
                return None

            end_date = obs_df["as_of_date"].max()
            start_date = end_date - timedelta(days=max(1, int(window_days)) - 1)

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
                export_asset_universe_csv(conn)
                export_portfolio_recent_csv(conn, days=window_days, end_date=end_date)
            except Exception as e:
                logger.error(f"Export failed but continuing to notify: {e}")
            
            # 3. 디스코드 알림 발송
            if result:
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

    run_daily_job(window_days=args.window_days)
