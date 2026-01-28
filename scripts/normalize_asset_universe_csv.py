import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TARGETS = [
    BASE_DIR / "data" / "asset_universe.csv",
    BASE_DIR / "data" / "asset_universe.csv.fxcm_bak",
    BASE_DIR / "data" / "asset_universe.csv.bak",
]

def normalize(path: Path) -> bool:
    if not path.exists():
        return False

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "FXCM_COPPER" not in fieldnames and "COPPER" in fieldnames:
        return False

    new_fields = [f for f in fieldnames if f != "FXCM_COPPER"]
    if "COPPER" not in new_fields:
        new_fields.append("COPPER")

    for row in rows:
        if row.get("COPPER") in (None, ""):
            row["COPPER"] = row.get("FXCM_COPPER") or ""
        row.pop("FXCM_COPPER", None)

    backup = path.with_suffix(path.suffix + ".pre_norm")
    if not backup.exists():
        path.replace(backup)
    else:
        path.unlink()

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=new_fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"normalized {path.name} (backup: {backup.name})")
    return True


def main() -> None:
    changed = False
    for p in TARGETS:
        changed = normalize(p) or changed
    if not changed:
        print("no changes")


if __name__ == "__main__":
    main()
