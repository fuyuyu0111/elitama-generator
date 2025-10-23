import psycopg2
from psycopg2.extras import DictCursor
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.environ.get('DATABASE_URL'), sslmode='require', cursor_factory=DictCursor)
cur = conn.cursor()

# 1. 全レコード数
cur.execute("SELECT COUNT(*) FROM skill_complete")
total = cur.fetchone()[0]
print(f"全レコード数: {total}")

# 2. ステータスごとの集計
cur.execute("""
    SELECT verification_status, COUNT(*) 
    FROM skill_complete 
    GROUP BY verification_status
""")
print("\n【ステータス別集計】")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}件")

# 3. サンプルデータ（最新5件）
cur.execute("""
    SELECT alien_id, skill_number, name, occupies_slot, verification_status, llm_analyzed_at
    FROM skill_complete
    ORDER BY alien_id DESC, skill_number
    LIMIT 10
""")
print("\n【サンプルデータ（最新10件）】")
for row in cur.fetchall():
    print(f"  ID:{row[0]} 個性{row[1]} {row[2][:20]}... 枠:{row[3]} ステータス:{row[4]} 解析日時:{row[5]}")

# 4. 特定のエイリアンのデータを確認（例：ID 925）
alien_id = 925
cur.execute("""
    SELECT skill_number, name, occupies_slot, verification_status
    FROM skill_complete
    WHERE alien_id = %s
    ORDER BY skill_number, group_id
""", (alien_id,))
print(f"\n【エイリアンID {alien_id} のデータ】")
for row in cur.fetchall():
    print(f"  個性{row[0]}: {row[1]} 枠:{row[2]} ステータス:{row[3]}")

cur.close()
conn.close()
