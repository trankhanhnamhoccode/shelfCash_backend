from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures.build_fake_workbook import build_fake_workbook


if __name__ == "__main__":
    destination = Path("runtime/fake_shelfcash.xlsx")
    build_fake_workbook(destination)
    print(f"Created {destination} ({destination.stat().st_size} bytes)")
