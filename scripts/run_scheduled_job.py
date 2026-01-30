import logging

from app.settings import get_settings
from scripts.run_daily import run_daily_job


def main() -> None:
    settings = get_settings()
    window_days = int(getattr(settings, "auto_refresh_window_days", 7) or 7)
    min_interval_sec = int(getattr(settings, "auto_refresh_daily_min_interval_sec", 0) or 0)
    run_daily_job(
        window_days=window_days,
        min_interval_sec=min_interval_sec if min_interval_sec > 0 else None,
        force=True,
        trigger="timer",
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    main()
