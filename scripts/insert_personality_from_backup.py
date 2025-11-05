"""
backups/skill_list.jsonl から個性の解析結果を読み込み、DBに挿入するスクリプト
"""

import os
import json
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
from typing import List, Tuple, Set
from dotenv import load_dotenv

# --- プロジェクトルート設定 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- 環境変数読み込み ---
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

# --- 環境変数 ---
DATABASE_URL = os.getenv("DATABASE_URL")

# --- 定数 ---
DEST_TABLE = "skill_text_verified_effects"
BACKUP_FILE = PROJECT_ROOT / "backups" / "skill_list.jsonl"


# --- DB接続 ---
def get_db_connection():
    """DB接続を取得"""
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    try:
        conn = psycopg2.connect(
            DATABASE_URL,
            sslmode='prefer'
        )
        return conn
    except Exception as e:
        print(f"データベース接続に失敗しました: {e}")
        return None


# --- JSONLファイル読み込み ---
def load_jsonl_file(file_path: Path) -> List[dict]:
    """JSONLファイルを読み込んで辞書のリストを返す"""
    rows = []
    if not file_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {file_path}")
    
    print(f"JSONLファイルを読み込んでいます: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                rows.append(row)
            except json.JSONDecodeError as e:
                print(f"警告: {line_num}行目のJSONパースに失敗: {e}")
                continue
    
    print(f"{len(rows)} 件のデータを読み込みました。")
    return rows


# --- データ形式変換 ---
def convert_to_tuples(rows: List[dict]) -> List[Tuple]:
    """辞書のリストをTupleのリストに変換"""
    tuples = []
    column_names = [
        'skill_text', 'effect_name', 'effect_type', 'category', 'condition_target',
        'requires_awakening', 'target', 'has_requirement', 'requirement_details', 'requirement_count'
    ]
    
    for row in rows:
        # Noneや空文字列を適切に処理
        tuple_row = tuple(
            row.get(col) if row.get(col) != '' else None
            for col in column_names
        )
        tuples.append(tuple_row)
    
    return tuples


# --- 個性テキストの取得 ---
def get_unique_skill_texts(rows: List[dict]) -> Set[str]:
    """JSONLからユニークなskill_textのセットを取得"""
    skill_texts = set()
    for row in rows:
        skill_text = row.get('skill_text')
        if skill_text:
            skill_texts.add(skill_text)
    return skill_texts


# --- 個性テキストの行を削除 ---
def delete_personality_rows(conn, skill_texts: Set[str]):
    """個性テキストに一致する行を削除"""
    if not skill_texts:
        print("削除対象の個性テキストがありません。")
        return 0
    
    try:
        with conn.cursor() as cur:
            # IN句で一括削除（大量データの場合はバッチ処理）
            skill_text_list = list(skill_texts)
            batch_size = 1000
            total_deleted = 0
            
            for i in range(0, len(skill_text_list), batch_size):
                batch = skill_text_list[i:i + batch_size]
                placeholders = ','.join(['%s'] * len(batch))
                delete_sql = f"""
                DELETE FROM {DEST_TABLE}
                WHERE skill_text IN ({placeholders})
                """
                cur.execute(delete_sql, batch)
                deleted = cur.rowcount
                total_deleted += deleted
                print(f"削除中: {min(i + batch_size, len(skill_text_list))}/{len(skill_text_list)} 件の個性テキストを処理... ({total_deleted}件削除)")
            
            conn.commit()
            print(f"\n個性テキストの行を {total_deleted} 件削除しました。")
            return total_deleted
    except Exception as e:
        print(f"削除エラー: {e}")
        conn.rollback()
        raise


# --- DB一括挿入 ---
def insert_effects_bulk(conn, rows: List[Tuple]):
    """skill_text_verified_effects テーブルに一括INSERT"""
    if not rows:
        print("挿入対象の効果がありませんでした。")
        return
    
    try:
        from datetime import datetime
        with conn.cursor() as cur:
            # execute_valuesで一括INSERT
            # updated_atにNOW()を含める
            insert_sql = f"""
            INSERT INTO {DEST_TABLE} (
                skill_text, effect_name, effect_type, category, condition_target,
                requires_awakening, target, has_requirement, requirement_details, requirement_count,
                updated_at
            ) VALUES %s
            """
            
            # 各タプルにupdated_at（NOW()相当）を追加
            now = datetime.now()
            rows_with_timestamp = [
                row + (now,) if len(row) == 10 else row
                for row in rows
            ]
            
            print(f"一括INSERTを実行中... ({len(rows)} 件)")
            # templateで11個のプレースホルダーを指定（10個のカラム + updated_at）
            execute_values(
                cur,
                insert_sql,
                rows_with_timestamp,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                page_size=1000
            )
            
            conn.commit()
            
            # 実際の挿入数を確認（skill_textでフィルタ）
            skill_texts_for_check = set(row[0] for row in rows if len(row) >= 1)
            if skill_texts_for_check:
                check_sql = f"""
                SELECT COUNT(*) FROM {DEST_TABLE}
                WHERE skill_text = ANY(%s)
                """
                cur.execute(check_sql, (list(skill_texts_for_check),))
                actual_count = cur.fetchone()[0]
                print(f"{DEST_TABLE} に {actual_count} 件の行が存在します（挿入対象: {len(rows)} 件）。")
            else:
                # rowcountを使用（page_sizeより小さい場合は正確）
                inserted_count = cur.rowcount if cur.rowcount > 0 else len(rows)
                print(f"{DEST_TABLE} に {inserted_count} 件を挿入しました（rowcount使用）。")
    except Exception as e:
        print(f"DB挿入エラー: {e}")
        conn.rollback()
        raise


# --- メイン処理 ---
def main():
    """メイン処理"""
    print("=" * 80)
    print("個性の解析結果をDBに挿入します")
    print("=" * 80)
    
    # JSONLファイルを読み込む
    try:
        rows_dict = load_jsonl_file(BACKUP_FILE)
        if not rows_dict:
            print("読み込んだデータがありません。")
            return
    except Exception as e:
        print(f"ファイル読み込みエラー: {e}")
        return
    
    # 個性テキストのリストを取得
    print("\n個性テキストのリストを取得しています...")
    skill_texts = get_unique_skill_texts(rows_dict)
    print(f"{len(skill_texts)} 件のユニークな個性テキストを取得しました。")
    
    # Tuple形式に変換
    print("\nデータ形式を変換しています...")
    rows_tuple = convert_to_tuples(rows_dict)
    print(f"{len(rows_tuple)} 件のデータを準備しました。")
    
    # プレビュー（最初の5件）
    print("\n--- プレビュー（最初の5件） ---")
    for i, row in enumerate(rows_tuple[:5]):
        print(f"  {i+1}. skill_text={row[0][:40]}..., effect_name={row[1]}, effect_type={row[2]}")
    
    # DB接続
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        # 個性テキストの行を削除
        print("\n既存の個性テキストの行を削除しています...")
        delete_personality_rows(conn, skill_texts)
        
        # DB一括挿入
        print("\nデータベースへの一括挿入を開始します...")
        insert_effects_bulk(conn, rows_tuple)
        print("\nデータベースへの変更をコミットしました。")
        
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

