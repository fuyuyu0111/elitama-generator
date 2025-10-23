from dotenv import load_dotenv
load_dotenv()

import os
import re
import json
import psycopg2
import google.generativeai as genai
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DATABASE_URL = os.environ.get('DATABASE_URL')
GEMINI_API_KEY_FOR_DATE_CREATE = os.environ.get('GEMINI_API_KEY_FOR_DATE_CREATE')
MODEL_NAME = 'gemini-2.5-flash-lite'
DATASET_FILE = 'buff_finetuning_dataset_full.jsonl'

PROMPT_TEMPLATE_BASE = """
あなたは、ゲーム「エイリアンのたまご」の超専門家です。入力された個性説明文を解析し、戦闘開始時に発動する全ての「バフ効果」と「デバフ効果」を詳細なJSON形式で出力してください。

# 厳守すべき最優先ルール
1.  **命名規則の遵守**: あなたがこれから生成する全ての`name`は、後述の「命名規則」のリストに存在する名前と完全に一致させてください。リストにない名前を勝手に作成してはいけません。
2.  **バフ枠の推論**: 個性説明文と後述の「バフ枠(occupies_slot)のルール」を注意深く比較し、バフ枠を消費するか否か(`occupies_slot`)を可能な限り`true`か`false`で判断してください。どうしても判断が難しい場合のみ`null`を許可します。
3.  **抽出対象**: 「WAVE開始時」「〜〜中」など、戦闘開始と同時に自動で発動する永続効果、または時限効果のみを抽出してください。「自分が初めて攻撃した時」「倒された時」など、戦闘中の特定のアクションを起点とする効果は抽出**しないでください**。

{dynamic_naming_list}

# バフ枠(occupies_slot)のルール
- **`true`になりやすい**:
  - `〇〇無効`系の効果
  - `味方全員`を対象とするステータスアップやデバフ無効
  - `敵全員`を対象とするデバフ
  - `のけぞりガード`
- **`false`になりやすい**:
  - 「味方に〇〇がいると〜」のような**条件付きで発動**する`自分`対象のステータスアップ
  - `被ダメ軽減(対〇〇)`や`与ダメアップ(対〇〇)`のような特定対象への効果
  - `たいりょく吸収`, `ダメージ反射`, `回避貫通`

# その他ルール
- **【】の解釈**: 文中にある`【〇〇】`は句読点がなくても新しい効果の始まりを示す区切りです。
- **キーの定義**: `group_id`, `name`, `target`, `value`, `unit`, `duration`, `probability`, `occupies_slot`, `is_debuff`, `awakening_required` を持つこと。
- **単位(unit)の定義**: `PERCENT`, `SECONDS`, `COUNT`, `FLAT`, `NONE` のいずれか。
- **確率**: 記載がない場合は `probability` は100とすること。

# 思考プロセスと出力例
入力: "味方に昆虫属性がいると、与ダメージを200%アップ！2体以上いると、さらに40%アップ！"
思考プロセス:
1.  「味方に昆虫属性がいると」は条件付き。対象は自分。効果は「与ダメージを200%アップ」。
2.  命名規則リストを参照し、「与ダメージアップ」は「与ダメアップ」に変換する。
3.  バフ枠ルールに基づき、条件付きの自己バフなので`occupies_slot`は`false`と判断。
4.  「2体以上いると、さらに40%アップ」も同様に処理する。
出力:
```json
[
  {{"group_id": 1, "name": "与ダメアップ", "target": "SELF", "value": 200, "unit": "PERCENT", "duration": 0, "probability": 100, "occupies_slot": false, "is_debuff": false, "awakening_required": false}},
  {{"group_id": 2, "name": "与ダメアップ", "target": "SELF", "value": 40, "unit": "PERCENT", "duration": 0, "probability": 100, "occupies_slot": false, "is_debuff": false, "awakening_required": false}}
]
過去のあなたの成功例
{examples}

本番の依頼
入力: "{skill_text}"
出力:
"""

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=DictCursor)

