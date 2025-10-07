import os
import re
import json
import psycopg2
import google.generativeai as genai
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DATABASE_URL = os.environ.get('DATABASE_URL')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
MODEL_NAME = 'gemini-2.5-flash-lite'  # または 'gemini-1.5' など
DATASET_FILE = 'buff_finetuning_dataset_full.jsonl'

PROMPT_TEMPLATE_BASE = """
あなたは、ゲーム「エイリアンのたまご」の超専門家です。入力された個性説明文を解析し、発生する全ての「バフ効果」と「デバフ効果」を詳細なJSON形式で出力してください。

# ルール
1.  **効果グループ**: 説明文中で「、」や「・」でまとめられている効果（例：「つよさ・やるきをアップ」）は、同じ`group_id`を割り振ってください。関連性のない効果は`group_id`をインクリメントしてください。
2.  **キーの定義**: 各効果は以下のキーを持つJSONオブジェクトとしてください。
    - `group_id` (number): 効果のグループID。個性内で1から始まる。
    - `name` (string): 効果の統一された名称 (例: "つよさアップ")。
    - `target` (string): 効果対象のコード。
    - `value` (number): 効果量 (例: 60, 180)。不明または無関係な場合は 0。
    - `value_type` (string): 効果量の種別 ("PERCENT", "FLAT", "SECONDS")。
    - `duration` (number): 持続時間（秒）。永続効果（「倒されるまで」など）は 0。
    - `condition` (string): 発動条件のコード (例: "PERMANENT", "ATTACK_ONCE", "AFFIL_1_PRESENT")。
    - `occupies_slot` (boolean | null): バフ/デバフ枠を使用する場合はtrue, しない場合はfalse, 不明な場合はnull。
    - `is_debuff` (boolean): 敵へのデバフ効果の場合はtrue, 味方へのバフ効果の場合はfalse。
    - `awakening_required` (boolean): `＜個性覚醒(★６)＞`など、覚醒が必要な効果の場合はtrue。
3.  **発動条件(condition)の定義**:
    - `PERMANENT`: WAVE開始時から永続
    - `TIME_30S`: WAVE開始から30秒間
    - `ATTACK_ONCE`: 自分が初めて攻撃した時
    - `AFFIL_1_PRESENT`: 味方に宇宙連合がいると
    - (その他、`ATTR_1_PRESENT`のように柔軟に定義)
4.  **過去の正解例を最優先**で参考にしてください。

# 過去の正解例
{examples}

# 本番の依頼
入力: "{skill_text}"
出力:
"""

# (app.pyの残りの部分は、前回の回答とほぼ同じため省略します。主要なロジックはindex.htmlのJavaScriptに移行しています)
# ... get_db_connection, load_processed_data, 各APIエンドポイント ...
# ... ただし、get-alien-dataはLLM解析を1エイリアン(3個性)単位で行うように修正します ...

# (以下、app.pyの完全版)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=DictCursor)

def load_processed_data():
    processed_data = {}
    examples = []
    if not os.path.exists(DATASET_FILE):
        return processed_data, examples
    with open(DATASET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                processed_data[data['skill_id']] = data
                if data.get('output'):
                    example_output = json.dumps(data['output'], ensure_ascii=False)
                    examples.append(f"入力: \"{data['text_input']}\"\n出力:\n```json\n{example_output}\n```")
            except (json.JSONDecodeError, KeyError):
                continue
    return processed_data, examples

@app.route('/')
def index():
    processed_data, _ = load_processed_data()
    conn = None
    aliens = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM alien ORDER BY id DESC")
            aliens = [dict(row) for row in cur.fetchall()]
    finally:
        if conn: conn.close()
    
    for alien in aliens:
        statuses = []
        for i in range(1, 4):
            skill_id = f"{alien['id']}_{i}"
            if skill_id in processed_data:
                entry = processed_data[skill_id]
                if any(buff.get('occupies_slot') is None for buff in entry.get('output', [])):
                    statuses.append('unknown')
                else:
                    statuses.append('complete')
        
        if len(statuses) == 3 and all(s == 'complete' for s in statuses):
            alien['status'] = 'complete'
        elif any(s == 'unknown' for s in statuses):
            alien['status'] = 'unknown'
        else:
            alien['status'] = 'incomplete'
            
    return render_template('index.html', all_aliens=aliens)

@app.route('/get-alien-data/<int:alien_id>')
async def get_alien_data(alien_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM alien WHERE id = %s", (alien_id,))
            alien = dict(cur.fetchone())
    finally:
        if conn: conn.close()
    
    processed_data, past_examples = load_processed_data()
    
    # 3つの個性をまとめてAIに問い合わせる
    unprocessed_skills = []
    for i in range(1, 4):
        skill_id = f"{alien_id}_{i}"
        skill_text = alien.get(f'skill_text{i}')
        if skill_text and skill_id not in processed_data:
            unprocessed_skills.append({'number': i, 'text': skill_text})

    ai_suggestions = {}
    if unprocessed_skills and GEMINI_API_KEY:
        # この例では簡略化のため逐次実行しますが、並列実行も可能です
        for skill in unprocessed_skills:
            example_str = "\n---\n".join(past_examples[-5:])
            prompt = PROMPT_TEMPLATE_BASE.format(examples=example_str, skill_text=skill['text'])
            try:
                genai.configure(api_key=GEMINI_API_KEY)
                model = genai.GenerativeModel(MODEL_NAME)
                response = await model.generate_content_async(prompt)
                
                json_match = re.search(r'```json\s*(\[[\s\S]*?\])\s*```|(\[[\s\S]*?\])', response.text)
                if json_match:
                    json_text = json_match.group(1) or json_match.group(2)
                    ai_suggestions[str(skill['number'])] = json.loads(json_text)
                else:
                    ai_suggestions[str(skill['number'])] = []
            except Exception:
                ai_suggestions[str(skill['number'])] = []

    # 最終的なデータを構築
    for i in range(1, 4):
        skill_id = f"{alien_id}_{i}"
        if skill_id in processed_data:
            alien[f'skill_{i}_data'] = processed_data[skill_id]['output']
        else:
            alien[f'skill_{i}_data'] = ai_suggestions.get(str(i), [])

    return jsonify(alien)


@app.route('/get-unique-buff-names')
def get_unique_buff_names():
    buff_names = set()
    if not os.path.exists(DATASET_FILE):
        return jsonify([])
    with open(DATASET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                if 'output' in data and data['output']:
                    for buff in data['output']:
                        if 'name' in buff:
                            buff_names.add(buff['name'])
            except (json.JSONDecodeError, KeyError):
                continue
    return jsonify(sorted(list(buff_names)))

@app.route('/save-labels', methods=['POST'])
def save_labels():
    all_data = {}
    if os.path.exists(DATASET_FILE):
        with open(DATASET_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try: 
                    data = json.loads(line)
                    all_data[data['skill_id']] = data
                except json.JSONDecodeError: continue
    
    new_data = request.json.get('data')
    
    for new_entry in new_data:
        all_data[new_entry['skill_id']] = new_entry

    try:
        with open(DATASET_FILE, 'w', encoding='utf-8') as f:
            for skill_id in sorted(all_data.keys()):
                f.write(json.dumps(all_data[skill_id], ensure_ascii=False) + '\n')
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("サーバーを起動します。 http://127.0.0.1:5000 にアクセスしてください。")
    app.run(debug=True, port=5000)