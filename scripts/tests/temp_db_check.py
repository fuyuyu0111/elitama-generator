import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from scripts.scraping.full_scraper import get_db_connection


def main():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(id) FROM alien")
        max_id = cur.fetchone()[0]
        print(f"DB latest alien id: {max_id}")

        cur.execute("SELECT id, name FROM alien ORDER BY id DESC LIMIT 5")
        latest = cur.fetchall()
        print("Latest 5 aliens:")
        for row in latest:
            print(f"  ID {row[0]}: {row[1]}")

        cur.execute("SELECT COUNT(*) FROM alien")
        total = cur.fetchone()[0]
        print(f"Total aliens in DB: {total}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()

