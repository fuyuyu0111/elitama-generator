from dotenv import load_dotenv
load_dotenv()

import os
import json
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.config['DEBUG'] = True  # デバッグモード有効化（ファイル変更時に自動リロード）
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=DictCursor)

def get_initial_data():
    """起動時に全データを一括読み込み（メインアプリと同様）"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 1. 全エイリアンの基本情報
        cur.execute("SELECT * FROM alien ORDER BY id")
        all_aliens = [dict(row) for row in cur.fetchall()]
        
        # 2. 全skill_completeデータ
        cur.execute("""
            SELECT 
                alien_id, skill_number,
                group_id, name, target, value, unit,
                duration, probability, occupies_slot, is_debuff, awakening_required,
                trigger_timing, trigger_condition,
                has_requirement, requirement_type, requirement_value, requirement_count,
                verification_status, original_llm_values
            FROM skill_complete
            ORDER BY alien_id, skill_number, group_id
        """)
        all_skills = cur.fetchall()
        
        # 3. エイリアンIDごとにスキルデータをグループ化
        skills_by_alien = {}
        for row in all_skills:
            alien_id = row[0]
            skill_number = row[1]
            
            if alien_id not in skills_by_alien:
                skills_by_alien[alien_id] = {1: [], 2: [], 3: []}
            
            effect = {
                'group_id': row[2],
                'name': row[3],
                'target': row[4],
                'value': float(row[5]) if row[5] is not None else 0,
                'unit': row[6],
                'duration': row[7],
                'probability': row[8],
                'occupies_slot': row[9],
                'is_debuff': row[10],
                'awakening_required': row[11],
                'trigger_timing': row[12],
                'trigger_condition': row[13],
                'has_requirement': row[14],
                'requirement_type': row[15],
                'requirement_value': row[16],
                'requirement_count': row[17],
                'verification_status': row[18]
            }
            skills_by_alien[alien_id][skill_number].append(effect)
        
        # 4. ユニークなバフ名リスト
        cur.execute("SELECT DISTINCT name, is_debuff FROM skill_complete ORDER BY name")
        buff_names = {'buffs': [], 'debuffs': []}
        for row in cur.fetchall():
            if row[1]:
                buff_names['debuffs'].append(row[0])
            else:
                buff_names['buffs'].append(row[0])
        
        cur.close()
        conn.close()
        
        return {
            'aliens': all_aliens,
            'skills': skills_by_alien,
            'buff_names': buff_names
        }
        
    except Exception as e:
        print(f"Error in get_initial_data: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.close()
        return {
            'aliens': [],
            'skills': {},
            'buff_names': {'buffs': [], 'debuffs': []}
        }

@app.route('/')
def index():
    """初回ロード時に全データをテンプレートに渡す"""
    try:
        data = get_initial_data()
        
        # エイリアン一覧に検証状態を付与
        aliens_with_status = []
        for alien in data['aliens']:
            alien_copy = dict(alien)
            alien_id = alien_copy['id']
            
            # このエイリアンのスキルデータを取得
            skills = data['skills'].get(alien_id, {1: [], 2: [], 3: []})
            
            # 検証状態を判定（4段階）
            all_statuses = set()
            unknown_slot_count = 0
            
            for skill_num in [1, 2, 3]:
                for effect in skills[skill_num]:
                    all_statuses.add(effect.get('verification_status', 'unverified'))
                    if effect.get('occupies_slot') is None:
                        unknown_slot_count += 1
            
            # 優先順位: on_hold > unverified > partial_verified > verified
            if 'on_hold' in all_statuses:
                alien_copy['status'] = 'on_hold'
            elif 'unverified' in all_statuses or not all_statuses:
                alien_copy['status'] = 'unverified'
            elif unknown_slot_count > 0:
                alien_copy['status'] = 'partial_verified'
            else:
                alien_copy['status'] = 'verified'
            
            aliens_with_status.append(alien_copy)
        
        return render_template('date_index.html', 
                             all_aliens=aliens_with_status,
                             all_skills=data['skills'],
                             buff_names=data['buff_names'])
        
    except Exception as e:
        print(f"Error in index: {e}")
        import traceback
        traceback.print_exc()
        return render_template('date_index.html', 
                             all_aliens=[],
                             all_skills={},
                             buff_names={'buffs': [], 'debuffs': []})

@app.route('/get-alien-data/<int:alien_id>')
def get_alien_data(alien_id):
    """キャッシュされたデータから該当エイリアンのデータを返す"""
    try:
        data = get_initial_data()
        
        # alienの基本情報を検索
        alien = None
        for a in data['aliens']:
            if a['id'] == alien_id:
                alien = dict(a)
                break
        
        if not alien:
            return jsonify({"error": "Alien not found"}), 404
        
        # スキルデータを追加
        skills = data['skills'].get(alien_id, {1: [], 2: [], 3: []})
        for i in range(1, 4):
            alien[f'skill_{i}_data'] = skills[i]
        
        return jsonify(alien)
        
    except Exception as e:
        print(f"Error in get_alien_data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/get-unique-buff-names')
def get_unique_buff_names():
    """キャッシュされたバフ名リストを返す"""
    try:
        data = get_initial_data()
        return jsonify(data['buff_names'])
    except Exception as e:
        print(f"Error in get_unique_buff_names: {e}")
        return jsonify({"buffs": [], "debuffs": []})

@app.route('/save-labels', methods=['POST'])
def save_labels():
    """
    skill_completeテーブルへデータを保存
    - 差分を自動記録（correctionsカラム）
    - verification_statusを更新
    """
    try:
        new_data = request.json.get('data')
        if not new_data:
            return jsonify({"error": "データがありません"}), 400
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        has_unknown_slot = False  # 枠使用が不明な効果があるか
        
        for entry in new_data:
            skill_id = entry['skill_id']
            alien_id, skill_number = skill_id.split('_')
            alien_id = int(alien_id)
            skill_number = int(skill_number)
            text_input = entry['text_input']
            output_effects = entry['output']
            
            # 既存データを取得（original_llm_values用）
            cur.execute("""
                SELECT original_llm_values, verification_status
                FROM skill_complete
                WHERE alien_id = %s AND skill_number = %s
                LIMIT 1
            """, (alien_id, skill_number))
            
            existing = cur.fetchone()
            original_llm = existing[0] if existing else None
            was_verified = existing[1] == 'verified' if existing else False
            
            # 既存データを全削除（同じalien_id + skill_numberのレコード）
            cur.execute("""
                DELETE FROM skill_complete
                WHERE alien_id = %s AND skill_number = %s
            """, (alien_id, skill_number))
            
            # 新しいデータを挿入
            for effect in output_effects:
                # 枠使用が不明な効果をチェック
                if effect.get('occupies_slot') is None:
                    has_unknown_slot = True
                
                # original_llm_valuesの設定（初回保存時のみ）
                if original_llm is None and not was_verified:
                    original_llm = json.dumps(effect, ensure_ascii=False)
                
                # correctionsの計算（検証済みデータの2回目以降の修正時のみ）
                corrections = None
                if original_llm and was_verified and effect.get('occupies_slot') is not None:
                    # 枠使用が不明でない場合のみ差分を記録
                    corrections = calculate_corrections(json.loads(original_llm), effect)
                
                # verification_statusの決定
                if effect.get('occupies_slot') is None:
                    verification_status = 'partial_verified'  # 枠以外検証済み
                else:
                    verification_status = 'verified'  # 完全検証済み
                
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
                        %s, NOW(), %s, %s
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
                    verification_status,
                    original_llm,
                    json.dumps(corrections, ensure_ascii=False) if corrections else None
                ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"status": "success"})
        
    except Exception as e:
        print(f"Error in save_labels: {e}")
        if 'conn' in locals() and conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500


def calculate_corrections(original, current):
    """
    original_llm_values と現在の値を比較して差分を抽出
    """
    corrections = {}
    
    # 比較対象のフィールド
    fields = ['name', 'target', 'value', 'unit', 'duration', 'probability',
              'occupies_slot', 'is_debuff', 'awakening_required',
              'trigger_timing', 'trigger_condition',
              'has_requirement', 'requirement_type', 'requirement_value', 'requirement_count']
    
    for field in fields:
        old_val = original.get(field)
        new_val = current.get(field)
        
        # None vs 存在しないキーを区別
        if field not in original and field not in current:
            continue
        
        if old_val != new_val:
            corrections[field] = {
                'old': old_val,
                'new': new_val
            }
    
    return corrections if corrections else None


@app.route('/rename-effect', methods=['POST'])
def rename_effect():
    """skill_completeテーブル内の効果名を一括変更"""
    data = request.json
    old_name = data.get('old_name')
    new_name = data.get('new_name')

    if not old_name or not new_name:
        return jsonify({"error": "古い名前と新しい名前が必要です。"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 変更対象のレコード数を確認
        cur.execute("SELECT COUNT(*) FROM skill_complete WHERE name = %s", (old_name,))
        changes_made = cur.fetchone()[0]
        
        if changes_made > 0:
            # 一括更新
            cur.execute("""
                UPDATE skill_complete
                SET name = %s
                WHERE name = %s
            """, (new_name, old_name))
            
            conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({"status": "success", "changes": changes_made})

    except Exception as e:
        print(f"Error in rename_effect: {e}")
        if 'conn' in locals() and conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/get-aliens-by-effect', methods=['POST'])
def get_aliens_by_effect():
    """特定の効果を持つエイリアンのリストを取得"""
    data = request.json
    effect_name = data.get('effect_name')

    if not effect_name:
        return jsonify({"error": "効果名が必要です。"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # skill_completeから該当するalien_idを取得
        cur.execute("""
            SELECT DISTINCT sc.alien_id, a.name
            FROM skill_complete sc
            JOIN alien a ON sc.alien_id = a.id
            WHERE sc.name = %s
            ORDER BY sc.alien_id DESC
        """, (effect_name,))
        
        aliens = [{'id': row[0], 'name': row[1]} for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        return jsonify({"aliens": aliens})

    except Exception as e:
        print(f"Error in get_aliens_by_effect: {e}")
        if 'conn' in locals() and conn:
            conn.close()
        return jsonify({"error": str(e)}), 500

@app.route('/get-stats')
def get_stats():
    """検証状態の統計情報を取得（4段階・キャッシュ使用）"""
    try:
        data = get_initial_data()
        
        verified_count = 0
        partial_verified_count = 0
        on_hold_count = 0
        unverified_count = 0
        
        for alien in data['aliens']:
            alien_id = alien['id']
            skills = data['skills'].get(alien_id, {1: [], 2: [], 3: []})
            
            # 検証状態を判定（index()と同じロジック）
            all_statuses = set()
            unknown_slot_count = 0
            
            for skill_num in [1, 2, 3]:
                for effect in skills[skill_num]:
                    all_statuses.add(effect.get('verification_status', 'unverified'))
                    if effect.get('occupies_slot') is None:
                        unknown_slot_count += 1
            
            # 優先順位: on_hold > unverified > partial_verified > verified
            if 'on_hold' in all_statuses:
                on_hold_count += 1
            elif 'unverified' in all_statuses or not all_statuses:
                unverified_count += 1
            elif unknown_slot_count > 0:
                partial_verified_count += 1
            else:
                verified_count += 1
        
        return jsonify({
            "verified": verified_count,
            "partial_verified": partial_verified_count,
            "on_hold": on_hold_count,
            "unverified": unverified_count,
            "total": len(data['aliens'])
        })
        
    except Exception as e:
        print(f"Error in get_stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "verified": 0, 
            "partial_verified": 0,
            "on_hold": 0, 
            "unverified": 0, 
            "total": 0
        })

@app.route('/estimate-slot', methods=['POST'])
def estimate_slot():
    """バフ枠使用の推定を返す（統計的推定 + ルールベース）"""
    data = request.json
    effect_name = data.get('name')
    target = data.get('target')
    has_requirement = data.get('has_requirement', False)
    
    if not effect_name:
        return jsonify({"error": "効果名が必要です"}), 400
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 統計的推定: 同じ効果名の検証済みデータから集計
        cur.execute("""
            SELECT occupies_slot, COUNT(*) as cnt
            FROM skill_complete
            WHERE name = %s AND verification_status = 'verified' AND occupies_slot IS NOT NULL
            GROUP BY occupies_slot
            ORDER BY cnt DESC
        """, (effect_name,))
        
        results = cur.fetchall()
        cur.close()
        conn.close()
        
        statistical_estimate = None
        confidence = "low"
        reason = ""
        
        if results:
            # 最多の値を採用
            most_common = results[0][0]
            count = results[0][1]
            total = sum(r[1] for r in results)
            ratio = count / total
            
            statistical_estimate = most_common
            
            if ratio >= 0.9 and total >= 5:
                confidence = "high"
                reason = f"同名バフ{total}件中{count}件({int(ratio*100)}%)が同じ値"
            elif ratio >= 0.7 and total >= 3:
                confidence = "medium"
                reason = f"同名バフ{total}件中{count}件({int(ratio*100)}%)が同じ値"
            else:
                confidence = "low"
                reason = f"同名バフ{total}件中{count}件({int(ratio*100)}%)（信頼度低）"
        
        # ルールベース推定
        rule_based_estimate = estimate_by_rules(effect_name, target, has_requirement)
        
        # 統計とルールの両方がある場合は統計を優先、片方のみなら採用
        final_estimate = statistical_estimate if statistical_estimate is not None else rule_based_estimate['value']
        
        if statistical_estimate is None and rule_based_estimate['value'] is not None:
            confidence = rule_based_estimate['confidence']
            reason = rule_based_estimate['reason']
        
        return jsonify({
            "occupies_slot": final_estimate,
            "confidence": confidence,
            "reason": reason
        })
        
    except Exception as e:
        print(f"Error in estimate_slot: {e}")
        return jsonify({"occupies_slot": None, "confidence": "low", "reason": "エラー"})

def estimate_by_rules(effect_name, target, has_requirement):
    """ルールベースでバフ枠使用を推定"""
    
    # ルール1: 「〇〇無効」「〇〇への抵抗力」系は基本的にtrue
    if '無効' in effect_name or '抵抗' in effect_name:
        return {
            'value': True,
            'confidence': 'medium',
            'reason': '無効/抵抗系は通常バフ枠使用'
        }
    
    # ルール2: 「対〇〇」系は基本的にfalse（条件付きバフ）
    if effect_name.startswith('対') or '(対' in effect_name:
        return {
            'value': False,
            'confidence': 'medium',
            'reason': '対象限定バフは枠不使用が多い'
        }
    
    # ルール3: 味方全員対象はtrue
    if target == 'ALL_ALLIES':
        return {
            'value': True,
            'confidence': 'medium',
            'reason': '味方全員対象は枠使用が多い'
        }
    
    # ルール4: 条件付き自己バフはfalse
    if target == 'SELF' and has_requirement:
        return {
            'value': False,
            'confidence': 'low',
            'reason': '条件付き自己バフは枠不使用の可能性'
        }
    
    # デフォルト: 不明
    return {
        'value': None,
        'confidence': 'low',
        'reason': 'ルールに該当せず'
    }

@app.route('/hold-alien', methods=['POST'])
def hold_alien():
    """エイリアンを保留状態にする"""
    data = request.json
    alien_id = data.get('alien_id')
    notes = data.get('notes', '')
    
    if not alien_id:
        return jsonify({"error": "alien_idが必要です"}), 400
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # 該当するalien_idの全レコードをon_holdに更新
        cur.execute("""
            UPDATE skill_complete
            SET verification_status = 'on_hold', notes = %s
            WHERE alien_id = %s
        """, (notes, alien_id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"status": "success"})
        
    except Exception as e:
        print(f"Error in hold_alien: {e}")
        if 'conn' in locals() and conn:
            conn.rollback()
            conn.close()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("サーバーを起動します。 http://127.0.0.1:5000 にアクセスしてください。")
    app.run(debug=True, port=5000)