from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = Field("0.0.0.0", env="HOST")
    port: int = Field(8000, env="PORT")
    webhook_token: str = Field(..., env="WEBHOOK_TOKEN")
    db_path: str = Field("./warroom.db", env="DB_PATH")
    discord_webhook_url: str = Field("", env="DISCORD_WEBHOOK_URL")
    discord_site_url: str = Field("https://wongram.shop/site2", env="DISCORD_SITE_URL")
    # Discord notifications
    discord_enabled: bool = Field(True, env="DISCORD_ENABLED")
    discord_timeout_sec: int = Field(8, env="DISCORD_TIMEOUT_SEC")
    discord_retry_max: int = Field(3, env="DISCORD_RETRY_MAX")
    timezone: str = Field("Asia/Seoul", env="TIMEZONE")
    as_of_tz: str = Field("Asia/Seoul", env="AS_OF_TZ")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    # Engine tuning (WarRoom v2.0 "Hybrid Model")
    defcon2_score_threshold: float = Field(2.0, env="DEFCON2_SCORE_THRESHOLD")
    defcon1_score_threshold: float = Field(3.5, env="DEFCON1_SCORE_THRESHOLD")
    normal_score_threshold: float = Field(2.0, env="NORMAL_SCORE_THRESHOLD")
    defcon1_exit_score_threshold: float = Field(3.0, env="DEFCON1_EXIT_SCORE_THRESHOLD")

    weight_t10y2y_cross_up: float = Field(2.0, env="WEIGHT_T10Y2Y_CROSS_UP")
    weight_baml_spread_risk: float = Field(1.5, env="WEIGHT_BAML_SPREAD_RISK")
    weight_wei_recession_trend: float = Field(1.0, env="WEIGHT_WEI_RECESSION_TREND")
    weight_copper_gold_under_ma200: float = Field(0.5, env="WEIGHT_COPPER_GOLD_UNDER_MA200")
    weight_umcsent_low: float = Field(0.5, env="WEIGHT_UMCSENT_LOW")

    sahm_hard_trigger_threshold: float = Field(0.5, env="SAHM_HARD_TRIGGER_THRESHOLD")

    # Trend filter (MA200) - used for hybrid posture/allocation (does not change macro score).
    trend_series_id: str = Field("NASDAQ_DLY_IXIC", env="TREND_SERIES_ID")
    trend_ma_window: int = Field(200, env="TREND_MA_WINDOW")

    # Optional external asset universe CSV (for portfolio recommendations).
    # If empty, the app will try to auto-detect a CSV containing BTCUSD/USDKRW/XAUUSD columns.
    asset_universe_csv_path: str = Field("", env="ASSET_UNIVERSE_CSV_PATH")

    auto_refresh_portfolio: bool = Field(True, env="AUTO_REFRESH_PORTFOLIO")
    auto_refresh_min_interval_sec: int = Field(30, env="AUTO_REFRESH_MIN_INTERVAL_SEC")
    auto_refresh_daily: bool = Field(True, env="AUTO_REFRESH_DAILY")
    auto_refresh_daily_min_interval_sec: int = Field(30, env="AUTO_REFRESH_DAILY_MIN_INTERVAL_SEC")
    auto_refresh_trigger_series: str = Field("", env="AUTO_REFRESH_TRIGGER_SERIES")

    # Allocation model
    # - fixed: use EQUITY_WEIGHT_* table (macro state + trend buckets)
    # - trend_vol_target: trend-following with vol targeting (cap leverage) + optional macro multipliers
    allocation_model: str = Field("trend_vol_target", env="ALLOCATION_MODEL")

    # Vol targeting (only used when ALLOCATION_MODEL=trend_vol_target)
    vol_window_days: int = Field(20, env="VOL_WINDOW_DAYS")
    target_vol_ann: float = Field(0.41, env="TARGET_VOL_ANN")  # annualized target vol (e.g. 0.41 == 41%)
    leverage_cap: float = Field(2.0, env="LEVERAGE_CAP")

    # Optional macro multipliers (only used when ALLOCATION_MODEL=trend_vol_target)
    macro_multiplier_normal: float = Field(1.0, env="MACRO_MULTIPLIER_NORMAL")
    macro_multiplier_defcon2: float = Field(1.0, env="MACRO_MULTIPLIER_DEFCON2")
    macro_multiplier_defcon1: float = Field(1.0, env="MACRO_MULTIPLIER_DEFCON1")

    # Portfolio recommendation engine (multi-asset)
    portfolio_allocation_model: str = Field("multi_asset_trend_vol", env="PORTFOLIO_ALLOCATION_MODEL")
    portfolio_ma_window: int = Field(200, env="PORTFOLIO_MA_WINDOW")
    portfolio_vol_window_days: int = Field(20, env="PORTFOLIO_VOL_WINDOW_DAYS")
    portfolio_target_vol_ann: float = Field(0.35, env="PORTFOLIO_TARGET_VOL_ANN")
    portfolio_leverage_cap: float = Field(2.0, env="PORTFOLIO_LEVERAGE_CAP")
    portfolio_bond10y_duration: float = Field(8.5, env="PORTFOLIO_BOND10Y_DURATION")
    portfolio_bond2y_duration: float = Field(1.9, env="PORTFOLIO_BOND2Y_DURATION")
    portfolio_bond_add_carry: bool = Field(True, env="PORTFOLIO_BOND_ADD_CARRY")
    portfolio_risk_assets: str = Field(
        "NASDAQ,BTC,COPPER,REMX,ALUMINUM,URANIUM",
        env="PORTFOLIO_RISK_ASSETS",
    )
    portfolio_macro_multiplier_normal: float = Field(1.0, env="PORTFOLIO_MACRO_MULTIPLIER_NORMAL")
    portfolio_macro_multiplier_defcon2: float = Field(1.0, env="PORTFOLIO_MACRO_MULTIPLIER_DEFCON2")
    portfolio_macro_multiplier_defcon1: float = Field(1.0, env="PORTFOLIO_MACRO_MULTIPLIER_DEFCON1")

    # Dynamic allocation defaults for fixed model (see app/engine.py).
    equity_weight_normal: float = Field(0.0, env="EQUITY_WEIGHT_NORMAL")
    equity_weight_defcon2_trend_up: float = Field(0.5, env="EQUITY_WEIGHT_DEFCON2_TREND_UP")
    equity_weight_defcon1_trend_up: float = Field(0.7, env="EQUITY_WEIGHT_DEFCON1_TREND_UP")
    equity_weight_defcon2_trend_down: float = Field(0.3, env="EQUITY_WEIGHT_DEFCON2_TREND_DOWN")
    equity_weight_defcon1_trend_down: float = Field(1.0, env="EQUITY_WEIGHT_DEFCON1_TREND_DOWN")
    equity_weight_defcon2_trend_unknown: float = Field(0.4, env="EQUITY_WEIGHT_DEFCON2_TREND_UNKNOWN")
    equity_weight_defcon1_trend_unknown: float = Field(0.8, env="EQUITY_WEIGHT_DEFCON1_TREND_UNKNOWN")

    # Stale handling
    expected_lag_days_1d: int = Field(2, env="EXPECTED_LAG_DAYS_1D")
    expected_lag_days_1w: int = Field(10, env="EXPECTED_LAG_DAYS_1W")
    expected_lag_days_1m: int = Field(40, env="EXPECTED_LAG_DAYS_1M")

    valid_for_days_1d: int = Field(4, env="VALID_FOR_DAYS_1D")
    valid_for_days_1w: int = Field(21, env="VALID_FOR_DAYS_1W")
    valid_for_days_1m: int = Field(62, env="VALID_FOR_DAYS_1M")

    hold_last_score_on_defcon_stale: bool = Field(True, env="HOLD_LAST_SCORE_ON_DEFCON_STALE")
    critical_series_ids: str = Field(
        "T10Y2Y,BAMLH0A0HYM2,COPPER_GOLD_RATIO,WEI,SAHMREALTIME,UMCSENT",
        env="CRITICAL_SERIES_IDS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
