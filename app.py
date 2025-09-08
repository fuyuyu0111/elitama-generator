import os
import json
import psycopg2
from psycopg2.extras import DictCursor
# ★ jsonify をインポートに追加
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# データベース接続関数 (変更なし)
def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    if not conn_str:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(conn_str, sslmode='require', cursor_factory=DictCursor)

# メインページ表示
@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # --- ★★★ 修正点1: 初期読み込みデータを軽量化 ★★★ ---
    # skill_text1, skill_text2, skill_text3 の読み込みを削除します。
    # また、エイリアン一覧は降順で表示するように ORDER BY id DESC を追加します。
    query = """
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
    # --- ★★★ ここまでが修正点1 ★★★ ---

    cur.execute(query)
    aliens = cur.fetchall()
    cur.close()
    conn.close()
    
    # (以降の処理は変更なし)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM skill')
    all_requirements_raw = cur.fetchall()
    cur.close()
    conn.close()

    all_requirements = {}
    for req in all_requirements_raw:
        alien_id_str = str(req['id'])
        if alien_id_str not in all_requirements:
            all_requirements[alien_id_str] = []
        all_requirements[alien_id_str].append(dict(req))
    
    return render_template(
        'index.html',
        aliens=aliens,
        all_requirements_json=json.dumps(all_requirements)
    )

# --- ★★★ 修正点2: 詳細情報取得APIをここに追加 ★★★ ---
@app.route('/api/alien_details/<int:alien_id>')
def get_alien_details(alien_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 特定のIDのエイリアンから、個性説明文だけを取得
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
        # 見つからなかった場合は空のオブジェクトを返す
        return jsonify({})
# --- ★★★ ここまでが修正点2 ★★★ ---


# ローカルテスト用の実行ブロック (変更なし)
if __name__ == '__main__':
    app.run(debug=False)