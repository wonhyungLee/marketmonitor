import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow running as `python scripts/init_db.py` without PYTHONPATH.
sys.path.insert(0, str(BASE_DIR))

from app import db
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    with db.db_session(settings.db_path) as conn:
        db.init_db(conn)
    print(f"initialized database at {settings.db_path}")


if __name__ == "__main__":
    main()
