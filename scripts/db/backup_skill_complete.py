"""skill_complete テーブルを JSONL としてバックアップするユーティリティ。"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups" / "skill_complete"


def backup_skill_complete(database_url: Optional[str] = None, backup_dir: Optional[Path] = None) -> Path:
    load_dotenv()
    db_url = database_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL が設定されていません。")

    target_dir = backup_dir or DEFAULT_BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = target_dir / f"skill_complete_{timestamp}.jsonl"

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM skill_complete ORDER BY effect_id")
            rows = cur.fetchall()
    finally:
        conn.close()

    with backup_path.open("w", encoding="utf-8") as fp:
        for row in rows:
            normalized = {}
            for key, value in row.items():
                if isinstance(value, datetime):
                    normalized[key] = value.isoformat()
                elif isinstance(value, Decimal):
                    normalized[key] = float(value)
                else:
                    normalized[key] = value
            json.dump(normalized, fp, ensure_ascii=False)
            fp.write("\n")

    return backup_path


if __name__ == "__main__":
    path = backup_skill_complete()
    print(f"skill_complete をバックアップしました: {path}")
