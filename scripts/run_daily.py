import subprocess
import sys
from pathlib import Path

from app import db
from app.exporter import export_daily_states_csv, export_nasdaq_1d_csv
from app.engine import evaluate
from app.notifier import notify
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    base_dir = Path(__file__).resolve().parent.parent
    with db.db_session(settings.db_path) as conn:
        db.init_db(conn)
        result = evaluate(conn)
        export_daily_states_csv(conn)
        export_nasdaq_1d_csv(conn)

    command = [
        sys.executable,
        str(base_dir / "scripts" / "build_portfolio_daily.py"),
        "--use-db",
        "--db-path",
        str(Path(settings.db_path)),
        "--states-csv",
        str(base_dir / "data" / "market_states_daily.csv"),
        "--out-csv",
        str(base_dir / "data" / "portfolio_daily.csv"),
    ]
    proc = subprocess.run(command, cwd=str(base_dir), capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        print(f"portfolio refresh failed: {err}", file=sys.stderr)
    elif proc.stdout.strip():
        print(proc.stdout.strip())
    notify(result)
    print(f"{result.as_of_date} -> {result.state} (score={result.score})")


if __name__ == "__main__":
    main()
