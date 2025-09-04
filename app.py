import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template

app = Flask(__name__)

# データベース接続関数 (変更なし)
def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(conn_str, cursor_factory=DictCursor)
    return conn

# メインページ表示
@app.route('/')
def index():
    # 1. 全エイリアンのデータを取得
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM alien ORDER BY id")
    aliens = cur.fetchall()
    cur.close()
    conn.close()
    
    # 2. 全要求（スキル）のデータを取得
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM skill')
    all_requirements_raw = cur.fetchall()
    cur.close()
    conn.close()

    # 3. 要求データをJavaScriptで扱いやすい形式に整形
    all_requirements = {}
    for req in all_requirements_raw:
        # DBから取得したidは数値なので、文字列に変換してキーにする
        alien_id_str = str(req['id'])
        if alien_id_str not in all_requirements:
            all_requirements[alien_id_str] = []
        # 各要求を辞書としてリストに追加
        all_requirements[alien_id_str].append(dict(req))
    
    # 4. 整形したデータをHTMLテンプレートに渡す
    return render_template(
        'index.html',
        aliens=aliens,
        all_requirements_json=json.dumps(all_requirements)
    )

# ここに存在した @app.route('/check') ... def check_party(): の部分は完全に削除します。

# ローカルテスト用の実行ブロック
if __name__ == '__main__':
    app.run(debug=True)