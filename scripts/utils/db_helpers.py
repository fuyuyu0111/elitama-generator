"""
データベース操作の共通ヘルパー関数

命名規則の統一と判定ロジックの共通化を目的とする
"""
import psycopg2
from psycopg2.extras import DictCursor
from typing import Dict, Optional, Any


def normalize_alien_row(row: dict) -> dict:
    """
    alienテーブルの行データを正規化
    S_Skill -> s_skill, S_Skill_text -> s_skill_text
    
    Args:
        row: alienテーブルから取得した行データ（DictCursor形式）
    
    Returns:
        正規化された辞書（Python/JavaScript互換形式）
    """
    if isinstance(row, dict):
        normalized = dict(row)
    else:
        # DictCursorのRowオブジェクトの場合
        normalized = dict(row)
    
    # S_Skill -> s_skill に変換
    if "S_Skill" in normalized:
        normalized["s_skill"] = normalized.pop("S_Skill")
    
    # S_Skill_text -> s_skill_text に変換
    if "S_Skill_text" in normalized:
        normalized["s_skill_text"] = normalized.pop("S_Skill_text")
    
    return normalized


def is_special_skill(skill_text: str, conn) -> bool:
    """
    特技かどうかを判定（skill_textから判定）
    
    Args:
        skill_text: 判定するスキルテキスト
        conn: データベース接続オブジェクト
    
    Returns:
        True if 特技, False if 個性
    """
    if not skill_text or skill_text == 'なし':
        return False
    
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT COUNT(*) > 0 as is_special
            FROM alien
            WHERE "S_Skill_text" = %s 
              AND "S_Skill_text" IS NOT NULL 
              AND "S_Skill_text" != 'なし'
        """, (skill_text,))
        result = cur.fetchone()[0]
        return bool(result)
    except Exception as e:
        # エラーの場合はFalseを返す（安全側に倒す）
        print(f"Warning: is_special_skill判定エラー: {e}")
        return False
    finally:
        cur.close()


def is_special_skill_by_category(category: Optional[str]) -> bool:
    """
    カテゴリから特技かどうかを判定
    
    Args:
        category: カテゴリ文字列
    
    Returns:
        True if 特技カテゴリ (S_SKILL_で始まる), False otherwise
    """
    if not category:
        return False
    return category.startswith('S_SKILL_')


def get_all_special_skill_texts(conn) -> set:
    """
    alienテーブルからすべての特技テキストを取得
    
    Args:
        conn: データベース接続オブジェクト
    
    Returns:
        特技テキストのセット
    """
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT "S_Skill_text"
            FROM alien
            WHERE "S_Skill_text" IS NOT NULL 
              AND "S_Skill_text" != 'なし'
        """)
        result = set(row[0] for row in cur.fetchall())
        return result
    finally:
        cur.close()


def is_personality_skill(skill_text: str, special_skill_texts: set) -> bool:
    """
    個性かどうかを判定（特技リストとの比較）
    
    Args:
        skill_text: 判定するスキルテキスト
        special_skill_texts: 特技テキストのセット（get_all_special_skill_textsで取得）
    
    Returns:
        True if 個性, False if 特技
    """
    if not skill_text or skill_text == 'なし':
        return False
    return skill_text not in special_skill_texts

