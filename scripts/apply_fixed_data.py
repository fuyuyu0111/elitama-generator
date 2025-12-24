"""
修正した効果データと辞書をデータベースに適用するスクリプト

処理内容:
1. backups/skill_list_fixed.jsonlから修正データを読み込む
2. 特技データ（category LIKE 'S_SKILL_%'）を除外し、個性データのみを抽出
3. correct_effect_namesテーブルを更新（個性用のみ、特技用は変更しない）
4. skill_text_verified_effectsテーブルを更新（個性用のみ、特技用は変更しない）

注意:
- 特技データは一切変更しません
- バックアップを自動的に作成します
"""

import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
from collections import defaultdict
import sys

# プロジェクトルート設定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

DATABASE_URL = os.getenv("DATABASE_URL")

# ファイルパス
FIXED_JSONL_PATH = PROJECT_ROOT / "backups" / "skill_list_fixed.jsonl"
BACKUP_DIR = PROJECT_ROOT / "backups"

# 個性用の効果カテゴリマッピング
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


def get_db_connection():
    """DB接続を取得"""
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def is_special_skill_by_category(category):
    """カテゴリから特技かどうかを判定"""
    if not category:
        return False
    return category.startswith('S_SKILL_')


def load_fixed_data(jsonl_path):
    """修正済みJSONLファイルを読み込む"""
    print(f"修正データを読み込み中: {jsonl_path}")
    
    if not jsonl_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {jsonl_path}")
    
    all_effects = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                effect = json.loads(line)
                all_effects.append(effect)
            except json.JSONDecodeError as e:
                print(f"警告: 行 {line_num} のJSON解析エラー: {e}")
                continue
    
    print(f"読み込んだ効果データ: {len(all_effects)}件")
    
    # 特技データを除外
    personality_effects = [
        e for e in all_effects 
        if not is_special_skill_by_category(e.get('category'))
    ]
    special_effects = [
        e for e in all_effects 
        if is_special_skill_by_category(e.get('category'))
    ]
    
    print(f"個性データ: {len(personality_effects)}件")
    print(f"特技データ（除外）: {len(special_effects)}件")
    
    return personality_effects


def backup_current_data(conn, backup_dir):
    """現在のデータをバックアップ"""
    print("\n" + "=" * 80)
    print("データベースのバックアップを作成中...")
    print("=" * 80)
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # correct_effect_namesのバックアップ（個性用のみ）
    cur.execute("""
        SELECT correct_name, effect_type, category, created_at
        FROM correct_effect_names
        WHERE category NOT LIKE 'S_SKILL_%'
        ORDER BY correct_name, category
    """)
    correct_names_backup = [dict(row) for row in cur.fetchall()]
    
    correct_names_backup_path = backup_dir / f"correct_effect_names_backup_{timestamp}.json"
    with open(correct_names_backup_path, 'w', encoding='utf-8') as f:
        json.dump(correct_names_backup, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"correct_effect_namesバックアップ: {correct_names_backup_path} ({len(correct_names_backup)}件)")
    
    # skill_text_verified_effectsのバックアップ（個性用のみ）
    cur.execute("""
        SELECT *
        FROM skill_text_verified_effects
        WHERE category NOT LIKE 'S_SKILL_%'
        ORDER BY skill_text, effect_name, COALESCE(condition_target, '')
    """)
    effects_backup = [dict(row) for row in cur.fetchall()]
    
    effects_backup_path = backup_dir / f"skill_text_verified_effects_backup_{timestamp}.json"
    with open(effects_backup_path, 'w', encoding='utf-8') as f:
        json.dump(effects_backup, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"skill_text_verified_effectsバックアップ: {effects_backup_path} ({len(effects_backup)}件)")
    
    cur.close()
    
    return correct_names_backup_path, effects_backup_path


