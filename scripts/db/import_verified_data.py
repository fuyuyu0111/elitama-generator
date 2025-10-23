"""
Phase 0で作成した検証済みデータ（buff_finetuning_dataset_full.jsonl）を
skill_completeテーブルに反映するスクリプト

- 既存のLLM解析結果をoriginal_llm_valuesに保存
- JSONLのデータで置き換え
- verification_statusを'verified'に設定
- correctionsに差分を記録
"""

import json
import psycopg2
from psycopg2.extras import DictCursor
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def calculate_corrections(original, current):
    """original_llm_values と現在の値を比較して差分を抽出"""
    corrections = {}
    
    fields = ['name', 'target', 'value', 'unit', 'duration', 'probability',
              'occupies_slot', 'is_debuff', 'awakening_required',
              'trigger_timing', 'trigger_condition',
              'has_requirement', 'requirement_type', 'requirement_value', 'requirement_count']
    
    for field in fields:
        old_val = original.get(field)
        new_val = current.get(field)
        if old_val != new_val:
            corrections[field] = {"old": old_val, "new": new_val}
    
    return corrections if corrections else None

def main():
    # JSONLファイルを読み込み（date_createディレクトリにある）
    jsonl_path = os.path.join(os.path.dirname(__file__), '..', '..', 'date_create', 'buff_finetuning_dataset_full.jsonl')
    jsonl_path = os.path.abspath(jsonl_path)
    
    if not os.path.exists(jsonl_path):
        print(f"エラー: {jsonl_path} が見つかりません")
        return
    
    print(f"JSONLファイルを読み込み中: {jsonl_path}")
    
    verified_data = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                verified_data.append(json.loads(line))
    
    print(f"読み込み完了: {len(verified_data)} 個性")
    
    # データベース接続
    conn = psycopg2.connect(os.environ.get('DATABASE_URL'), sslmode='require', cursor_factory=DictCursor)
    cur = conn.cursor()
    
    updated_count = 0
    skipped_count = 0
    
    for entry in verified_data:
        skill_id = entry['skill_id']
        alien_id, skill_number = skill_id.split('_')
        alien_id = int(alien_id)
        skill_number = int(skill_number)
        output_effects = entry['output']
        
        print(f"\n処理中: エイリアンID {alien_id}, 個性{skill_number}")
        
        # 既存データを取得
        cur.execute("""
            SELECT 
                group_id, name, target, value, unit,
                duration, probability, occupies_slot, is_debuff, awakening_required,
                trigger_timing, trigger_condition,
                has_requirement, requirement_type, requirement_value, requirement_count
            FROM skill_complete
            WHERE alien_id = %s AND skill_number = %s
            ORDER BY group_id
        """, (alien_id, skill_number))
        
        existing_effects = cur.fetchall()
        
        if not existing_effects:
            print(f"  スキップ: データが存在しません")
            skipped_count += 1
            continue
        
        # 既存データをoriginal_llm_valuesとして保存
        original_llm_values_list = []
        for row in existing_effects:
            original_llm_values_list.append({
                'group_id': row[0],
                'name': row[1],
                'target': row[2],
                'value': float(row[3]) if row[3] is not None else 0,
                'unit': row[4],
                'duration': row[5],
                'probability': row[6],
                'occupies_slot': row[7],
                'is_debuff': row[8],
                'awakening_required': row[9],
                'trigger_timing': row[10],
                'trigger_condition': row[11],
                'has_requirement': row[12],
                'requirement_type': row[13],
                'requirement_value': row[14],
                'requirement_count': row[15]
            })
        
        # 既存データを削除
        cur.execute("""
            DELETE FROM skill_complete
            WHERE alien_id = %s AND skill_number = %s
        """, (alien_id, skill_number))
        
        print(f"  削除: {len(existing_effects)} 件")
        
        # 新しいデータを挿入
        for i, effect in enumerate(output_effects):
            # original_llm_valuesから対応するデータを取得（group_idで照合）
            original_llm = None
            if i < len(original_llm_values_list):
                original_llm = json.dumps(original_llm_values_list[i], ensure_ascii=False)
            
            # correctionsを計算
            corrections = None
            if i < len(original_llm_values_list):
                corrections = calculate_corrections(original_llm_values_list[i], effect)
            
            cur.execute("""
                INSERT INTO skill_complete (
                    alien_id, skill_number, group_id, name, target, value, unit,
                    duration, probability, occupies_slot, is_debuff, awakening_required,
                    trigger_timing, trigger_condition,
                    has_requirement, requirement_type, requirement_value, requirement_count,
                    verification_status, verified_at, original_llm_values, corrections
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """, (
                alien_id, skill_number, effect.get('group_id', 0), effect['name'],
                effect['target'], effect.get('value', 0), effect['unit'],
                effect.get('duration', 0), effect.get('probability', 100),
                effect.get('occupies_slot'), effect.get('is_debuff', False),
                effect.get('awakening_required', False),
                effect.get('trigger_timing', 'BATTLE_START'),
                json.dumps(effect.get('trigger_condition')) if effect.get('trigger_condition') else None,
                effect.get('has_requirement', False), effect.get('requirement_type'),
                effect.get('requirement_value'), effect.get('requirement_count'),
                'verified',  # 検証済み
                datetime.now(),
                original_llm,
                json.dumps(corrections, ensure_ascii=False) if corrections else None
            ))
        
        print(f"  挿入: {len(output_effects)} 件（検証済みとして）")
        updated_count += 1
    
    # コミット
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\n完了:")
    print(f"  更新: {updated_count} 個性")
    print(f"  スキップ: {skipped_count} 個性")
    print(f"\n注意: date_appを再起動してキャッシュをクリアしてください")

if __name__ == '__main__':
    main()
