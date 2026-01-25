import argparse
from pathlib import Path

from app import db
from app.exporter import export_daily_states_csv

try:
    from app.settings import get_settings
except Exception:
    get_settings = None


def _default_db_path() -> str:
    if get_settings is None:
        return "warroom.db"
    return str(get_settings().db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export daily_states table to market_states_daily.csv")
    parser.add_argument("--db-path", default="", help="Path to warroom.db (default: from .env)")
    parser.add_argument("--out-csv", default="", help="Output CSV (default: data/market_states_daily.csv)")
    args = parser.parse_args()

    db_path = args.db_path.strip() or _default_db_path()
    out_csv = Path(args.out_csv) if args.out_csv.strip() else None

    with db.db_session(db_path) as conn:
        out_path = export_daily_states_csv(conn, out_csv)

    print(f"wrote: {out_path.as_posix()}")


if __name__ == "__main__":
    main()