def update_correct_effect_names(conn, personality_effects):
    """correct_effect_namesテーブルを更新（個性用のみ）"""
    print("\n" + "=" * 80)
    print("correct_effect_namesテーブルを更新中（個性用のみ）...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # 効果名の一意セットを作成（修正データから）
    effect_names_from_data = set()
    for effect in personality_effects:
        effect_name = effect.get('effect_name')
        if effect_name:
            effect_names_from_data.add(effect_name)
    
    # PERSONALITY_EFFECT_CATEGORIESに存在する効果名を追加
    effect_names_from_dict = set(PERSONALITY_EFFECT_CATEGORIES.keys())
    
    # 両方のソースから効果名を統合
    all_effect_names = effect_names_from_data | effect_names_from_dict
    
    print(f"更新対象の効果名: {len(all_effect_names)}件")
    
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    for effect_name in all_effect_names:
        # PERSONALITY_EFFECT_CATEGORIESから効果タイプとカテゴリを取得
        if effect_name in PERSONALITY_EFFECT_CATEGORIES:
            effect_type, category = PERSONALITY_EFFECT_CATEGORIES[effect_name]
        else:
            # 修正データから取得を試みる
            matching_effects = [e for e in personality_effects if e.get('effect_name') == effect_name]
            if matching_effects:
                effect_type = matching_effects[0].get('effect_type')
                category = matching_effects[0].get('category')
                if not effect_type or not category:
                    print(f"警告: {effect_name} のeffect_typeまたはcategoryが取得できませんでした。スキップします。")
                    skipped_count += 1
                    continue
            else:
                print(f"警告: {effect_name} がPERSONALITY_EFFECT_CATEGORIESに存在しません。スキップします。")
                skipped_count += 1
                continue
        
        try:
            # 既存レコードをチェック
            cur.execute("""
                SELECT correct_name, effect_type, category
                FROM correct_effect_names
                WHERE correct_name = %s AND category = %s
            """, (effect_name, category))
            existing = cur.fetchone()
            
            if existing:
                # 既に存在する場合は、effect_typeが異なる場合のみ更新
                if existing['effect_type'] != effect_type:
                    cur.execute("""
                        UPDATE correct_effect_names
                        SET effect_type = %s
                        WHERE correct_name = %s AND category = %s
                    """, (effect_type, effect_name, category))
                    updated_count += 1
                    print(f"  更新: {effect_name} -> {category} (effect_type: {existing['effect_type']} -> {effect_type})")
                else:
                    skipped_count += 1
            else:
                # 新規挿入
                cur.execute("""
                    INSERT INTO correct_effect_names (correct_name, effect_type, category, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (correct_name, category) DO UPDATE
                    SET effect_type = EXCLUDED.effect_type
                """, (effect_name, effect_type, category, datetime.now()))
                
                if cur.rowcount > 0:
                    inserted_count += 1
                    print(f"  追加: {effect_name} -> {category}")
        except Exception as e:
            print(f"エラー: {effect_name} -> {category} の更新に失敗: {e}")
            error_count += 1
            continue
    
    conn.commit()
    
    # PERSONALITY_EFFECT_CATEGORIESに存在しない個性用のエントリを削除
    print(f"\n不要なエントリを削除中（個性用のみ）...")
    cur.execute("""
        SELECT correct_name, category
        FROM correct_effect_names
        WHERE category NOT LIKE 'S_SKILL_%'
    """)
    all_db_effect_names = {row['correct_name']: row['category'] for row in cur.fetchall()}
    
    deleted_count = 0
    for db_effect_name, db_category in all_db_effect_names.items():
        # PERSONALITY_EFFECT_CATEGORIESに存在しない場合は削除
        if db_effect_name not in PERSONALITY_EFFECT_CATEGORIES:
            # 修正データにも存在しない場合のみ削除
            if db_effect_name not in effect_names_from_data:
                try:
                    cur.execute("""
                        DELETE FROM correct_effect_names
                        WHERE correct_name = %s AND category = %s
                    """, (db_effect_name, db_category))
                    if cur.rowcount > 0:
                        deleted_count += 1
                        print(f"  削除: {db_effect_name} ({db_category})")
                except Exception as e:
                    print(f"エラー: {db_effect_name} ({db_category}) の削除に失敗: {e}")
    
    conn.commit()
    cur.close()
    
    print(f"\n更新完了: 追加 {inserted_count}件、更新 {updated_count}件、削除 {deleted_count}件、スキップ {skipped_count}件、エラー {error_count}件")


def update_skill_text_verified_effects(conn, personality_effects):
    """skill_text_verified_effectsテーブルを更新（個性用のみ）"""
    print("\n" + "=" * 80)
    print("skill_text_verified_effectsテーブルを更新中（個性用のみ）...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # まず、既存の個性データを削除（特技データは残す）
    print("既存の個性データを削除中...")
    cur.execute("""
        DELETE FROM skill_text_verified_effects
        WHERE category NOT LIKE 'S_SKILL_%'
    """)
    deleted_count = cur.rowcount
    print(f"削除した個性データ: {deleted_count}件")
    
    # 修正データを挿入
    print("修正データを挿入中...")
    inserted_count = 0
    
    for effect in personality_effects:
        skill_text = effect.get('skill_text')
        effect_name = effect.get('effect_name')
        effect_type = effect.get('effect_type')
        category = effect.get('category')
        condition_target = effect.get('condition_target')
        requires_awakening = effect.get('requires_awakening')
        target = effect.get('target')
        has_requirement = effect.get('has_requirement', False)
        requirement_details = effect.get('requirement_details', '')
        requirement_count = effect.get('requirement_count', 1)
        
        # NULL値の処理
        if condition_target == '':
            condition_target = None
        if requirement_details == '':
            requirement_details = None
        if not requirement_count:
            requirement_count = 1
        
        try:
            cur.execute("""
                INSERT INTO skill_text_verified_effects (
                    skill_text, effect_name, effect_type, category, condition_target,
                    requires_awakening, target, has_requirement, requirement_details, requirement_count,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                skill_text, effect_name, effect_type, category, condition_target,
                requires_awakening, target, has_requirement, requirement_details, requirement_count
            ))
            inserted_count += 1
        except Exception as e:
            print(f"エラー: {skill_text[:50]}... の {effect_name} の挿入に失敗: {e}")
            continue
    
    conn.commit()
    cur.close()
    
    print(f"\n挿入完了: {inserted_count}件")


def verify_data(conn):
    """データ整合性を確認"""
    print("\n" + "=" * 80)
    print("データ整合性を確認中...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # 個性用と特技用のレコード数を確認
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE category NOT LIKE 'S_SKILL_%') as personality_count,
            COUNT(*) FILTER (WHERE category LIKE 'S_SKILL_%') as special_count
        FROM skill_text_verified_effects
    """)
    stats = cur.fetchone()
    
    print(f"skill_text_verified_effects:")
    print(f"  個性データ: {stats['personality_count']}件")
    print(f"  特技データ: {stats['special_count']}件（変更なし）")
    
    # correct_effect_namesも確認
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE category NOT LIKE 'S_SKILL_%') as personality_count,
            COUNT(*) FILTER (WHERE category LIKE 'S_SKILL_%') as special_count
        FROM correct_effect_names
    """)
    stats = cur.fetchone()
    
    print(f"correct_effect_names:")
    print(f"  個性用: {stats['personality_count']}件")
    print(f"  特技用: {stats['special_count']}件（変更なし）")
    
    cur.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="修正した効果データと辞書をデータベースに適用")
    parser.add_argument("--dry-run", action="store_true", help="実際の更新を行わず、確認のみ実行")
    args = parser.parse_args()
    
    conn = None
    try:
        # データ読み込み
        personality_effects = load_fixed_data(FIXED_JSONL_PATH)
        
        if args.dry_run:
            print("\n" + "=" * 80)
            print("DRY RUN モード: 実際の更新は行いません")
            print("=" * 80)
            print(f"\n適用予定の個性データ: {len(personality_effects)}件")
            
            # 効果名の統計
            effect_names = set(e.get('effect_name') for e in personality_effects)
            print(f"ユニークな効果名: {len(effect_names)}件")
            
            # categoryの統計
            categories = defaultdict(int)
            for e in personality_effects:
                cat = e.get('category', 'unknown')
                categories[cat] += 1
            print("\nカテゴリ別件数:")
            for cat, count in sorted(categories.items()):
                print(f"  {cat}: {count}件")
            
            sys.exit(0)
        
        # DB接続
        conn = get_db_connection()
        
        # バックアップ作成
        backup_correct, backup_effects = backup_current_data(conn, BACKUP_DIR)
        
        # correct_effect_namesを更新
        update_correct_effect_names(conn, personality_effects)
        
        # skill_text_verified_effectsを更新
        update_skill_text_verified_effects(conn, personality_effects)
        
        # 整合性確認
        verify_data(conn)
        
        print("\n" + "=" * 80)
        print("全ての処理が完了しました！")
        print("=" * 80)
        print(f"バックアップ:")
        print(f"  {backup_correct}")
        print(f"  {backup_effects}")
        
    except Exception as e:
        print(f"\nエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

