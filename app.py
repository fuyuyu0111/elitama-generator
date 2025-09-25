import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, jsonify
from functools import lru_cache # キャッシュ機能のためにlru_cacheをインポート

app = Flask(__name__)

# --- ★★★ 修正点1: データベース接続処理をキャッシュ対応に ★★★ ---
# データベース接続関数は変更なし
def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    if not conn_str:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(conn_str, sslmode='require', cursor_factory=DictCursor)

# lru_cacheデコレータを使用して、関数の結果をキャッシュします。
# maxsize=2 は、aliens と skills の2つのデータをキャッシュすることを意味します。
@lru_cache(maxsize=2)
def get_initial_data():
    """
    DBから初期表示に必要なデータを取得し、キャッシュする関数。
    この関数は一度実行されると、結果がメモリに保存されます。
    """
    print("--- Fetching data from database for caching ---") # データベースアクセス時にログ出力
    conn = get_db_connection()
    cur = conn.cursor()

    # エイリアン一覧を取得するクエリ
    aliens_query = """
        SELECT
            id, name, attribute, affiliation, attack_area, attack_range, role,
            type_1, type_2, type_3, type_4,
            skill_no1 AS skill_no1_name,
            skill_no2 AS skill_no2_name,
            skill_no3 AS skill_no3_name
        FROM
            alien
        ORDER BY id DESC
    """
    cur.execute(aliens_query)
    aliens = cur.fetchall()

    # 全てのスキル条件を取得するクエリ
    cur.execute('SELECT * FROM skill')
    all_requirements_raw = cur.fetchall()
    
    cur.close()
    conn.close()

    # スキルデータを整形
    all_requirements = {}
    for req in all_requirements_raw:
        alien_id_str = str(req['id'])
        if alien_id_str not in all_requirements:
            all_requirements[alien_id_str] = []
        all_requirements[alien_id_str].append(dict(req))
    
    return aliens, json.dumps(all_requirements)

# --- ★★★ ここまでが修正点1 ★★★ ---

# メインページ表示
@app.route('/')
def index():
    # --- ★★★ 修正点2: キャッシュされたデータを直接利用 ★★★ ---
    # 毎回DBに接続するのではなく、キャッシュされたデータを取得します。
    # 最初のアクセスでのみ get_initial_data() が実行され、DBに接続します。
    # 2回目以降のアクセスでは、キャッシュから即座にデータが返されます。
    aliens, all_requirements_json = get_initial_data()
    # --- ★★★ ここまでが修正点2 ★★★ ---
    
    return render_template(
        'index.html',
        aliens=aliens,
        all_requirements_json=all_requirements_json
    )

# 詳細情報取得API (キャッシュ対応)
# こちらもlru_cacheでキャッシュすることで、一度取得したエイリアンの詳細は
# DBに問い合わせず、メモリから高速に返すようになります。
@app.route('/api/alien_details/<int:alien_id>')
@lru_cache(maxsize=128) # 128体分の詳細データをキャッシュ
def get_alien_details(alien_id):
    print(f"--- Fetching details for alien {alien_id} from database ---") # DBアクセス時にログ出力
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        "SELECT skill_text1, skill_text2, skill_text3 FROM alien WHERE id = %s", 
        (alien_id,)
    )
    
    details = cur.fetchone()
    cur.close()
    conn.close()
    
    if details:
        return jsonify(dict(details))
    else:
        return jsonify({})

# ローカルテスト用の実行ブロック (変更なし)
if __name__ == '__main__':
    app.run(debug=True)