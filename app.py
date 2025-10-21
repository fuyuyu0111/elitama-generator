from dotenv import load_dotenv
load_dotenv()

import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, jsonify
from functools import lru_cache

app = Flask(__name__)

def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    if not conn_str:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(conn_str, sslmode='require', cursor_factory=DictCursor)

@lru_cache(maxsize=1)
def get_initial_data():
    print("--- データベースから全データを読み込んでキャッシュします ---")
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM alien ORDER BY id DESC")
    aliens_raw = cur.fetchall()

    cur.execute('SELECT * FROM skill')
    all_requirements_raw = cur.fetchall()
    
    cur.close()
    conn.close()

    aliens = [dict(row) for row in aliens_raw]
    all_requirements = {}
    for req in all_requirements_raw:
        alien_id_str = str(req['id'])
        if alien_id_str not in all_requirements:
            all_requirements[alien_id_str] = []
        all_requirements[alien_id_str].append(dict(req))
    
    return aliens, all_requirements

@app.route('/')
def index():
    aliens, all_requirements = get_initial_data()
    aliens_dict = {str(alien['id']): alien for alien in aliens}
    
    return render_template(
        'index.html',
        aliens=aliens,
        all_aliens_data=aliens_dict,
        all_requirements_data=all_requirements
    )

@app.route('/debug/check_text/<int:alien_id>')
def debug_check_text(alien_id):
    """デバッグ用: テキストの改行文字を確認"""
    aliens, _ = get_initial_data()
    alien = next((a for a in aliens if a['id'] == alien_id), None)
    if alien:
        return {
            'id': alien_id,
            'S_Skill_text': alien.get('S_Skill_text', ''),
            'S_Skill_text_repr': repr(alien.get('S_Skill_text', '')),
            'has_newline': '\n' in (alien.get('S_Skill_text', '') or ''),
            'skill_text1': alien.get('skill_text1', ''),
            'skill_text1_repr': repr(alien.get('skill_text1', '')),
            'skill_text1_has_newline': '\n' in (alien.get('skill_text1', '') or '')
        }
    return {'error': 'Alien not found'}, 404

if __name__ == '__main__':
    app.run(debug=False)