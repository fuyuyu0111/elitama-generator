"""
correct_effect_namesテーブルの修正とデータ復元スクリプト

このスクリプトは、correct_effect_namesテーブルの主キーを(correct_name, category)の
複合主キーに変更し、上書きされた個性用データを復元します。

使い方:
    # 確認のみ（スキーマ変更なし）
    python scripts/fix_correct_effect_names.py --check-only
    
    # 完全実行（スキーマ変更とデータ復元）
    python scripts/fix_correct_effect_names.py
    
    # 復元のみ実行（スキーマ変更はスキップ）
    python scripts/fix_correct_effect_names.py --restore-only

処理内容:
1. 現在のDB状態を確認
2. データをエクスポート（バックアップ）
3. スキーマ変更（主キーを複合主キーに変更）
4. データ復元（個性用マッピングから自動復元）
5. データ整合性確認

注意:
- スキーマ変更は不可逆的な操作です。必ずバックアップが作成されます。
- 既にスキーマ変更が完了している場合は--restore-onlyを使用してください。
"""

import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# プロジェクトルート設定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

DATABASE_URL = os.getenv("DATABASE_URL")


def get_db_connection():
    """DB接続を取得"""
    if not DATABASE_URL:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def check_current_db_state(conn):
    """現在のcorrect_effect_namesテーブルの状態を確認"""
    print("=" * 80)
    print("1. 現在のDB状態を確認中...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # 全データを取得
    cur.execute("""
        SELECT correct_name, effect_type, category, created_at
        FROM correct_effect_names
        ORDER BY correct_name, category
    """)
    all_records = cur.fetchall()
    
    print(f"総レコード数: {len(all_records)}")
    
    # 個性用と特技用に分類
    personality_records = [r for r in all_records if not r['category'].startswith('S_SKILL_')]
    special_records = [r for r in all_records if r['category'].startswith('S_SKILL_')]
    
    print(f"個性用（S_SKILL_以外）: {len(personality_records)}件")
    print(f"特技用（S_SKILL_で始まる）: {len(special_records)}件")
    
    # 重複しているeffect_nameを確認（個性と特技で同じ名前がある場合）
    personality_names = {r['correct_name'] for r in personality_records}
    special_names = {r['correct_name'] for r in special_records}
    duplicate_names = personality_names & special_names
    
    print(f"\n個性と特技で重複しているeffect_name: {len(duplicate_names)}件")
    if duplicate_names:
        print("重複している名前（最初の20件）:")
        for name in sorted(duplicate_names)[:20]:
            print(f"  - {name}")
            # 個性と特技の両方のレコードを表示
            p_rec = [r for r in personality_records if r['correct_name'] == name]
            s_rec = [r for r in special_records if r['correct_name'] == name]
            if p_rec:
                print(f"    個性: {p_rec[0]['category']}")
            if s_rec:
                print(f"    特技: {s_rec[0]['category']}")
        if len(duplicate_names) > 20:
            print(f"  ... 他 {len(duplicate_names) - 20} 件")
    
    # 個性用のcategoryが特技用に上書きされている可能性があるレコードを確認
    # （個性用の名前なのに、categoryがS_SKILL_で始まっている場合）
    print("\n個性用の名前なのに特技用のcategoryになっている可能性があるレコード:")
    suspicious = []
    personality_mapping = get_personality_effect_mapping()
    for record in special_records:
        if record['correct_name'] in personality_mapping:
            expected_type, expected_category = personality_mapping[record['correct_name']]
            if not expected_category.startswith('S_SKILL_'):
                suspicious.append(record)
    
    print(f"疑わしいレコード数: {len(suspicious)}件")
    if suspicious:
        print("疑わしいレコード（最初の20件）:")
        for rec in suspicious[:20]:
            print(f"  - {rec['correct_name']}: category={rec['category']}")
            expected_type, expected_category = personality_mapping[rec['correct_name']]
            print(f"    （本来は個性用: {expected_category} であるべき）")
        if len(suspicious) > 20:
            print(f"  ... 他 {len(suspicious) - 20} 件")
    
    cur.close()
    
    return {
        'all_records': all_records,
        'personality_records': personality_records,
        'special_records': special_records,
        'duplicate_names': duplicate_names,
        'suspicious_records': suspicious
    }


def get_personality_effect_mapping():
    """個性用の効果マッピングを取得"""
    # auto_complete_classification関数のマッピング
    effect_categories = {
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
        "急速ダメージダウン抵抗": ("BUFF", "BUFF_RESIST"),
        "持続ダメージダウン抵抗": ("BUFF", "BUFF_RESIST"),
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
        "自動回復": ("BUFF", "BUFF_OTHER"),
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
        "時速ダメージ": ("DEBUFF", "DEBUFF_OTHER"),
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
    
    return effect_categories


def backup_current_data(conn, output_dir):
    """現在のデータをエクスポート（バックアップ）"""
    print("\n" + "=" * 80)
    print("2. 現在のデータをバックアップ中...")
    print("=" * 80)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = output_dir / f"correct_effect_names_backup_{timestamp}.json"
    
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT correct_name, effect_type, category, created_at
        FROM correct_effect_names
        ORDER BY correct_name, category
    """)
    records = [dict(row) for row in cur.fetchall()]
    cur.close()
    
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"バックアップ完了: {backup_path}")
    print(f"バックアップ件数: {len(records)}件")
    
    return backup_path


def get_special_skill_mapping():
    """特技用の効果マッピングを取得（現在は使用しない）"""
    # 元々analysis/prompts/s_skill_effect_names.jsonから読み込んでいたが、
    # 解析機能の削除とともに空のマッピングを返す
    return {}


def modify_schema(conn):
    """correct_effect_namesテーブルの主キーを複合主キーに変更"""
    print("\n" + "=" * 80)
    print("3. スキーマ変更中...")
    print("=" * 80)
    
    cur = conn.cursor()
    
    try:
        # 既存の主キー制約を確認
        cur.execute("""
            SELECT constraint_name, constraint_type
            FROM information_schema.table_constraints
            WHERE table_name = 'correct_effect_names'
            AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        """)
        constraints = cur.fetchall()
        print(f"既存の制約: {constraints}")
        
        # 既存の主キー制約を削除（存在する場合）
        cur.execute("""
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'correct_effect_names'
            AND constraint_type = 'PRIMARY KEY'
        """)
        pk_constraints = cur.fetchall()
        
        for (constraint_name,) in pk_constraints:
            print(f"主キー制約 '{constraint_name}' を削除中...")
            cur.execute(f"ALTER TABLE correct_effect_names DROP CONSTRAINT IF EXISTS {constraint_name}")
        
        # 既存のUNIQUE制約を削除（correct_name単独の場合）
        cur.execute("""
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'correct_effect_names'
            AND constraint_type = 'UNIQUE'
        """)
        unique_constraints = cur.fetchall()
        
        for (constraint_name,) in unique_constraints:
            # correct_name単独のUNIQUE制約を確認
            cur.execute("""
                SELECT column_name
                FROM information_schema.key_column_usage
                WHERE constraint_name = %s
                AND table_name = 'correct_effect_names'
            """, (constraint_name,))
            columns = [row[0] for row in cur.fetchall()]
            if len(columns) == 1 and columns[0] == 'correct_name':
                print(f"UNIQUE制約 '{constraint_name}' (correct_name) を削除中...")
                cur.execute(f"ALTER TABLE correct_effect_names DROP CONSTRAINT IF EXISTS {constraint_name}")
        
        # 複合主キーを追加
        print("複合主キー (correct_name, category) を追加中...")
        cur.execute("""
            ALTER TABLE correct_effect_names
            ADD PRIMARY KEY (correct_name, category)
        """)
        
        conn.commit()
        print("スキーマ変更が完了しました。")
        
    except Exception as e:
        conn.rollback()
        print(f"スキーマ変更エラー: {e}")
        raise
    finally:
        cur.close()


def restore_personality_data(conn):
    """個性データを復元"""
    print("\n" + "=" * 80)
    print("4. 個性データを復元中...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    personality_mapping = get_personality_effect_mapping()
    special_mapping = get_special_skill_mapping()
    
    # 現在のデータを取得（correct_nameとcategoryの組み合わせをキーにする）
    cur.execute("""
        SELECT correct_name, effect_type, category
        FROM correct_effect_names
    """)
    # (correct_name, category) の組み合わせをキーにする
    current_records = {(row['correct_name'], row['category']): row for row in cur.fetchall()}
    
    restored_count = 0
    inserted_count = 0
    skipped_count = 0
    
    # 個性用のマッピングに基づいて復元
    for effect_name, (effect_type, category) in personality_mapping.items():
        key = (effect_name, category)
        
        # 個性用のレコードが既に存在するかチェック
        if key in current_records:
            # 既に存在する場合はスキップ
            skipped_count += 1
            continue
        
        # 個性用のレコードが存在しない場合、追加する
        try:
            cur.execute("""
                INSERT INTO correct_effect_names (correct_name, effect_type, category, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (correct_name, category) DO NOTHING
            """, (effect_name, effect_type, category, datetime.now()))
            
            # 挿入されたかどうかを確認
            if cur.rowcount > 0:
                restored_count += 1
                print(f"  復元: {effect_name} -> {category}")
            else:
                # 既に存在していた（競合していた）
                skipped_count += 1
        except Exception as e:
            print(f"  復元エラー ({effect_name} -> {category}): {e}")
    
    conn.commit()
    cur.close()
    
    print(f"復元完了: {restored_count}件を復元、{skipped_count}件は既に存在していました。")


def verify_data(conn):
    """データ整合性を確認"""
    print("\n" + "=" * 80)
    print("5. データ整合性を確認中...")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # 個性用と特技用のレコード数を確認
    cur.execute("""
        SELECT 
            COUNT(*) FILTER (WHERE category NOT LIKE 'S_SKILL_%') as personality_count,
            COUNT(*) FILTER (WHERE category LIKE 'S_SKILL_%') as special_count,
            COUNT(DISTINCT correct_name) as unique_names
        FROM correct_effect_names
    """)
    stats = cur.fetchone()
    
    print(f"個性用レコード数: {stats['personality_count']}")
    print(f"特技用レコード数: {stats['special_count']}")
    print(f"総レコード数: {stats['personality_count'] + stats['special_count']}")
    print(f"ユニークな効果名数: {stats['unique_names']}")
    
    # 重複チェック（同じeffect_nameで個性と特技の両方がある場合）
    cur.execute("""
        WITH personality_names AS (
            SELECT DISTINCT correct_name
            FROM correct_effect_names
            WHERE category NOT LIKE 'S_SKILL_%'
        ),
        special_names AS (
            SELECT DISTINCT correct_name
            FROM correct_effect_names
            WHERE category LIKE 'S_SKILL_%'
        )
        SELECT COUNT(*) as duplicate_count
        FROM personality_names p
        INNER JOIN special_names s ON p.correct_name = s.correct_name
    """)
    duplicate_count = cur.fetchone()['duplicate_count']
    
    print(f"\n個性と特技で重複している効果名: {duplicate_count}件")
    if duplicate_count > 0:
        print("（これは正常です。同じ効果名でも個性用と特技用で異なるcategoryとして保存されます）")
    
    # 個性用のマッピングに存在する効果名の確認
    personality_mapping = get_personality_effect_mapping()
    if personality_mapping:
        # 個性用のレコードをすべて取得してからフィルタリング
        cur.execute("""
            SELECT correct_name, category
            FROM correct_effect_names
            WHERE category NOT LIKE 'S_SKILL_%'
        """)
        existing_personality = {row['correct_name']: row['category'] for row in cur.fetchall()}
    else:
        existing_personality = {}
    
    missing_count = 0
    wrong_category_count = 0
    for effect_name, (expected_type, expected_category) in personality_mapping.items():
        if effect_name not in existing_personality:
            missing_count += 1
            print(f"  警告: 個性用効果 '{effect_name}' が存在しません")
        elif existing_personality[effect_name] != expected_category:
            wrong_category_count += 1
            print(f"  警告: 個性用効果 '{effect_name}' のcategoryが不正です")
            print(f"    期待: {expected_category}, 実際: {existing_personality[effect_name]}")
    
    if missing_count == 0 and wrong_category_count == 0:
        print("\n✓ 個性用データは正しく復元されています。")
    else:
        print(f"\n警告: {missing_count}件の欠損、{wrong_category_count}件のcategory不一致があります。")
    
    cur.close()


if __name__ == "__main__":
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="correct_effect_namesテーブルの修正とデータ復元")
    parser.add_argument("--check-only", action="store_true", help="確認のみ実行（スキーマ変更は行わない）")
    parser.add_argument("--skip-restore", action="store_true", help="データ復元をスキップ")
    parser.add_argument("--restore-only", action="store_true", help="データ復元のみ実行（スキーマ変更はスキップ）")
    args = parser.parse_args()
    
    conn = None
    try:
        conn = get_db_connection()
        
        # 現在の状態を確認
        state = check_current_db_state(conn)
        
        # バックアップ
        backup_dir = PROJECT_ROOT / "backups"
        backup_path = backup_current_data(conn, backup_dir)
        
        if args.check_only:
            print("\n確認のみモード: スキーマ変更とデータ復元はスキップされました。")
            sys.exit(0)
        
        if args.restore_only:
            # 復元のみ実行
            restore_personality_data(conn)
            verify_data(conn)
        else:
            # スキーマ変更
            modify_schema(conn)
            
            # データ復元
            if not args.skip_restore:
                restore_personality_data(conn)
            
            # 整合性確認
            verify_data(conn)
        
        print("\n" + "=" * 80)
        print("全ての処理が完了しました！")
        print("=" * 80)
        
    except Exception as e:
        print(f"\nエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            conn.close()

