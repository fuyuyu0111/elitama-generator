import os
import re
import time
import json
import psycopg2
import google.generativeai as genai
from psycopg2.extras import DictCursor


# --- 定数と設定 ---
# ★★★ 1. 環境変数の設定（後述）★★★
# スクリプトを実行する前に、環境変数としてAPIキーとデータベースURLを設定してください。
DATABASE_URL = os.environ.get('DATABASE_URL')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# ★★★ 2. モデルの指定 ★★★
FAST_MODEL_NAME = 'gemini-2.5-flash-lite'
POWERFUL_MODEL_NAME = 'gemini-2.5-pro'

# ★★★ 3. プロンプトの定義 ★★★
# ご指定いただいたプロンプトに、思考プロセスと一括処理用の具体例を反映させました。
PROMPT_TEMPLATE = """
あなたは、ゲーム「エイリアンのたまご」のスキルデータを解析する、高精度なデータ抽出エキスパートです。
以下のルールと具体例に厳密に従い、入力された個性の説明文から「味方の構成によって発動する条件」のみを抽出し、JSON配列形式で出力してください。

# 抽出ルール
1.  抽出対象は、「味方チームの構成（特定の属性やタイプが何体いるか）」を発動の**前提条件**とするもの**のみ**です。
2.  「敵」に依存する条件、「WAVE開始時」「自分が倒されるまで」のような時間や状態のみに依存する条件、「〇〇属性に与えるダメージアップ」のような効果対象の指定は、**発動の前提条件ではないため完全に無視**してください。
3.  出力は**単一のJSONオブジェクト**とします。このオブジェクトのキーは `"1"`, `"2"`, `"3"` とし、それぞれが入力の「個性1」「個性2」「個性3」に対応します。
4.  各キーの値は、`"conditions"` と `"confidence"` の2つのキーを持つオブジェクトとします。
5.  `"conditions"` の値は、抽出した条件オブジェクトの配列です。条件がない場合は空の配列 `[]` となります。
6.  各条件オブジェクトは、`"category"`, `"value"`, `"count"` の3つのキーを持ちます。
7.  `"confidence"` の値は、あなた自身のその個性の解析結果に対する自信度を **0.0から1.0の数値**で示してください。非常に自信がある場合は1.0、少しでも曖昧な点があれば低い数値を設定してください。

# categoryのマッピング
- 属性: "a"
- 所属: "b"
- はんい: "c"
- きょり: "d"
- タイプ: "e"

# valueのマッピング
- 属性: 動物:1, 昆虫:2, 機械:3, ナゾ:4
- 所属: 宇宙連合:1, 星間帝国:2, 恒星連邦:3, unknown:4, 銀河同盟:5
- はんい: たんたい:1, はんい:2
- きょり: ちかい:1, ふつう:2, とおい:3
- タイプ: 海:A, 夜:B, 氷:C, ラブ:D, 空:E, 音:F, 魔術:G, 熱:H, 大和:I, エレメント:J, 新星:K, チケット:L, 突然変異:M, 競技:N, 盗賊:O, 祈祷:P, 闇:Q, カタログIP:AA

# 具体例
---
入力:
個性1: 「WAVE開始時、10秒間、味方全体のいどうを60アップするぞ！」
個性2: 「味方に動物属性が1体いると、与ダメージを200%アップ！2体以上いると、さらに40%アップ！」
個性3: 「自分以外の味方にナゾ属性が3体以上いると、つよさを100％アップするぞ！」

思考プロセス:
1.  個性1を分析する。「WAVE開始時」「10秒間」は時間条件であり、チーム構成条件ではないため、抽出対象外。conditionsは空配列[]となる。自信度は1.0。
2.  個性2を分析する。「味方に動物属性が1体いると」はチーム構成条件。基本条件である1体を抽出する。categoryは'a'、valueは'1'、countは1となる。自信度は1.0。
3.  個性3を分析する。「自分以外の味方にナゾ属性が3体以上いると」はチーム構成条件。categoryは'a'、valueは'4'、countは3となる。自信度は1.0。
4.  上記分析に基づき、最終的なJSONを組み立てる。

出力:
```json
{{
  "1": {{
    "conditions": [],
    "confidence": 1.0
  }},
  "2": {{
    "conditions": [
      {{"category": "a", "value": "1", "count": 1}}
    ],
    "confidence": 1.0
  }},
  "3": {{
    "conditions": [
      {{"category": "a", "value": "4", "count": 3}}
    ],
    "confidence": 1.0
  }}
}}

入力:
個性1: 「味方に機械属性がいると、たいりょくとつよさを70%アップし、昆虫属性に与えるダメージを80%アップするぞ！」
個性2: 「やるきを200アップ、クリティカル率を50%アップし、デバフ（呪縛・気絶・いどう）への抵抗力を持つぞ！＜個性レベル＋６＞味方全員にやるきダウンへの抵抗力をつける」
個性3: 「味方に動物属性がいると、自分のたいりょくとつよさを120%アップ、昆虫/ナゾ属性に与えるダメージを100%アップし、攻撃範囲：はんいの敵からの被ダメージを40%軽減するぞ！」

思考プロセス:

個性1: 「味方に機械属性がいると」はチーム構成条件。「昆虫属性に与えるダメージ」は効果対象の指定なので無視する。

個性2: 発動条件がなく、自身のステータスアップと味方への効果付与のみ。チーム構成条件ではないので抽出対象は0件。

個性3: 「味方に動物属性がいると」はチーム構成条件。「昆虫/ナゾ属性に与えるダメージ」「はんいの敵からの被ダメージ」は効果対象の指定なので無視する。

出力:
```json
{{
  "1": {{
    "conditions": [
      {{"category": "a", "value": "3", "count": 1}}
    ],
    "confidence": 1.0
  }},
  "2": {{
    "conditions": [],
    "confidence": 1.0
  }},
  "3": {{
    "conditions": [
      {{"category": "a", "value": "1", "count": 1}}
    ],
    "confidence": 1.0
  }}
}}

本番の依頼
入力:
個性1: "{skill_text1}"
個性2: "{skill_text2}"
個性3: "{skill_text3}"

思考プロセス:
1． 各個性の説明文を注意深く読み、上記の抽出ルールに基づいて「味方の構成によって発動する条件」を特定します。
2． 各個性について、抽出した条件を対応するJSONオブジェクトにまとめます。
3． 各個性について、解析結果に対する自信度を0.0から1.0の範囲で評価します。
4． 最終的に、3つの個性それぞれについて、指定されたJSON形式で出力します。

出力:
"""

