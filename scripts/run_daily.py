import logging
from pathlib import Path
from typing import Optional

from app import db
from app.exporter import export_daily_states_csv, export_nasdaq_1d_csv, export_asset_universe_csv
from app.engine import evaluate, EngineResult
from app.notifier import notify
from app.settings import get_settings

logger = logging.getLogger("warroom.job")


def run_daily_job() -> Optional[EngineResult]:
    """
    모듈화된 데일리 통합 작업 파이프라인.
    이제 서버(FastAPI) 내부에서 직접 호출 가능합니다.
    """
    settings = get_settings()
    logger.info("Starting consolidated daily job (v2.1)...")
    
    try:
        with db.db_session(settings.db_path) as conn:
            # 1. 시장 국면 평가 및 DB 저장
            result = evaluate(conn)
            
            # 2. 웹 UI를 위한 CSV 데이터 내보내기
            export_daily_states_csv(conn)
            export_nasdaq_1d_csv(conn)
            export_asset_universe_csv(conn)
            
            # 3. 디스코드 알림 발송
            notify(result)
            
            logger.info(f"Daily job completed successfully for {result.as_of_date}")
            return result
            
    except Exception as e:
        logger.exception(f"Daily job failed: {str(e)}")
        return None


if __name__ == "__main__":
    # CLI 환경에서의 직접 실행 지원
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    run_daily_job()