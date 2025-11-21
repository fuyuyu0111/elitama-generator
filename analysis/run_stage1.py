"""
1段階目: 効果名と要求のみを抽出する解析スクリプト
effect_type と category は自動補完で付与
"""

import os
import time
import json
import re
import psycopg2
from psycopg2.extras import Json, DictCursor, execute_values
import asyncio
from decimal import Decimal
from datetime import datetime
from tqdm import tqdm
from pathlib import Path
import argparse
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from dotenv import load_dotenv
import google.generativeai as genai

# --- プロジェクトルート設定 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- 環境変数読み込み ---
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

# --- 環境変数 ---
DATABASE_URL = os.getenv("DATABASE_URL")
# 本番実行時は別のAPIキーを使用
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_API_KEY_1")

# --- 定数 ---
SOURCE_TABLE_ALIEN = "alien"
DEST_TABLE = "skill_text_verified_effects"
CORRECT_NAMES_TABLE = "correct_effect_names"
PROMPT_PATH = PROJECT_ROOT / "analysis" / "prompts" / "stage1_effect_names.md"
S_SKILL_PROMPT_PATH = PROJECT_ROOT / "analysis" / "prompts" / "s_skill_effect_names.md"
S_SKILL_EFFECT_DICT_PATH = PROJECT_ROOT / "analysis" / "prompts" / "s_skill_effect_names.json"
BACKUP_DIR = PROJECT_ROOT / "backups" / "stage1"

# --- LLMとレート制限の設定 ---
MODEL_NAME = "gemini-2.5-flash"
# TPM: 1,000,000 tokens/分、RPM: 10 req/分の制限を考慮
# テスト結果: 約7,052 tokens/リクエスト → RPM制限がボトルネック（6.00秒間隔必要）
# 安全マージン込みで6.5秒間隔に設定（実際のRPM: 9.2 req/分、TPM: 約65,000 tokens/分）
ACTUAL_INTERVAL = 6.5  # 秒

print(f"レート制限設定:")
print(f"  モデル: {MODEL_NAME}")
print(f"  リクエスト間隔: {ACTUAL_INTERVAL:.1f}秒")
print(f"  想定スループット: {60.0 / ACTUAL_INTERVAL:.1f} req/分")
print(f"  レート制限: TPM 1,000,000 tokens/分、RPM 10 req/分")

# --- キャッシュ ---
_CORRECT_NAMES_CACHE = None

# --- 効果名カテゴリマッピング（共通定義） ---
# このマッピングは auto_complete_classification と format_effect_names_for_prompt の両方で使用されます
PERSONALITY_EFFECT_CATEGORIES = {
    # バフ - アップ系
    "いどうアップ": ("BUFF", "BUFF_BOOST"),
    "おおきさアップ": ("BUFF", "BUFF_BOOST"),
    "たいりょくアップ": ("BUFF", "BUFF_BOOST"),
    "やるきアップ": ("BUFF", "BUFF_BOOST"),
    "つよさアップ": ("BUFF", "BUFF_BOOST"),
    "攻撃力アップ": ("BUFF", "BUFF_BOOST"),
    "クリティカルダメージアップ": ("BUFF", "BUFF_BOOST"),
    "クリティカル率アップ": ("BUFF", "BUFF_BOOST"),
    "たいりょく回復量アップ": ("BUFF", "BUFF_BOOST"),
    "回避率アップ": ("BUFF", "BUFF_BOOST"),
    "攻撃回数アップ": ("BUFF", "BUFF_BOOST"),
    "与ダメージアップ": ("BUFF", "BUFF_BOOST"),
    "通常攻撃与ダメージアップ": ("BUFF", "BUFF_BOOST"),
    "特技与ダメージアップ": ("BUFF", "BUFF_BOOST"),
    "特技頻度アップ": ("BUFF", "BUFF_BOOST"),
    
    # バフ - 軽減系
    "吹き飛ばし軽減": ("BUFF", "BUFF_REDUCE"),
    "毒ダメージ軽減": ("BUFF", "BUFF_REDUCE"),
    "毒効果時間短縮": ("BUFF", "BUFF_REDUCE"),
    "気絶時間短縮": ("BUFF", "BUFF_REDUCE"),
    "特技被ダメージ軽減": ("BUFF", "BUFF_REDUCE"),
    "被クリティカルダメージ軽減": ("BUFF", "BUFF_REDUCE"),
    "被ダメージ軽減": ("BUFF", "BUFF_REDUCE"),
    "通常攻撃被ダメージ軽減": ("BUFF", "BUFF_REDUCE"),
    
    # バフ - 抵抗系
    "いどうダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "おおきさダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "クリティカル率ダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "つよさダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "やるきダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "与ダメージダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "呪縛抵抗": ("BUFF", "BUFF_RESIST"),
    "回復量ダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "回避率ダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "急速ダメージ抵抗": ("BUFF", "BUFF_RESIST"),
    "持続ダメージ抵抗": ("BUFF", "BUFF_RESIST"),
    "攻撃不可抵抗": ("BUFF", "BUFF_RESIST"),
    "攻撃力ダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "毒抵抗": ("BUFF", "BUFF_RESIST"),
    "気絶抵抗": ("BUFF", "BUFF_RESIST"),
    "特技頻度ダウン抵抗": ("BUFF", "BUFF_RESIST"),
    "被ダメージアップ抵抗": ("BUFF", "BUFF_RESIST"),
    "足止め抵抗": ("BUFF", "BUFF_RESIST"),
    
    # バフ - その他バフ
    "たいりょく吸収": ("BUFF", "BUFF_OTHER"),
    "たいりょく回復": ("BUFF", "BUFF_OTHER"),
    "ダメージ反射": ("BUFF", "BUFF_OTHER"),
    "のけぞりアタック": ("BUFF", "BUFF_OTHER"),
    "のけぞりガード": ("BUFF", "BUFF_OTHER"),
    "ひきよせ": ("BUFF", "BUFF_OTHER"),
    "凶暴化": ("BUFF", "BUFF_OTHER"),
    "吹き飛ばし": ("BUFF", "BUFF_OTHER"),
    "回避無視": ("BUFF", "BUFF_OTHER"),
    "奮迅": ("BUFF", "BUFF_OTHER"),
    "属性狙い": ("BUFF", "BUFF_OTHER"),
    "復活": ("BUFF", "BUFF_OTHER"),
    "心眼": ("BUFF", "BUFF_OTHER"),
    "急速回復": ("BUFF", "BUFF_OTHER"),
    "自然回復": ("BUFF", "BUFF_OTHER"),
    "有終の美": ("BUFF", "BUFF_OTHER"),
    "無双": ("BUFF", "BUFF_OTHER"),
    "自爆": ("BUFF", "BUFF_OTHER"),
    "足止め解除": ("BUFF", "BUFF_OTHER"),
    "追加ダメージ": ("BUFF", "BUFF_OTHER"),
    "連撃": ("BUFF", "BUFF_OTHER"),
    "隠密": ("BUFF", "BUFF_OTHER"),
    
    # デバフ - ダウン系
    "いどうダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "おおきさダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "クリティカル率ダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "回復量ダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "つよさダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "やるきダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "与ダメージダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "反射ダメージダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "攻撃力ダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "特技与ダメージダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    "特技頻度ダウン": ("DEBUFF", "DEBUFF_REDUCE"),
    
    # デバフ - その他デバフ
    "クリティカルダメージアップ無効": ("DEBUFF", "DEBUFF_OTHER"),
    "たいりょく回復量アップ無効": ("DEBUFF", "DEBUFF_OTHER"),
    "凶暴化無効": ("DEBUFF", "DEBUFF_OTHER"),
    "反射無効": ("DEBUFF", "DEBUFF_OTHER"),
    "回避率アップ無効": ("DEBUFF", "DEBUFF_OTHER"),
    "急速ダメージ": ("DEBUFF", "DEBUFF_OTHER"),
    "特技頻度アップ無効": ("DEBUFF", "DEBUFF_OTHER"),
    "自動回復解除": ("DEBUFF", "DEBUFF_OTHER"),
    "被ダメージアップ": ("DEBUFF", "DEBUFF_OTHER"),
    "被ダメージ軽減無効": ("DEBUFF", "DEBUFF_OTHER"),
    "被ダメージ軽減解除": ("DEBUFF", "DEBUFF_OTHER"),
    
    # 状態異常 - 毒系
    "毒アタック": ("STATUS", "STATUS_POISON"),
    "毒キラー": ("STATUS", "STATUS_POISON"),
    "毒ダメージアップ": ("STATUS", "STATUS_POISON"),
    "毒効果時間延長": ("STATUS", "STATUS_POISON"),
    
    # 状態異常 - 気絶系
    "気絶アタック": ("STATUS", "STATUS_STUN"),
    "気絶キラー": ("STATUS", "STATUS_STUN"),
    "気絶時被ダメージアップ": ("STATUS", "STATUS_STUN"),
    "気絶時間延長": ("STATUS", "STATUS_STUN"),
    "気絶体力吸収": ("STATUS", "STATUS_STUN"),
    
    # 状態異常 - その他状態異常
    "呪縛": ("STATUS", "STATUS_OTHER_INFLICT"),
    "小人化": ("STATUS", "STATUS_OTHER_INFLICT"),
    "攻撃不可": ("STATUS", "STATUS_OTHER_INFLICT"),
    "衰弱": ("STATUS", "STATUS_OTHER_INFLICT"),
    "足止め": ("STATUS", "STATUS_OTHER_INFLICT"),
}