def get_db_connection():
    # """環境変数から接続情報を読み取り、データベース接続を返す"""
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    # cursor_factory=DictCursor を追加して、結果を辞書形式で受け取れるようにします
    return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=DictCursor)

def fetch_aliens_from_db(conn):
    # """DBから解析対象のエイリアン情報を取得する"""
    with conn.cursor() as cur:
        cur.execute("SELECT id, skill_text1, skill_text2, skill_text3 FROM alien ORDER BY id")
        # ↓↓↓↓ 修正箇所 ↓↓↓↓
        # DictCursorの結果は特殊な型なので、標準の辞書のリストに変換します
        aliens = [dict(row) for row in cur.fetchall()]
        # ↑↑↑↑ 修正箇所 ↑↑↑↑
    return aliens

def analyze_skills_for_alien(model, alien):
    # """エイリアン1体分の3つのスキルテキストをまとめてLLMに送信し、解析結果を返す"""
    prompt = PROMPT_TEMPLATE.format(
        skill_text1=alien.get('skill_text1') or "（なし）",
        skill_text2=alien.get('skill_text2') or "（なし）",
        skill_text3=alien.get('skill_text3') or "（なし）"
    )
    try:
        response = model.generate_content(prompt)
        # ↓↓↓↓ 修正箇所 ↓↓↓↓
        # モデルの応答からJSON部分のみを抜き出す正規表現を修正・強化
        json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```|(\{[\s\S]*?\})', response.text)
        
        if not json_match:
            raise ValueError("モデルの応答から有効なJSONを見つけられませんでした。")
        
        # マッチしたどちらかのグループからJSON文字列を取得
        json_text = json_match.group(1) or json_match.group(2)
        parsed_json = json.loads(json_text)
        
        return parsed_json if isinstance(parsed_json, dict) else None
        # ↑↑↑↑ 修正箇所 ↑↑↑↑

    except Exception as e:
        print(f"  -> LLM解析またはJSONパースでエラー: {e}")
        if 'response' in locals():
            print(f"  -> RAWレスポンス: {response.text}")
        # ↓↓↓↓ 修正箇所 ↓↓↓↓
        return None # エラーが発生した場合はNoneを返すように修正
    
def insert_skills_to_db(conn, skills_to_insert):
    """解析したスキル条件をDBに書き込む（既存データは全削除してから挿入）"""
    if not skills_to_insert:
        print("\n書き込むデータがありません。")
        return
    with conn.cursor() as cur:
        print("\n既存のskillテーブルのデータを削除しています...")
        cur.execute("DELETE FROM skill")
        print(f"{len(skills_to_insert)}件の新しいスキルデータを書き込んでいます...")
        for skill in skills_to_insert:
            cur.execute(
                """
                INSERT INTO skill (id, skill_number, condition_type, condition_value, condition_count)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (skill['alien_id'], skill['skill_num'], skill['category'], skill['value'], skill['count'])
            )
    print("データベースへの書き込みが完了しました。")

# --- メインの実行部分 ---
if __name__ == '__main__':
    if not GEMINI_API_KEY or not DATABASE_URL:
        raise ValueError("環境変数 'GEMINI_API_KEY' と 'DATABASE_URL' を設定してください。")
    
    genai.configure(api_key=GEMINI_API_KEY)
    fast_model = genai.GenerativeModel(FAST_MODEL_NAME)
    powerful_model = genai.GenerativeModel(POWERFUL_MODEL_NAME)
    print(f"高速モデル: '{FAST_MODEL_NAME}', 高性能モデル: '{POWERFUL_MODEL_NAME}' を使用します。")

    # ★★★ 変更点1: 最初にデータを読み込んで、すぐに接続を切断する ★★★
    try:
        conn = get_db_connection()
        print("データベースからエイリアンデータを読み込んでいます...")
        aliens = fetch_aliens_from_db(conn)
        print(f"{len(aliens)}体のエイリアンデータを読み込みました。")
    finally:
        if conn:
            conn.close()
            print("データ読み込み完了。一時的にデータベース接続をクローズしました。")

    all_skill_conditions = []
    low_confidence_targets = []
    final_review_list = []
    
    # --- フェーズ1: 高速モデルによる一次解析 (この間はDBに接続しない) ---
    print("\n--- フェーズ1: 高速モデルによる一次解析を開始します ---")
    for i, alien in enumerate(aliens, 1):
        print(f"[{i}/{len(aliens)}] 図鑑No.{alien['id']} を一次解析中...")
        
        analysis_result = analyze_skills_for_alien(fast_model, alien)
        
        if not analysis_result:
            print(f"  -> 解析失敗。再解析リストに追加します。")
            low_confidence_targets.append(alien)
            time.sleep(4.1)
            continue

        is_low_confidence = False
        for skill_num_str in ["1", "2", "3"]:
            confidence = analysis_result.get(skill_num_str, {}).get("confidence", 0.0)
            if confidence < 0.95:
                is_low_confidence = True
                break
        
        if is_low_confidence:
            print(f"  -> 低信度の結果を検出。再解析リストに追加します。")
            low_confidence_targets.append(alien)
        else:
            print(f"  -> 高信度の結果を検出。DB書き込みリストに追加します。")
            for skill_num_str in ["1", "2", "3"]:
                result_data = analysis_result.get(skill_num_str, {})
                for cond in result_data.get("conditions", []):
                    all_skill_conditions.append({
                        'alien_id': alien['id'], 'skill_num': int(skill_num_str),
                        'category': cond.get('category'), 'value': cond.get('value'),
                        'count': int(cond.get('count', 1)),
                    })
        time.sleep(4.1)

    # --- フェーズ2: 高性能モデルによる再解析 (この間もDBに接続しない) ---
    if low_confidence_targets:
        print(f"\n--- フェーズ2: {len(low_confidence_targets)}件の低信度データを高性能モデルで再解析します ---")
        for i, alien in enumerate(low_confidence_targets, 1):
            print(f"[{i}/{len(low_confidence_targets)}] 図鑑No.{alien['id']} を再解析中...")
            analysis_result = analyze_skills_for_alien(powerful_model, alien)

            if not analysis_result:
                print(f"  -> 再解析に失敗。手動確認リストに追加します。")
                for num in ["1", "2", "3"]:
                    final_review_list.append({'id': alien['id'], 'skill_num': num, 'skill_text': alien.get(f'skill_text{num}'), 'parsed_conditions': [{'error': '再解析失敗'}]})
                time.sleep(12.1)
                continue

            print(f"  -> 再解析完了。結果をDB書き込みリストに追加します。")
            for skill_num_str in ["1", "2", "3"]:
                result_data = analysis_result.get(skill_num_str, {})
                conditions = result_data.get("conditions", [])
                confidence = result_data.get("confidence", 0.0)
                
                if confidence < 0.95:
                     final_review_list.append({
                        'id': alien['id'], 'skill_num': skill_num_str,
                        'skill_text': alien.get(f'skill_text{skill_num_str}'),
                        'parsed_conditions': conditions
                    })

                for cond in conditions:
                    all_skill_conditions.append({
                        'alien_id': alien['id'], 'skill_num': int(skill_num_str),
                        'category': cond.get('category'), 'value': cond.get('value'),
                        'count': int(cond.get('count', 1)),
                    })
            time.sleep(12.1)

    # ★★★ 変更点2: 書き込み直前に再度接続し、終わったら切断する ★★★
    conn = None
    try:
        print("\n--- フェーズ3: DB書き込みと最終報告 ---")
        conn = get_db_connection()
        insert_skills_to_db(conn, all_skill_conditions)
        conn.commit()
        print("\nデータベースへの変更をコミットしました。")
    except (Exception, psycopg2.Error) as error:
        print("\nDB書き込み中にエラーが発生しました。", error)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            print("データベース接続を(書き込み後に)クローズしました。")

    if final_review_list:
        print("\n" + "="*60)
        print("★★★【最終報告】手動確認が必要な解析結果 ★★★")
        print(f"高性能モデルで再解析しても自信度が低かったものが {len(final_review_list)} 件あります。")
        print("="*60)
        for item in final_review_list:
            print(f"\n[図鑑No]: {item['id']} - 個性{item['skill_num']}")
            print(f"[個性説明文]: {item['skill_text']}")
            print("[LLMによる最終解析結果]:")
            if not item['parsed_conditions']:
                print("  -> 条件なし")
            else:
                for cond in item['parsed_conditions']:
                    print(f"  -> Category: {cond.get('category')}, Value: {cond.get('value')}, Count: {cond.get('count', 1)}")
        print("\n" + "="*60)

    print("\n--- 全ての処理が完了しました ---")