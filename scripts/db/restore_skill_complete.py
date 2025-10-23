#!/usr/bin/env python3
"""
skill_complete テーブルをJSONLバックアップから復元するスクリプト
"""
import os
import sys
import json
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from datetime import datetime


def restore_from_jsonl(db_url: str, jsonl_path: str, truncate: bool = False):
    """
    JSONLファイルからskill_completeテーブルにデータを復元
    
    Args:
        db_url: データベース接続URL
        jsonl_path: 復元元のJSONLファイルパス
        truncate: Trueの場合、復元前にテーブルをTRUNCATEする
    """
    # JSONLファイルを読み込み
    print(f"JSONLファイルを読み込んでいます: {jsonl_path}")
    rows = []
    
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            
            try:
                data = json.loads(line)
                
                # タプルに変換（INSERT用）
                row = (
                    data.get('alien_id'),
                    data.get('skill_number'),
                    data.get('group_id'),
                    data.get('name'),
                    data.get('target'),
                    data.get('value'),
                    data.get('unit'),
                    data.get('duration', 0),
                    data.get('probability', 100),
                    data.get('occupies_slot'),
                    data.get('is_debuff', False),
                    data.get('awakening_required', False),
                    data.get('trigger_timing'),
                    json.dumps(data.get('trigger_condition')) if data.get('trigger_condition') else None,
                    data.get('has_requirement', False),
                    data.get('requirement_type'),
                    data.get('requirement_value'),
                    data.get('requirement_count'),
                    data.get('verification_status', 'unverified'),
                    data.get('verified_at'),
                    data.get('llm_model'),
                    data.get('llm_analyzed_at'),
                    json.dumps(data.get('original_llm_values')) if data.get('original_llm_values') else None,
                    json.dumps(data.get('corrections')) if data.get('corrections') else None,
                    data.get('notes')
                )
                rows.append(row)
                
            except json.JSONDecodeError as e:
                print(f"警告: 行 {line_num} のJSON解析に失敗しました: {e}")
                continue
    
    print(f"{len(rows)} 件のレコードを読み込みました。")
    
    if not rows:
        print("復元対象のデータがありません。")
        return
    
    # データベースに接続
    print("\nデータベースに接続しています...")
    conn = psycopg2.connect(db_url)
    
    try:
        with conn.cursor() as cur:
            if truncate:
                print("skill_complete を TRUNCATE します...")
                cur.execute("TRUNCATE skill_complete RESTART IDENTITY")
            
            print(f"skill_complete に {len(rows)} 件のレコードを挿入しています...")
            insert_sql = (
                "INSERT INTO skill_complete ("
                "alien_id, skill_number, group_id, name, target, value, unit, duration, probability, "
                "occupies_slot, is_debuff, awakening_required, trigger_timing, trigger_condition, "
                "has_requirement, requirement_type, requirement_value, requirement_count, "
                "verification_status, verified_at, llm_model, llm_analyzed_at, original_llm_values, corrections, notes"
                ") VALUES %s"
            )
            execute_values(cur, insert_sql, rows)
        
        conn.commit()
        print(f"✅ 復元完了: {len(rows)} 件のレコードを挿入しました。")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ エラーが発生しました: {e}")
        raise
    
    finally:
        conn.close()
        print("データベース接続をクローズしました。")


def main():
    """メイン処理"""
    import argparse
    
    parser = argparse.ArgumentParser(description="skill_complete テーブルをJSONLバックアップから復元")
    parser.add_argument("jsonl_path", help="復元元のJSONLファイルパス")
    parser.add_argument("--truncate", action="store_true", help="復元前にテーブルをTRUNCATEする")
    args = parser.parse_args()
    
    # .envファイルを読み込み
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    
    if not db_url:
        print("エラー: DATABASE_URL が設定されていません。")
        sys.exit(1)
    
    if not os.path.exists(args.jsonl_path):
        print(f"エラー: ファイルが見つかりません: {args.jsonl_path}")
        sys.exit(1)
    
    # 復元実行
    restore_from_jsonl(db_url, args.jsonl_path, truncate=args.truncate)
    
    print("\n--- 復元処理が完了しました ---")


if __name__ == "__main__":
    main()
