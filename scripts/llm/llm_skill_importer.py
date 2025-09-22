import os
import time
import json
import psycopg2
import math
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from pprint import pprint
from urllib.parse import urljoin, urlparse, parse_qs

# --- 定数と設定 ---
DATABASE_URL = os.environ.get('DATABASE_URL')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# ★★★ ここからモデルの優先順位と制限を定義 ★★★
MODEL_PRIORITY = [
    {'name': 'gemini-2.5-flash-lite', 'rpd': 1000, 'rpm': 15},
    {'name': 'gemini-2.0-flash-lite', 'rpd': 200,  'rpm': 30},
    {'name': 'gemini-2.5-flash',      'rpd': 250,  'rpm': 10},
    {'name': 'gemini-2.0-flash',      'rpd': 200,  'rpm': 15},
    {'name': 'gemini-2.5-pro',        'rpd': 100,  'rpm': 5},
]
# ★★★ ここまで ★★★

PROMPT_TEMPLATE = """
あなたは、ゲーム「エイリアンのたまご」のスキルデータを解析する、高精度なデータ抽出エキスパートです。
以下のルールと具体例に厳密に従い、入力された個性の説明文から「味方の構成によって発動する条件」のみを抽出し、JSON配列形式で出力してください。

# 抽出ルール
1.  抽出対象は、**味方チームの構成**（特定の属性やタイプが何体いるか）を**発動の前提条件**とするもののみです。
2.  **「敵」に依存する条件は完全に無視してください。**
3.  **「WAVE開始時」「自分が倒されるまで」「〇秒間」のような、時間や自分自身の状態に依存するだけの効果は、チーム構成が条件ではないため抽出しないでください。**
4.  **「〇〇属性に与えるダメージアップ」や「〇〇タイプの味方を回復」のような、効果が及ぶ対象の指定は、発動の前提条件ではないため抽出しないでください。**
5.  出力は必ずJSON配列形式とします。該当する条件がない場合は、空の配列 `[]` を返してください。
6.  各条件は、`category`, `value`, `count` の3つのキーを持つオブジェクトとしてください。
7.  `count`は、説明文に「〇体以上」などの指定があればその数値を、指定がなければ `1` としてください。

# categoryのマッピング
- 属性: "a"
- 所属: "b"
- タイプ: "e"

# valueのマッピング
- 属性: 動物:1, 昆虫:2, 機械:3, ナゾ:4
- 所属: 宇宙連合:1, 星間帝国:2, 恒星連邦:3, unknown:4, 銀河同盟:5
- タイプ: 海:A, 夜:B, 氷:C, ラブ:D, 空:E, 音:F, 魔術:G, 熱:H, 大和:I, エレメント:J, 新星:K, チケット:L, 突然変異:M, パックマンシリーズまたはカタログIP:AA

# 具体例
---
入力文: 「味方に海タイプがいると、自分のクリティカルダメージを250%アップするぞ！同様に大和タイプがいると、自分の特技与ダメージを200%アップ！」
出力:
[
  {{"category": "e", "value": "A", "count": 1}},
  {{"category": "e", "value": "I", "count": 1}}
]
---
入力文: 「自分以外の味方にナゾ属性が3体以上いると、つよさを100％アップするぞ！」
出力:
[
  {{"category": "a", "value": "4", "count": 3}}
]
---
入力文: 「WAVE開始時、10秒間、味方全体のいどうを60アップするぞ！」
出力:
[]
---
入力文: 「味方に機械属性がいると、たいりょくとつよさを70%アップし、昆虫属性に与えるダメージを80%アップするぞ！」
出力:
[
  {{"category": "a", "value": "3", "count": 1}}
]
---
入力文: 「やるきを200アップ、クリティカル率を50%アップし、デバフ（呪縛・気絶・いどう）への抵抗力を持つぞ！＜個性レベル＋６＞味方全員にやるきダウンへの抵抗力をつける」
出力:
[]
---
入力文: 「味方に動物属性がいると、自分のたいりょくとつよさを120%アップ、昆虫/ナゾ属性に与えるダメージを100%アップし、攻撃範囲：はんいの敵からの被ダメージを40%軽減するぞ！」
出力:
[
  {{"category": "a", "value": "1", "count": 1}}
]
---

# 本番の依頼
入力文: "{skill_text}"
出力:
"""

# --- 関数定義 (内容は前回と同じ) ---
def get_db_connection():
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def fetch_aliens_from_db(conn):
    aliens = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, skill_text1, skill_text2, skill_text3 FROM alien ORDER BY id")
        for row in cur.fetchall():
            aliens.append({
                'id': row[0], 'skill_text1': row[1],
                'skill_text2': row[2], 'skill_text3': row[3],
            })
    return aliens