# --- Gemini APIのセットアップ ---
genai.configure(api_key=GEMINI_API_KEY)
generation_config = {
    "temperature": 0.0,
    "response_mime_type": "application/json",
}
safety_settings = [
    {"category": c, "threshold": "BLOCK_NONE"}
    for c in [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
]


# --- DB接続 ---
def get_db_connection():
    """DB接続を取得（長時間実行用にキープアライブ設定）"""
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    try:
        conn = psycopg2.connect(
            DATABASE_URL,
            sslmode='prefer',
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        return conn
    except Exception as e:
        print(f"データベース接続に失敗しました: {e}")
        return None


# --- ファイル読み込み ---
def load_prompt_template(path: Path) -> Optional[str]:
    """プロンプトテンプレートファイルを読み込む"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        print(f"エラー: プロンプトファイルが見つかりません: {path}")
        return None


# --- 効果名一覧取得 ---
def fetch_correct_effect_names(conn) -> List[Dict]:
    """correct_effect_names テーブルから効果名の一覧を取得"""
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            query = f"""
            SELECT 
                correct_name,
                effect_type,
                category
            FROM {CORRECT_NAMES_TABLE}
            ORDER BY effect_type, category, correct_name
            """
            
            cur.execute(query)
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    except Exception as e:
        print(f"効果名一覧の取得中にエラー: {e}")
        return []


# --- 効果名の自動補完 ---
def auto_complete_classification(effect_name: str, conn) -> Tuple[Optional[str], Optional[str]]:
    """
    効果名から effect_type と category を自動補完
    
    Returns:
        (effect_type, category) or (None, None) if not found
    """
    return PERSONALITY_EFFECT_CATEGORIES.get(effect_name, (None, None))


# --- キャラクター単位で個性テキスト取得（1キャラ3個性ずつ） ---
def fetch_characters_with_skills_from_db(conn, limit: Optional[int] = None, offset: int = 0, unanalyzed_only: bool = False) -> List[Dict]:
    """
    1キャラ（3個性）ずつ取得
    
    Args:
        unanalyzed_only: Trueの場合、未解析の個性テキストを持つキャラのみを取得
    
    Returns:
        List of dicts with keys: id, skill_text1, skill_text2, skill_text3
    """
    if unanalyzed_only:
        # 未解析の個性テキストを持つキャラのみを取得
        query = """
        WITH analyzed_texts AS (
            SELECT DISTINCT skill_text FROM {verified_table}
        )
        SELECT a.id, a.skill_text1, a.skill_text2, a.skill_text3
        FROM {alien_table} a
        WHERE (
            (a.skill_text1 IS NOT NULL AND a.skill_text1 != 'なし' AND a.skill_text1 NOT IN (SELECT skill_text FROM analyzed_texts))
            OR (a.skill_text2 IS NOT NULL AND a.skill_text2 != 'なし' AND a.skill_text2 NOT IN (SELECT skill_text FROM analyzed_texts))
            OR (a.skill_text3 IS NOT NULL AND a.skill_text3 != 'なし' AND a.skill_text3 NOT IN (SELECT skill_text FROM analyzed_texts))
        )
        ORDER BY a.id
        """.format(alien_table=SOURCE_TABLE_ALIEN, verified_table=DEST_TABLE)
    else:
        # 全てのキャラを取得
        query = """
        SELECT id, skill_text1, skill_text2, skill_text3
        FROM {alien_table}
        WHERE skill_text1 IS NOT NULL AND skill_text1 != 'なし'
           OR skill_text2 IS NOT NULL AND skill_text2 != 'なし'
           OR skill_text3 IS NOT NULL AND skill_text3 != 'なし'
        ORDER BY id
        """.format(alien_table=SOURCE_TABLE_ALIEN)
    
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset > 0:
        query += " OFFSET %s"
        params.append(offset)
    
    characters = []
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                characters.append({
                    'id': row['id'],
                    'skill_text1': row['skill_text1'],
                    'skill_text2': row['skill_text2'],
                    'skill_text3': row['skill_text3']
                })
    except Exception as e:
        print(f"キャラクターデータ取得エラー: {e}")
    return characters


# --- 個性テキスト取得（後方互換性のため残す） ---
def fetch_skill_texts_from_db(conn, limit: Optional[int] = None, offset: int = 0, unanalyzed_only: bool = False, regular_skills_only: bool = False) -> List[str]:
    """
    個性テキスト（ユニーク）を取得
    
    Args:
        unanalyzed_only: Trueの場合、skill_text_verified_effectsに存在しない個性テキストのみを取得
        regular_skills_only: Trueの場合、個性テキスト(skill_text1-3)のみを取得し、特技(S_Skill_text)を除外
    
    Returns:
        List of skill_text strings
    """
    if unanalyzed_only:
        # 未解析の個性テキストのみを取得
        if regular_skills_only:
            # 個性のみ（特技除外）
            query = """
            WITH unique_texts AS (
                SELECT DISTINCT skill_text
                FROM (
                    SELECT skill_text1 as skill_text FROM {alien_table} WHERE skill_text1 IS NOT NULL AND skill_text1 != 'なし'
                    UNION 
                    SELECT skill_text2 FROM {alien_table} WHERE skill_text2 IS NOT NULL AND skill_text2 != 'なし'
                    UNION 
                    SELECT skill_text3 FROM {alien_table} WHERE skill_text3 IS NOT NULL AND skill_text3 != 'なし'
                ) all_texts
            ),
            analyzed_texts AS (
                SELECT DISTINCT skill_text FROM {verified_table}
            )
            SELECT ut.skill_text
            FROM unique_texts ut
            LEFT JOIN analyzed_texts at ON ut.skill_text = at.skill_text
            WHERE at.skill_text IS NULL
            ORDER BY ut.skill_text
            """.format(alien_table=SOURCE_TABLE_ALIEN, verified_table=DEST_TABLE)
        else:
            # 個性+特技
            query = """
            WITH unique_texts AS (
                SELECT DISTINCT skill_text
                FROM (
                    SELECT skill_text1 as skill_text FROM {alien_table} WHERE skill_text1 IS NOT NULL AND skill_text1 != 'なし'
                    UNION 
                    SELECT skill_text2 FROM {alien_table} WHERE skill_text2 IS NOT NULL AND skill_text2 != 'なし'
                    UNION 
                    SELECT skill_text3 FROM {alien_table} WHERE skill_text3 IS NOT NULL AND skill_text3 != 'なし'
                    UNION 
                    SELECT "S_Skill_text" as skill_text FROM {alien_table} WHERE "S_Skill_text" IS NOT NULL AND "S_Skill_text" != 'なし'
                ) all_texts
            ),
            analyzed_texts AS (
                SELECT DISTINCT skill_text FROM {verified_table}
            )
            SELECT ut.skill_text
            FROM unique_texts ut
            LEFT JOIN analyzed_texts at ON ut.skill_text = at.skill_text
            WHERE at.skill_text IS NULL
            ORDER BY ut.skill_text
            """.format(alien_table=SOURCE_TABLE_ALIEN, verified_table=DEST_TABLE)
    else:
        # 全ての個性テキストを取得
        if regular_skills_only:
            # 個性のみ（特技除外）
            query = """
            WITH unique_texts AS (
                SELECT DISTINCT skill_text
                FROM (
                    SELECT skill_text1 as skill_text FROM {alien_table} WHERE skill_text1 IS NOT NULL AND skill_text1 != 'なし'
                    UNION 
                    SELECT skill_text2 FROM {alien_table} WHERE skill_text2 IS NOT NULL AND skill_text2 != 'なし'
                    UNION 
                    SELECT skill_text3 FROM {alien_table} WHERE skill_text3 IS NOT NULL AND skill_text3 != 'なし'
                ) all_texts
            )
            SELECT ut.skill_text
            FROM unique_texts ut
            ORDER BY ut.skill_text
            """.format(alien_table=SOURCE_TABLE_ALIEN)
        else:
            # 個性+特技
            query = """
            WITH unique_texts AS (
                SELECT DISTINCT skill_text
                FROM (
                    SELECT skill_text1 as skill_text FROM {alien_table} WHERE skill_text1 IS NOT NULL AND skill_text1 != 'なし'
                    UNION 
                    SELECT skill_text2 FROM {alien_table} WHERE skill_text2 IS NOT NULL AND skill_text2 != 'なし'
                    UNION 
                    SELECT skill_text3 FROM {alien_table} WHERE skill_text3 IS NOT NULL AND skill_text3 != 'なし'
                    UNION 
                    SELECT "S_Skill_text" as skill_text FROM {alien_table} WHERE "S_Skill_text" IS NOT NULL AND "S_Skill_text" != 'なし'
                ) all_texts
            )
            SELECT ut.skill_text
            FROM unique_texts ut
            ORDER BY ut.skill_text
            """.format(alien_table=SOURCE_TABLE_ALIEN)
    
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset > 0:
        query += " OFFSET %s"
        params.append(offset)

    skill_texts = []
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                skill_texts.append(row['skill_text'])
    except Exception as e:
        print(f"個性テキストデータ取得エラー: {e}")
    return skill_texts


# --- 特技テキスト取得 ---
def fetch_s_skill_texts_from_db(conn, limit: Optional[int] = None, offset: int = 0, unanalyzed_only: bool = False) -> List[str]:
    """
    特技テキスト（ユニーク）を取得
    
    Args:
        unanalyzed_only: Trueの場合、skill_text_verified_effectsに存在しない特技テキストのみを取得
    
    Returns:
        List of skill_text strings
    """
    if unanalyzed_only:
        # 未解析の特技テキストのみを取得
        query = """
        WITH unique_texts AS (
            SELECT DISTINCT "S_Skill_text" as skill_text
            FROM {alien_table}
            WHERE "S_Skill_text" IS NOT NULL AND "S_Skill_text" != 'なし'
        ),
        analyzed_texts AS (
            SELECT DISTINCT skill_text FROM {verified_table}
        )
        SELECT ut.skill_text
        FROM unique_texts ut
        LEFT JOIN analyzed_texts at ON ut.skill_text = at.skill_text
        WHERE at.skill_text IS NULL
        ORDER BY ut.skill_text
        """.format(alien_table=SOURCE_TABLE_ALIEN, verified_table=DEST_TABLE)
    else:
        # 全ての特技テキストを取得
        query = """
        SELECT DISTINCT "S_Skill_text" as skill_text
        FROM {alien_table}
        WHERE "S_Skill_text" IS NOT NULL AND "S_Skill_text" != 'なし'
        ORDER BY skill_text
        """.format(alien_table=SOURCE_TABLE_ALIEN)
    
    params = []
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    if offset > 0:
        query += " OFFSET %s"
        params.append(offset)
    
    skill_texts = []
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                skill_texts.append(row['skill_text'])
    except Exception as e:
        print(f"特技テキストデータ取得エラー: {e}")
    return skill_texts


# --- LLM API呼び出し ---
def call_gemini_sync(model: genai.GenerativeModel, prompt: str, count_tokens: bool = False) -> Tuple[Optional[str], Optional[int], Optional[float]]:
    """
    Gemini APIを同期で呼び出す
    
    Returns:
        (response_text, token_count, retry_after_seconds)
        retry_after_seconds: 429エラーの場合、再試行可能になるまでの秒数（Noneの場合は即時リトライ可能）
    """
    try:
        response = model.generate_content(prompt)
        if not response or not hasattr(response, "text") or not response.text:
            print("\n    警告: Gemini APIからの応答が空です。レート制限の可能性があります。")
            return None, None, None
        
        token_count = None
        if count_tokens:
            # 入力トークン数を取得
            try:
                usage_metadata = response.usage_metadata
                if usage_metadata:
                    token_count = usage_metadata.prompt_token_count
            except Exception:
                pass
        
        return response.text, token_count, None
    except Exception as e:
        # 429エラーの場合はretry_delayを取得
        retry_after = None
        error_str = str(e)
        error_class = e.__class__.__name__
        
        if "429" in error_str or "ResourceExhausted" in error_class:
            # エラーメッセージからretry_delayを抽出
            # "Please retry in X.XXXXXs" の形式を探す
            match = re.search(r'Please retry in ([\d.]+)s', error_str)
            if match:
                retry_after = float(match.group(1))
                print(f"\n    レート制限エラー: {retry_after:.1f}秒後にリトライ可能")
            else:
                # デフォルトで60秒待機
                retry_after = 60.0
                print(f"\n    レート制限エラー: 60秒後にリトライ可能（retry_delayの抽出に失敗）")
        else:
            print(f"\n    Gemini API呼び出しエラー: {error_class}: {e}")
        
        return None, None, retry_after


# --- 応答パース ---
def parse_stage1_response(raw_text: Optional[str]) -> Optional[List[Dict]]:
    """1段階目の応答パース（effect_name, has_requirement, requirement_details, requirement_count, target, condition_target）"""
    if raw_text is None or not raw_text.strip():
        return None

    cleaned = raw_text.strip()
    # ```json ... ``` の除去
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # 配列で始まらない場合はエラー
    if not cleaned.startswith("["):
        return None

    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            return None
        return parsed
    except json.JSONDecodeError:
        return None


# --- 効果名辞書の整形 ---
def format_effect_names_for_prompt() -> str:
    """
    効果名一覧を分類別にグループ化してプロンプト用に整形
    
    固定の効果名リストに表記ゆれや具体例を含めた詳細な辞書を作成
    """
    # 固定の効果名リスト（説明付き）
    effect_dict = {
        # BUFF - アップ系
        "いどうアップ": "",
        "おおきさアップ": "",
        "たいりょくアップ": "",
        "やるきアップ": "",
        "つよさアップ": "★攻撃力アップとは別の効果です",
        "攻撃力アップ": "★つよさアップとは別の効果です。「味方全員の攻撃力を〇〇アップ」など",
        "クリティカルダメージアップ": "",
        "クリティカル率アップ": "",
        "たいりょく回復量アップ": "「回復量を〇%アップ」など、回復量を増やす効果",
        "回避率アップ": "「攻撃を回避」「〇回まで〇%で攻撃を回避」など",
        "攻撃回数アップ": "「攻撃回数を〇回アップ」「攻撃回数が○回増える」「攻撃回数が○回ふえる」「れんぞく攻撃回数○回アップ」など",
        "与ダメージアップ": "表記ゆれ: 与ダメアップ",
        "通常攻撃与ダメージアップ": "表記ゆれ: 通常攻撃与ダメアップ",
        "特技与ダメージアップ": "表記ゆれ: 特技与ダメアップ",
        "特技頻度アップ": "「特技が出やすくなる」など",
        
        # BUFF - 軽減系
        "吹き飛ばし軽減": "",
        "毒ダメージ軽減": "",
        "毒効果時間短縮": "",
        "気絶時間短縮": "「気絶になった際の効果時間が短くなる」など、気絶時間を短縮する効果",
        "特技被ダメージ軽減": "",
        "被クリティカルダメージ軽減": "",
        "被ダメージ軽減": "表記ゆれ: 被ダメ軽減、ダメージ軽減",
        "通常攻撃被ダメージ軽減": "表記ゆれ: 通常攻撃被ダメ軽減",
        
        # BUFF - 抵抗系
        "いどうダウン抵抗": "",
        "おおきさダウン抵抗": "",
        "クリティカル率ダウン抵抗": "",
        "つよさダウン抵抗": "",
        "やるきダウン抵抗": "",
        "与ダメージダウン抵抗": "",
        "呪縛抵抗": "",
        "回復量ダウン抵抗": "",
        "回避率ダウン抵抗": "",
        "急速ダメージ抵抗": "",
        "持続ダメージ抵抗": "",
        "攻撃不可抵抗": "",
        "攻撃力ダウン抵抗": "",
        "毒抵抗": "",
        "気絶抵抗": "",
        "特技頻度ダウン抵抗": "",
        "被ダメージアップ抵抗": "",
        "足止め抵抗": "",
        
        # BUFF - その他バフ
        "たいりょく吸収": "「与えたダメージの〇%分をたいりょく吸収する」など",
        "たいりょく回復": "",
        "ダメージ反射": "",
        "のけぞりアタック": "",
        "のけぞりガード": "",
        "ひきよせ": "「〇〇属性の的が自分を狙うようになるぞ！」「〇〇属性をほいほいするぞ！」のように引き寄せる系全般",
        "凶暴化": "",
        "吹き飛ばし": "",
        "回避無視": "「回避率アップ状態の敵に攻撃が当たるようになる」「回避を無視」など（自分に付与されるバフ、target: 自分）",
        "奮迅": "複合効果：やるきアップ、攻撃回数アップ、被ダメージ軽減の複合状態",
        "属性狙い": "「〇〇属性の敵を狙うようになる」など、特定の属性に攻撃を集中させる効果（ひきよせではない）",
        "復活": "「たいりょくが0になったときたいりょくを◯%回復」など（自分を回復）",
        "心眼": "",
        "急速回復": "「急速回復する」など、時間をかけて回復する効果",
        "自然回復": "「徐々に回復する」「一定時間毎に回復する」「自動回復する」など、時間経過で回復する効果",
        "有終の美": "たいりょくが0になったときに味方を回復する（自分以外の味方を回復）",
        "無双": "",
        "自爆": "たいりょくが0になったときに近くの敵にダメージを与える",
        "足止め解除": "",
        "追加ダメージ": "",
        "連撃": "「連撃する」など、自動的に複数回攻撃する特殊効果（攻撃回数アップではない）",
        "隠密": "「敵から狙われにくくなる」など",
        
        # DEBUFF - ダウン系
        "いどうダウン": "",
        "おおきさダウン": "",
        "クリティカル率ダウン": "",
        "回復量ダウン": "",
        "つよさダウン": "",
        "やるきダウン": "",
        "与ダメージダウン": "",
        "反射ダメージダウン": "",
        "攻撃力ダウン": "",
        "特技与ダメージダウン": "",
        "特技頻度ダウン": "",
        
        # DEBUFF - その他デバフ
        "クリティカルダメージアップ無効": "",
        "たいりょく回復量アップ無効": "",
        "凶暴化無効": "",
        "反射無効": "",
        "回避率アップ無効": "敵の回避率アップを無効にするデバフ（回避無視とは別）",
        "急速ダメージ": "",
        "特技頻度アップ無効": "",
        "自動回復解除": "",
        "被ダメージアップ": "",
        "被ダメージ軽減無効": "敵の被ダメージ軽減を無効にするデバフ",
        "被ダメージ軽減解除": "敵の被ダメージ軽減状態を解除するデバフ",
        
        # STATUS - 毒系
        "毒アタック": "「毒状態にしやすい」など",
        "毒キラー": "毒状態の的に対して与ダメージアップ",
        "毒ダメージアップ": "",
        "毒効果時間延長": "",
        
        # STATUS - 気絶系
        "気絶アタック": "「気絶させやすくなる」など",
        "気絶キラー": "気絶状態の的に対して与ダメージアップ",
        "気絶時被ダメージアップ": "「気絶状態の敵の被ダメージをアップ」",
        "気絶時間延長": "",
        "気絶体力吸収": "「気絶状態の的に攻撃したとき与えたダメージの〇%分を体力吸収するぞ」など",
        
        # STATUS - その他状態異常
        "呪縛": "",
        "小人化": "",
        "攻撃不可": "",
        "衰弱": "",
        "足止め": "",
    }
    
    # カテゴリでグループ化
    grouped = defaultdict(lambda: defaultdict(list))
    for name, description in effect_dict.items():
        if name in PERSONALITY_EFFECT_CATEGORIES:
            effect_type, category = PERSONALITY_EFFECT_CATEGORIES[name]
            grouped[effect_type][category].append((name, description))
    
    formatted_lines = []
    
    # BUFF系
    buff_order = ["BUFF_BOOST", "BUFF_REDUCE", "BUFF_RESIST", "BUFF_OTHER"]
    formatted_lines.append("### バフ系効果 (BUFF)\n")
    for category in buff_order:
        if category in grouped['BUFF']:
            category_jp = {"BUFF_BOOST": "アップ系", "BUFF_REDUCE": "軽減系", "BUFF_RESIST": "抵抗系", "BUFF_OTHER": "その他バフ"}[category]
            formatted_lines.append(f"#### {category_jp}\n")
            for name, description in grouped['BUFF'][category]:
                formatted_lines.append(f"- **{name}**")
                if description:
                    formatted_lines.append(f"  {description}")
            formatted_lines.append("")
    
    # DEBUFF系
    debuff_order = ["DEBUFF_REDUCE", "DEBUFF_OTHER"]
    formatted_lines.append("### デバフ系効果 (DEBUFF)\n")
    for category in debuff_order:
        if category in grouped['DEBUFF']:
            category_jp = {"DEBUFF_REDUCE": "ダウン系", "DEBUFF_OTHER": "その他デバフ"}[category]
            formatted_lines.append(f"#### {category_jp}\n")
            for name, description in grouped['DEBUFF'][category]:
                formatted_lines.append(f"- **{name}**")
                if description:
                    formatted_lines.append(f"  {description}")
            formatted_lines.append("")
    
    # STATUS系
    status_order = ["STATUS_POISON", "STATUS_STUN", "STATUS_OTHER_INFLICT"]
    formatted_lines.append("### 状態異常系効果 (STATUS)\n")
    for category in status_order:
        if category in grouped['STATUS']:
            category_jp = {"STATUS_POISON": "毒系", "STATUS_STUN": "気絶系", "STATUS_OTHER_INFLICT": "その他状態異常"}[category]
            formatted_lines.append(f"#### {category_jp}\n")
            for name, description in grouped['STATUS'][category]:
                formatted_lines.append(f"- **{name}**")
                if description:
                    formatted_lines.append(f"  {description}")
            formatted_lines.append("")
    
    formatted_lines.append("**重要**: 新しい効果名を作らないでください。必ず上記のリストから選択してください。\n")
    
    return "\n".join(formatted_lines)


# --- 特技用効果名辞書の整形 ---
def format_s_skill_effect_names_for_prompt() -> str:
    """特技用効果名一覧をJSONから読み込んでプロンプト用に整形"""
    try:
        with open(S_SKILL_EFFECT_DICT_PATH, 'r', encoding='utf-8') as f:
            effect_dict = json.load(f)
        
        # カテゴリ別にグループ化
        grouped = defaultdict(lambda: defaultdict(list))
        for name, info in effect_dict.items():
            effect_type = info.get('effect_type', '')
            category = info.get('category', '')
            description = info.get('description', '')
            grouped[effect_type][category].append((name, description))
        
        formatted_lines = []
        
        # BUFF系
        if 'BUFF' in grouped:
            formatted_lines.append("### バフ系効果 (BUFF)\n")
            for category in ['S_SKILL_HEAL', 'S_SKILL_BUFF']:
                if category in grouped['BUFF']:
                    category_jp = {'S_SKILL_HEAL': '回復系', 'S_SKILL_BUFF': 'バフ系'}[category]
                    formatted_lines.append(f"#### {category_jp}\n")
                    for name, description in grouped['BUFF'][category]:
                        formatted_lines.append(f"- **{name}**")
                        if description:
                            formatted_lines.append(f"  {description}")
                    formatted_lines.append("")
        
        # DEBUFF系
        if 'DEBUFF' in grouped:
            formatted_lines.append("### デバフ(状態異常)系効果 (DEBUFF)\n")
            if 'S_SKILL_DEBUFF' in grouped['DEBUFF']:
                formatted_lines.append("#### デバフ(状態異常)系\n")
                for name, description in grouped['DEBUFF']['S_SKILL_DEBUFF']:
                    formatted_lines.append(f"- **{name}**")
                    if description:
                        formatted_lines.append(f"  {description}")
                formatted_lines.append("")
        
        formatted_lines.append("**重要**: 新しい効果名を作らないでください。必ず上記のリストから選択してください。\n")
        
        return "\n".join(formatted_lines)
    except Exception as e:
        print(f"特技用効果名辞書の読み込みエラー: {e}")
        return ""


# --- 特技用効果分類の自動補完 ---
def auto_complete_s_skill_classification(effect_name: str, conn) -> Tuple[Optional[str], Optional[str]]:
    """特技用効果名からeffect_typeとcategoryを自動補完"""
    try:
        with open(S_SKILL_EFFECT_DICT_PATH, 'r', encoding='utf-8') as f:
            effect_dict = json.load(f)
        
        if effect_name in effect_dict:
            info = effect_dict[effect_name]
            return info.get('effect_type'), info.get('category')
    except Exception as e:
        print(f"特技用効果名辞書読み込みエラー: {e}")
    
    return None, None


# --- プロンプト構築 ---
def build_prompt_for_skill(skill_text: str, is_special_skill: bool = False) -> Optional[str]:
    """個性または特技テキストから1段階目プロンプトを構築"""
    if not skill_text or skill_text == "なし":
        return None

    if is_special_skill:
        # 特技用プロンプト
        prompt_template = load_prompt_template(S_SKILL_PROMPT_PATH)
        if not prompt_template:
            raise RuntimeError("特技用プロンプトファイルの読み込みに失敗しました。")
        
        effect_names_text = format_s_skill_effect_names_for_prompt()
        prompt = prompt_template.replace("{effect_names_list}", effect_names_text)
        prompt = prompt.replace("{s_skill_text}", skill_text)
    else:
        # 個性用プロンプト
        prompt_template = load_prompt_template(PROMPT_PATH)
        if not prompt_template:
            raise RuntimeError("プロンプトファイルの読み込みに失敗しました。")

        effect_names_text = format_effect_names_for_prompt()
        prompt = prompt_template.replace("{effect_names_list}", effect_names_text)
        prompt = prompt.replace("{skill_text}", skill_text)
    
    return prompt


# --- データ準備 ---
def prepare_stage1_effects_for_db(effects_list: List[Dict], skill_text: str, conn, is_special_skill: bool = False) -> List[Tuple]:
    """
    1段階目用のDB挿入データ準備（自動補完含む）
    target, condition_target をLLMレスポンスから取得
    """
    rows = []
    seen_keys = set()  # 重複チェック用

    if not isinstance(effects_list, list):
        return rows

    for effect_data in effects_list:
        effect_name = effect_data.get('name')
        if not effect_name:
            continue

        # 自動補完: effect_name → effect_type, category
        if is_special_skill:
            effect_type, category = auto_complete_s_skill_classification(effect_name, conn)
        else:
            effect_type, category = auto_complete_classification(effect_name, conn)
        
        if effect_type is None or category is None:
            print(f"    警告: 効果名 '{effect_name}' が辞書に見つかりません。スキップします。")
            continue

        # 数値変換
        requirement_count = int_or_none(effect_data.get("requirement_count"))

        # has_requirement (bool)
        has_requirement = bool(effect_data.get('has_requirement', False))

        # target, condition_target を取得
        target = effect_data.get('target')
        if target == "":
            target = None
        condition_target = effect_data.get('condition_target')
        if condition_target == "":
            condition_target = None

        # UNIQUE制約のキー（skill_text, effect_name, condition_target）
        unique_key = (skill_text, effect_name, condition_target)
        
        # 重複チェック
        if unique_key in seen_keys:
            print(f"    警告: 重複データをスキップ - {effect_name}")
            continue
        seen_keys.add(unique_key)

        row = (
            skill_text,                          # skill_text
            effect_name,                         # effect_name
            effect_type,                         # effect_type (自動補完)
            category,                            # category (自動補完)
            condition_target,                    # condition_target (LLMから取得)
            None,                                # requires_awakening (NULL)
            target,                              # target (LLMから取得)
            has_requirement,                     # has_requirement
            effect_data.get('requirement_details'),  # requirement_details
            requirement_count,                   # requirement_count
        )
        rows.append(row)
    
    return rows


# --- 補助関数 ---
def int_or_none(value: Any) -> Optional[int]:
    """int変換、失敗時はNone"""
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def display_and_check_llm_output(skill_text: str, effects_list: List[Dict]) -> List[Dict]:
    """
    LLMの出力を表示してAI（Cursor）が2重チェックする
    AgentモードでCursorがターミナル出力を確認して必要に応じてスクリプトを修正する
    
    Args:
        skill_text: 個性テキスト
        effects_list: LLMが出力した効果リスト
        
    Returns:
        修正後の効果リスト（修正されなかった場合はそのまま）
    """
    import json
    
    print("\n" + "="*80)
    print(f"個性テキスト: {skill_text}")
    print("-"*80)
    print("LLM解析結果:")
    
    if not effects_list:
        print("  （効果なし）")
        print("\n" + "="*80)
        print("JSON出力:")
        print("[]")
        print("="*80 + "\n")
        return effects_list
    
    for i, effect in enumerate(effects_list, 1):
        print(f"  {i}. {effect.get('name', 'N/A')}")
        if effect.get('has_requirement'):
            req_details = effect.get('requirement_details', '')
            req_count = effect.get('requirement_count', 1)
            print(f"     編成条件: {req_details} が{req_count}体以上")
        print(f"     target: {effect.get('target', 'N/A')}")
        if effect.get('condition_target'):
            print(f"     condition_target: {effect.get('condition_target', 'N/A')}")
    
    print("\n" + "="*80)
    print("JSON出力:")
    print(json.dumps(effects_list, ensure_ascii=False, indent=2))
    print("="*80 + "\n")
    
    return effects_list


# --- 並列処理 ---
async def process_skill_texts_parallel(
    skill_texts: List[str],
    model: genai.GenerativeModel,
    interval: float,
    conn,
    count_tokens: bool = False,
    is_special_skill: bool = False
) -> Tuple[List[Tuple], List[str], Optional[int]]:
    """
    個性テキスト単位でリクエストを一定間隔で送信し、回答を並行して待つ
    
    Returns:
        (all_rows, failed_skills, total_tokens)
    """
    total_skills = len(skill_texts)
    tasks = []
    failed_skills_initial = []
    total_tokens = None

    # --- リクエスト送信ループ ---
    print(f"\nLLMによる解析を開始します（{interval:.1f}秒間隔）")
    loop = asyncio.get_event_loop()
    task_start_time = time.monotonic()

    for index, skill_text in enumerate(skill_texts):
        # 待機時間計算
        now = time.monotonic()
        next_start_offset = index * interval
        wait_time = max(0, (task_start_time + next_start_offset) - now)
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        # プロンプト作成
        prompt = build_prompt_for_skill(skill_text, is_special_skill=is_special_skill)
        if not prompt:
            continue

        # 同期API呼び出しを非同期タスクとしてスケジュール
        print(f"[{index + 1}/{total_skills}] 個性テキスト「{skill_text[:30]}...」を送信...")
        task = loop.run_in_executor(None, call_gemini_sync, model, prompt, count_tokens)
        tasks.append((skill_text, task))

    print(f"\n全 {len(tasks)} 件のリクエスト送信完了。回答を待機中...\n")

    # --- 回答収集ループ ---
    all_rows = []
    pbar = tqdm(total=len(tasks), desc="回答収集中", unit="個性")
    retry_skills_with_delay = []  # 429エラーで待機が必要な個性テキスト
    
    for skill_text, task in tasks:
        try:
            result = await task
            raw_response = result[0]
            token_count = result[1]
            retry_after = result[2] if len(result) > 2 else None
            
            # 429エラーの場合、リトライリストに追加
            if retry_after is not None and retry_after > 0:
                retry_skills_with_delay.append((skill_text, retry_after))
                failed_skills_initial.append(skill_text)
                continue
            
            if token_count is not None:
                if total_tokens is None:
                    total_tokens = 0
                total_tokens += token_count
            
            effects_list = parse_stage1_response(raw_response)

            if effects_list is not None:
                rows = prepare_stage1_effects_for_db(effects_list, skill_text, conn, is_special_skill=is_special_skill)
                all_rows.extend(rows)
            else:
                failed_skills_initial.append(skill_text)
        except Exception as e:
            print(f"  個性テキスト「{skill_text[:30]}...」: 予期せぬエラー ({e})")
            failed_skills_initial.append(skill_text)
        finally:
            pbar.update(1)
    pbar.close()
    
    # 429エラーで待機が必要な個性テキストがあれば、適切な間隔でリトライ
    if retry_skills_with_delay:
        max_retry_after = max(delay for _, delay in retry_skills_with_delay)
        print(f"\nレート制限により {len(retry_skills_with_delay)} 件のリクエストが待機中...")
        print(f"最大待機時間: {max_retry_after:.1f}秒後からリトライを開始します。")
        
        # 待機時間が長い場合は、間隔を調整してリトライ
        await asyncio.sleep(max_retry_after + interval)
        
        retry_skill_texts = [skill for skill, _ in retry_skills_with_delay]
        if retry_skill_texts:
            print(f"リトライを開始します（{len(retry_skill_texts)} 件）...")
            retry_tasks = []
            retry_start_time = time.monotonic()
            
            for idx, retry_skill in enumerate(retry_skill_texts):
                # 適切な間隔でリトライ
                now = time.monotonic()
                next_start_offset = idx * interval
                wait_time = max(0, (retry_start_time + next_start_offset) - now)
                
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                
                prompt = build_prompt_for_skill(retry_skill, is_special_skill=is_special_skill)
                if prompt:
                    task = loop.run_in_executor(None, call_gemini_sync, model, prompt, count_tokens)
                    retry_tasks.append((retry_skill, task))
            
            # リトライ結果を収集
            for retry_skill, retry_task in retry_tasks:
                try:
                    retry_result = await retry_task
                    retry_response = retry_result[0]
                    retry_token_count = retry_result[1]
                    
                    if retry_token_count is not None:
                        if total_tokens is None:
                            total_tokens = 0
                        total_tokens += retry_token_count
                    
                    retry_effects_list = parse_stage1_response(retry_response)
                    
                    if retry_effects_list is not None:
                        retry_rows = prepare_stage1_effects_for_db(retry_effects_list, retry_skill, conn, is_special_skill=is_special_skill)
                        all_rows.extend(retry_rows)
                        # 成功したのでfailed_skills_initialから削除
                        if retry_skill in failed_skills_initial:
                            failed_skills_initial.remove(retry_skill)
                    else:
                        if retry_skill not in failed_skills_initial:
                            failed_skills_initial.append(retry_skill)
                except Exception as e:
                    print(f"  リトライ失敗「{retry_skill[:30]}...」: {e}")
                    if retry_skill not in failed_skills_initial:
                        failed_skills_initial.append(retry_skill)

    return all_rows, failed_skills_initial, total_tokens


# --- 逐次処理（2重チェック付き） ---
def process_skill_texts_sequential_with_check(
    skill_texts: List[str],
    model: genai.GenerativeModel,
    interval: float,
    conn,
    count_tokens: bool = False,
    is_special_skill: bool = False
) -> Tuple[List[Tuple], List[str], Optional[int]]:
    """
    個性テキストを1件ずつ処理し、AIによる2重チェックを行う
    
    Returns:
        (all_rows, failed_skills, total_tokens)
    """
    total_skills = len(skill_texts)
    all_rows = []
    failed_skills_initial = []
    total_tokens = 0
    
    print(f"\n逐次処理で解析を開始します（{interval:.1f}秒間隔、2重チェック有効）")
    
    for index, skill_text in enumerate(skill_texts):
        print(f"\n[{index + 1}/{total_skills}] 処理中...")
        
        # プロンプト作成
        prompt = build_prompt_for_skill(skill_text, is_special_skill=is_special_skill)
        if not prompt:
            print("  プロンプト作成に失敗しました")
            failed_skills_initial.append(skill_text)
            continue
        
        # API呼び出し
        try:
            result = call_gemini_sync(model, prompt, count_tokens)
            raw_response = result[0]
            token_count = result[1]
            retry_after = result[2] if len(result) > 2 else None
            
            # 429エラー
            if retry_after is not None and retry_after > 0:
                print(f"  レート制限: {retry_after:.1f}秒待機...")
                time.sleep(retry_after + interval)
                # リトライ
                result = call_gemini_sync(model, prompt, count_tokens)
                raw_response = result[0]
                token_count = result[1]
                
            if token_count is not None:
                total_tokens += token_count
            
            # レスポンスパース
            effects_list = parse_stage1_response(raw_response)
            
            if effects_list is not None:
                # AIによる2重チェック
                checked_effects_list = display_and_check_llm_output(skill_text, effects_list)
                
                # DB挿入用データ準備
                rows = prepare_stage1_effects_for_db(checked_effects_list, skill_text, conn, is_special_skill=is_special_skill)
                all_rows.extend(rows)
            else:
                print("  LLMレスポンスのパースに失敗しました")
                failed_skills_initial.append(skill_text)
                
        except Exception as e:
            print(f"  エラー: {e}")
            failed_skills_initial.append(skill_text)
        
        # 間隔調整
        if index < total_skills - 1:
            time.sleep(interval)
    
    return all_rows, failed_skills_initial, total_tokens if total_tokens > 0 else None


# --- DB挿入 ---
def insert_effects(conn, rows: List[Tuple], truncate: bool = False):
    """skill_text_verified_effects テーブルにバッチ挿入"""
    if not rows:
        print("挿入対象の効果がありませんでした。")
        return

    try:
        with conn.cursor() as cur:
            if truncate:
                print(f"{DEST_TABLE} を TRUNCATE します...")
                cur.execute(f"TRUNCATE TABLE {DEST_TABLE} RESTART IDENTITY")

            # 既存データをチェックしてUPDATE/INSERTを分ける
            # UNIQUE INDEXが関数式のため、ON CONFLICTは使えない
            inserted_count = 0
            updated_count = 0
            
            for row in rows:
                skill_text, effect_name, effect_type, category, condition_target, \
                    requires_awakening, target, has_requirement, requirement_details, requirement_count = row[:10]
                
                # 既存データをチェック
                check_sql = f"""
                SELECT id FROM {DEST_TABLE}
                WHERE skill_text = %s 
                  AND effect_name = %s 
                  AND COALESCE(condition_target, '') = COALESCE(%s, '')
                """
                cur.execute(check_sql, (skill_text, effect_name, condition_target))
                existing = cur.fetchone()
                
                if existing:
                    # UPDATE
                    update_sql = f"""
                    UPDATE {DEST_TABLE} SET
                        effect_type = %s,
                        category = %s,
                        requires_awakening = %s,
                        target = %s,
                        has_requirement = %s,
                        requirement_details = %s,
                        requirement_count = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """
                    cur.execute(update_sql, (
                        effect_type, category, requires_awakening, target,
                        has_requirement, requirement_details, requirement_count,
                        existing[0]
                    ))
                    updated_count += 1
                else:
                    # INSERT
                    insert_sql = f"""
                    INSERT INTO {DEST_TABLE} (
                        skill_text, effect_name, effect_type, category, condition_target,
                        requires_awakening, target, has_requirement, requirement_details, requirement_count,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """
                    cur.execute(insert_sql, (
                        skill_text, effect_name, effect_type, category, condition_target,
                        requires_awakening, target, has_requirement, requirement_details, requirement_count
                    ))
                    inserted_count += 1
            
            conn.commit()
            print(f"{DEST_TABLE} に {inserted_count} 件を挿入、{updated_count} 件を更新しました（合計 {len(rows)} 件）。")
    except Exception as e:
        print(f"DB挿入エラー: {e}")
        conn.rollback()
        raise


# --- バックアップ ---
def save_extraction_backup(rows: List[Tuple], timestamp: str) -> Path:
    """抽出結果をJSONLファイルとしてバックアップする"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = BACKUP_DIR / f"stage1_extraction_{timestamp}.jsonl"
    
    print(f"\n抽出結果をバックアップしています: {backup_path}")
    
    column_names = [
        'skill_text', 'effect_name', 'effect_type', 'category', 'condition_target',
        'requires_awakening', 'target', 'has_requirement', 'requirement_details', 'requirement_count'
    ]
    
    try:
        with open(backup_path, 'w', encoding='utf-8') as f:
            for row in rows:
                row_dict = dict(zip(column_names, row))
                json.dump(row_dict, f, ensure_ascii=False)
                f.write("\n")
        
        print(f"バックアップ完了: {len(rows)} 件を保存しました")
        return backup_path
    except Exception as e:
        print(f"バックアップ中にエラーが発生: {e}")
        return None


# --- 引数パース ---
def parse_args() -> argparse.Namespace:
    """コマンドライン引数のパース"""
    parser = argparse.ArgumentParser(description="1段階目: 効果名と要求のみを抽出")
    parser.add_argument("--limit", type=int, help="処理する個性テキスト数の上限")
    parser.add_argument("--offset", type=int, default=0, help="スキップする個性テキスト数")
    parser.add_argument("--truncate", action="store_true", help="テーブルをクリアしてから挿入")
    parser.add_argument("--dry-run", action="store_true", help="DBへ書き込まず結果を表示")
    parser.add_argument("--unanalyzed-only", action="store_true", help="未解析の個性テキストのみ処理")
    parser.add_argument("--regular-skills-only", action="store_true", help="個性テキストのみ処理（特技除外）")
    parser.add_argument("--special-skills-only", action="store_true", help="特技テキストのみ処理（個性除外）")
    parser.add_argument("--sequential-check", action="store_true", help="逐次処理でAIが2重チェック（時間はかかるが精度重視）")

    return parser.parse_args()


# --- メイン処理 ---
def main():
    """メイン処理"""
    args = parse_args()

    # APIキー取得
    # --unanalyzed-only の場合はテスト用APIキーを優先、なければ本番用APIキーを使用
    # それ以外は本番用APIキーを優先、なければテスト用APIキーを使用
    if args.unanalyzed_only:
        api_key = os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY_2")
        if not api_key:
            raise RuntimeError("APIキーが環境変数 GEMINI_API_KEY_1 または GEMINI_API_KEY_2 に設定されていません。")
    else:
        # 本番実行時は別のAPIキーを使用
        api_key = os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_API_KEY_1")
        if not api_key:
            raise RuntimeError("APIキーが環境変数 GEMINI_API_KEY_2 または GEMINI_API_KEY_1 に設定されていません。")
    genai.configure(api_key=api_key)

    # モデル設定
    print(f"使用モデル: {MODEL_NAME}")
    model = genai.GenerativeModel(MODEL_NAME)

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return

        # 個性または特技テキストを取得
        if args.special_skills_only:
            print("データベースから特技テキストを読み込んでいます...")
            skill_texts = fetch_s_skill_texts_from_db(
                conn,
                limit=args.limit,
                offset=args.offset,
                unanalyzed_only=args.unanalyzed_only
            )
            print(f"{len(skill_texts)} 件の特技テキストを読み込みました。")
            
            # 特技の場合は既存の処理を使用
            is_special = True
            if args.sequential_check:
                # 逐次処理で2重チェック
                all_rows, failed_skills_initial, total_tokens = process_skill_texts_sequential_with_check(
                    skill_texts, model, ACTUAL_INTERVAL, conn, count_tokens=True, is_special_skill=is_special
                )
                print(f"\n逐次解析完了: {len(all_rows)} 件の効果を抽出しました。")
                print(f"失敗: {len(failed_skills_initial)} 件")
                if total_tokens is not None:
                    print(f"総入力トークン数: {total_tokens} tokens")
                
                # 全ての成功データ
                final_all_rows = all_rows
                failed_skills_final = failed_skills_initial
            else:
                # 並列処理（既存の処理）
                all_rows, failed_skills_initial, total_tokens = asyncio.run(process_skill_texts_parallel(
                    skill_texts, model, ACTUAL_INTERVAL, conn, count_tokens=True, is_special_skill=is_special
                ))
                print(f"\n初回解析完了: {len(all_rows)} 件の効果を抽出しました。")
                print(f"初回失敗: {len(failed_skills_initial)} 件")
                if total_tokens is not None:
                    print(f"総入力トークン数: {total_tokens} tokens")

                # --- リトライ処理 ---
                retry_rows = []
                failed_skills_final = []
                if failed_skills_initial:
                    print(f"\n--- リトライ解析 ({len(failed_skills_initial)} 件) ---")
                    retry_rows, failed_skills_final, _ = asyncio.run(process_skill_texts_parallel(
                        failed_skills_initial, model, ACTUAL_INTERVAL, conn, is_special_skill=is_special
                    ))
                    print(f"リトライ完了: {len(retry_rows)} 件の効果を追加抽出。")
                    print(f"最終失敗: {len(failed_skills_final)} 件")
                    if failed_skills_final:
                        print("最終的に失敗した特技:", ", ".join([
                            f"「{s[:20]}...」" for s in failed_skills_final[:20]
                        ]))

                # 全ての成功データを結合
                final_all_rows = all_rows + retry_rows
                print(f"\n全解析完了: 合計 {len(final_all_rows)} 件の効果を抽出しました。")
        else:
            # 個性の場合は1キャラ（3個性）ずつ処理
            print("データベースからキャラクター（1キャラ3個性ずつ）を読み込んでいます...")
            characters = fetch_characters_with_skills_from_db(
                conn, 
                limit=args.limit, 
                offset=args.offset,
                unanalyzed_only=args.unanalyzed_only
            )
            print(f"{len(characters)} キャラクターを読み込みました。")
            if not characters:
                return
            
            # 1キャラずつ処理（3個性を順番に解析）
            final_all_rows = []
            failed_skills_final = []
            total_tokens = 0
            
            for char_idx, char in enumerate(characters, 1):
                print(f"\n[{char_idx}/{len(characters)}] キャラクターID {char['id']} を処理中...")
                
                # 3個性をリストに変換（Noneや'なし'を除外）
                skill_texts_for_char = []
                for i in range(1, 4):
                    skill_text = char.get(f'skill_text{i}')
                    if skill_text and skill_text != 'なし':
                        skill_texts_for_char.append(skill_text)
                
                if not skill_texts_for_char:
                    print(f"  キャラクターID {char['id']} には解析対象の個性がありません。スキップします。")
                    continue
                
                print(f"  個性数: {len(skill_texts_for_char)}件")
                
                # 個性テキストを順番に解析
                is_special = False
                if args.sequential_check:
                    # 逐次処理で2重チェック
                    char_rows, char_failed, char_tokens = process_skill_texts_sequential_with_check(
                        skill_texts_for_char, model, ACTUAL_INTERVAL, conn, count_tokens=True, is_special_skill=is_special
                    )
                    final_all_rows.extend(char_rows)
                    failed_skills_final.extend(char_failed)
                    if char_tokens is not None:
                        total_tokens += char_tokens
                else:
                    # 並列処理
                    char_rows, char_failed, char_tokens = asyncio.run(process_skill_texts_parallel(
                        skill_texts_for_char, model, ACTUAL_INTERVAL, conn, count_tokens=True, is_special_skill=is_special
                    ))
                    final_all_rows.extend(char_rows)
                    failed_skills_final.extend(char_failed)
                    if char_tokens is not None:
                        total_tokens += char_tokens
                    
                    # リトライ処理
                    if char_failed:
                        print(f"  リトライ: {len(char_failed)}件")
                        retry_rows, retry_failed, _ = asyncio.run(process_skill_texts_parallel(
                            char_failed, model, ACTUAL_INTERVAL, conn, is_special_skill=is_special
                        ))
                        final_all_rows.extend(retry_rows)
                        failed_skills_final = [s for s in failed_skills_final if s not in retry_failed]
                        failed_skills_final.extend(retry_failed)
            
            print(f"\n全解析完了: 合計 {len(final_all_rows)} 件の効果を抽出しました。")
            print(f"失敗: {len(failed_skills_final)} 件")
            if total_tokens > 0:
                print(f"総入力トークン数: {total_tokens} tokens")

        # ドライラン
        if args.dry_run:
            print("\n--- ドライラン: プレビュー（最大10件） ---")
            for i, row in enumerate(final_all_rows):
                if i >= 10:
                    break
                print(f"  skill_text={row[0][:30]}..., name='{row[1]}', type='{row[2]}', cat='{row[3]}', has_req={row[7]}")
            return

        # 抽出結果のバックアップを作成
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = save_extraction_backup(final_all_rows, timestamp)

        # DB接続を再確立（長時間の解析でタイムアウトした可能性があるため）
        print("\nDB接続を確認・再接続しています...")
        try:
            with conn.cursor() as test_cur:
                test_cur.execute("SELECT 1")
            print("既存の接続は有効です。")
        except Exception as e:
            print(f"接続が切れていました: {e}")
            print("DB接続を再確立します...")
            conn.close()
            conn = get_db_connection()
            if not conn:
                print(f"警告: DB接続の再確立に失敗しました。")
                print(f"抽出結果はバックアップファイルに保存されています: {backup_path}")
                return
            print("DB接続を再確立しました。")

        # DB挿入 (トランザクション管理)
        print("\nデータベースへの挿入を開始します...")
        try:
            insert_effects(conn, final_all_rows, truncate=args.truncate)
            conn.commit()
            print("データベースへの変更をコミットしました。")
        except Exception as insert_error:
            print(f"\nDB挿入に失敗しました: {insert_error}")
            print(f"抽出結果はバックアップファイルに保存されています: {backup_path}")
            raise

    except Exception as error:
        print(f"\nエラーが発生しました: {error}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                if not conn.closed:
                    conn.rollback()
                    print("データベースへの変更をロールバックしました。")
            except Exception as rb_error:
                print(f"ロールバック中にエラー: {rb_error}")
    finally:
        if conn:
            try:
                if not conn.closed:
                    conn.close()
                    print("データベース接続をクローズしました。")
            except Exception as close_error:
                print(f"接続クローズ中にエラー: {close_error}")

    print("\n--- 全ての処理が完了しました ---")


if __name__ == "__main__":
    main()

