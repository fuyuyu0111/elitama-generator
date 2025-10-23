#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0インポート済みデータを保留状態に変更"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# verified と partial_verified のデータを on_hold に変更
cur.execute("""
    UPDATE skill_complete
    SET verification_status = 'on_hold',
        notes = 'Phase 0データ - 要求と枠の判定が不完全なため保留'
    WHERE verification_status IN ('verified', 'partial_verified')
""")

affected_rows = cur.rowcount
conn.commit()

print(f"✅ {affected_rows}件のレコードを on_hold に変更しました")

# 統計確認
cur.execute("""
    SELECT verification_status, COUNT(*) 
    FROM skill_complete 
    GROUP BY verification_status 
    ORDER BY verification_status
""")

print("\n=== 変更後の統計 ===")
for row in cur.fetchall():
    status = row[0] if row[0] else '(null)'
    count = row[1]
    print(f"{status}: {count}件")

cur.close()
conn.close()