def analyze_skill_with_llm(model, skill_text):
    if not skill_text or not skill_text.strip():
        return []
    prompt = PROMPT_TEMPLATE.format(skill_text=skill_text)
    try:
        response = model.generate_content(prompt)
        json_text = response.text.strip().replace('`', '').replace('json', '')
        parsed_json = json.loads(json_text)
        return parsed_json if isinstance(parsed_json, list) else []
    except Exception as e:
        print(f"  -> LLM解析エラー: {e}")
        return []

def insert_skills_to_db(conn, skills_to_insert):
    if not skills_to_insert:
        return
    with conn.cursor() as cur:
        print("\n既存のskillテーブルのデータを削除しています...")
        cur.execute("DELETE FROM skill")
        print(f"{len(skills_to_insert)}件の新しいスキルデータを書き込んでいます...")
        for skill in skills_to_insert:
            cur.execute(
                "INSERT INTO skill (id, skill_number, condition_type, condition_value, condition_count) VALUES (%s, %s, %s, %s, %s)",
                (skill['alien_id'], skill['skill_num'], skill['category'], skill['value'], skill['count'])
            )
    print("データベースへの書き込みが完了しました。")

# --- メインの実行部分 ---
if __name__ == '__main__':
    if not GEMINI_API_KEY:
        raise ValueError("環境変数 'GEMINI_API_KEY' が設定されていません。")
    
    genai.configure(api_key=GEMINI_API_KEY)

    # ★★★ ここからモデル管理ロジック ★★★
    current_model_index = 0
    requests_on_current_model = 0
    current_model_info = MODEL_PRIORITY[current_model_index]
    model = genai.GenerativeModel(current_model_info['name'])
    print(f"初期モデルとして '{current_model_info['name']}' (RPD: {current_model_info['rpd']}) を使用します。")
    # ★★★ ここまで ★★★

    conn = None
    try:
        conn = get_db_connection()
        print("データベースからエイリアンデータを読み込んでいます...")
        aliens = fetch_aliens_from_db(conn)
        print(f"{len(aliens)}体のエイリアンデータを読み込みました。")

        all_skill_conditions = []
        print("\nLLMによる個性テキストの解析を開始します...")
        
        total_skills = len(aliens) * 3
        current_skill_count = 0

        for i, alien in enumerate(aliens, 1):
            print(f"[{i}/{len(aliens)}] 図鑑No.{alien['id']} を処理中...")
            
            for skill_num in range(1, 4):
                current_skill_count += 1
                skill_text = alien[f'skill_text{skill_num}']
                
                # ★★★ ここからモデル切り替え判定 ★★★
                if requests_on_current_model >= (current_model_info['rpd'] - 10):
                    current_model_index += 1
                    if current_model_index >= len(MODEL_PRIORITY):
                        raise Exception("利用可能な全てのモデルのRPD上限に達しました。")
                    
                    requests_on_current_model = 0
                    current_model_info = MODEL_PRIORITY[current_model_index]
                    model = genai.GenerativeModel(current_model_info['name'])
                    print("\n" + "="*50)
                    print(f"RPD上限に達したため、モデルを '{current_model_info['name']}' (RPD: {current_model_info['rpd']}) に切り替えます。")
                    print("="*50 + "\n")
                # ★★★ ここまで ★★★

                conditions = analyze_skill_with_llm(model, skill_text)
                requests_on_current_model += 1
                
                print(f"  個性{skill_num}: {len(conditions)}件の条件を抽出 ({current_skill_count}/{total_skills})")

                for cond in conditions:
                    all_skill_conditions.append({
                        'alien_id': alien['id'], 'skill_num': skill_num,
                        'category': cond.get('category'), 'value': cond.get('value'), 'count': cond.get('count'),
                    })
                
                # ★★★ 動的な待機時間 ★★★
                # RPMに基づいて待機時間を計算 (60秒 / RPM) + 安全マージン
                sleep_time = 60 / current_model_info['rpm'] + 0.1
                time.sleep(sleep_time)

        insert_skills_to_db(conn, all_skill_conditions)
        
        conn.commit()
        print("\nデータベースへの変更をコミットしました。")

    except (Exception, psycopg2.Error) as error:
        print("\nエラーが発生したため、処理を中断しました。", error)
        if conn:
            conn.rollback()
            print("データベースへの変更をロールバックしました。")
    finally:
        if conn:
            conn.close()
            print("データベース接続をクローズしました。")

    print("\n--- 全ての処理が完了しました ---")