def load_processed_data():
    processed_data = {}
    examples = []
    buff_names = set()
    debuff_names = set()

    if not os.path.exists(DATASET_FILE):
        return processed_data, examples, buff_names, debuff_names

    with open(DATASET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                processed_data[data['skill_id']] = data
                if data.get('output'):
                    # AIへのお手本（過去の正解例）を作成
                    example_output = json.dumps(data['output'], ensure_ascii=False)
                    examples.append(f"入力: \"{data['text_input']}\"\n出力:\n```json\n{example_output}\n```")
                    
                    # AIが参照する命名規則リスト用の名前を収集
                    for effect in data['output']:
                        if 'name' in effect:
                            if effect.get('is_debuff'):
                                debuff_names.add(effect['name'])
                            else:
                                buff_names.add(effect['name'])
            except (json.JSONDecodeError, KeyError):
                continue
    return processed_data, examples, buff_names, debuff_names

@app.route('/')
def index():
    processed_data, _, _, _ = load_processed_data()
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
        is_complete = True
        has_unknown = False
        # 3つの個性がすべて処理済みかチェック
        for i in range(1, 4):
            skill_id = f"{alien['id']}_{i}"
            if skill_id not in processed_data:
                is_complete = False
                break
            # 処理済みデータの中に一つでも不明な項目があればunknownフラグを立てる
            entry = processed_data[skill_id]
            if any(buff.get('occupies_slot') is None for buff in entry.get('output', [])):
                has_unknown = True
        
        if not is_complete:
            alien['status'] = 'incomplete'
        elif has_unknown:
            alien['status'] = 'unknown'
        else:
            alien['status'] = 'complete'
            
    return render_template('date_index.html', all_aliens=aliens)

@app.route('/get-alien-data/int:alien_id')
async def get_alien_data(alien_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM alien WHERE id = %s", (alien_id,))
            alien = dict(cur.fetchone())
    finally:
        if conn:
            conn.close()

    # 命名規則リストも取得するように変更
    processed_data, past_examples, buff_names, debuff_names = load_processed_data()

    unprocessed_skills = []
    for i in range(1, 4):
        skill_id = f"{alien_id}_{i}"
        skill_text = alien.get(f'skill_text{i}')
        if skill_text and skill_id not in processed_data:
            unprocessed_skills.append({'number': i, 'text': skill_text})

    ai_suggestions = {}
    if unprocessed_skills and GEMINI_API_KEY_FOR_DATE_CREATE:
        # --- ここからが動的にプロンプトを生成する部分 ---
        naming_list_str = "# 命名規則リスト（このリストにある名前を最優先で使用すること）\n"
        if buff_names:
            naming_list_str += "- **バフ**: " + ", ".join(sorted(list(buff_names))) + "\n"
        if debuff_names:
            naming_list_str += "- **デバフ**: " + ", ".join(sorted(list(debuff_names))) + "\n"
        # --- ここまで ---

        for skill in unprocessed_skills:
            example_str = "\n---\n".join(past_examples[-10:])  # 参考にする例を10件に増やす

            # プロンプトに動的に生成したリストを埋め込む
            prompt = PROMPT_TEMPLATE_BASE.format(
                examples=example_str,
                skill_text=skill['text'],
                dynamic_naming_list=naming_list_str
            )
            try:
                genai.configure(api_key=GEMINI_API_KEY_FOR_DATE_CREATE)
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
    debuff_names = set()
    if not os.path.exists(DATASET_FILE):
        return jsonify({"buffs": [], "debuffs": []})
    with open(DATASET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                if 'output' in data and data['output']:
                    for buff in data['output']:
                        if 'name' in buff:
                            if buff.get('is_debuff'):
                                debuff_names.add(buff['name'])
                            else:
                                buff_names.add(buff['name'])
            except (json.JSONDecodeError, KeyError):
                continue
    return jsonify({
        "buffs": sorted(list(buff_names)),
        "debuffs": sorted(list(debuff_names))
    })

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

    for skill_id, entry in all_data.items():
        if 'output' in entry and isinstance(entry['output'], list):
            for effect in entry['output']:
                if 'probability' not in effect:
                    effect['probability'] = 100


    try:
        with open(DATASET_FILE, 'w', encoding='utf-8') as f:
            for skill_id in sorted(all_data.keys()):
                f.write(json.dumps(all_data[skill_id], ensure_ascii=False) + '\n')
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/rename-effect', methods=['POST'])
def rename_effect():
    data = request.json
    old_name = data.get('old_name')
    new_name = data.get('new_name')

    if not old_name or not new_name:
        return jsonify({"error": "古い名前と新しい名前が必要です。"}), 400

    if not os.path.exists(DATASET_FILE):
        return jsonify({"error": "データセットファイルが見つかりません。"}), 404

    updated_entries = []
    changes_made = 0
    try:
        with open(DATASET_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if 'output' in entry and isinstance(entry['output'], list):
                        for effect in entry['output']:
                            if effect.get('name') == old_name:
                                effect['name'] = new_name
                                changes_made += 1
                    updated_entries.append(entry)
                except json.JSONDecodeError:
                    continue # 不正な行はスキップ

        # 変更があった場合のみファイルに書き込む
        if changes_made > 0:
            with open(DATASET_FILE, 'w', encoding='utf-8') as f:
                for entry in updated_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        
        return jsonify({"status": "success", "changes": changes_made})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- ▼▼▼ この関数をファイルの一番下（if __name__ == '__main__': の前）に追加 ▼▼▼ ---
@app.route('/get-aliens-by-effect', methods=['POST'])
def get_aliens_by_effect():
    data = request.json
    effect_name = data.get('effect_name')

    if not effect_name:
        return jsonify({"error": "効果名が必要です。"}), 400

    if not os.path.exists(DATASET_FILE):
        return jsonify({"aliens": []}) # ファイルがなくてもエラーにしない

    alien_ids_with_effect = set()
    try:
        with open(DATASET_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if 'output' in entry and isinstance(entry['output'], list):
                        for effect in entry['output']:
                            if effect.get('name') == effect_name:
                                # skill_id (e.g., "1604_1") から alien_id (e.g., 1604) を抽出
                                alien_id = int(entry['skill_id'].split('_')[0])
                                alien_ids_with_effect.add(alien_id)
                except (json.JSONDecodeError, ValueError):
                    continue
        
        if not alien_ids_with_effect:
            return jsonify({"aliens": []})

        # DBからエイリアン名を取得
        conn = get_db_connection()
        with conn.cursor() as cur:
            # IN句を安全に使うためのプレースホルダー生成
            placeholders = ','.join(['%s'] * len(alien_ids_with_effect))
            query = f"SELECT id, name FROM alien WHERE id IN ({placeholders}) ORDER BY id DESC"
            cur.execute(query, tuple(alien_ids_with_effect))
            aliens = [dict(row) for row in cur.fetchall()]
        
        return jsonify({"aliens": aliens})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals() and conn:
            conn.close()
# --- ▲▲▲ 追加はここまで ▲▲▲ ---

if __name__ == '__main__':
    print("サーバーを起動します。 http://127.0.0.1:5000 にアクセスしてください。")
    app.run(debug=True, port=5000)