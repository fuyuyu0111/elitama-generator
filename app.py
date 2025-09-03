import os
import psycopg2
from psycopg2.extras import DictCursor
import json
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- データベース接続部分をPostgreSQL用に変更 ---
def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    conn = psycopg2.connect(conn_str, cursor_factory=DictCursor)
    return conn
# ----------------------------------------------

@app.route('/')
def index():
    conn = get_db_connection()
    cur = conn.cursor() # 作業員（カーソル）を呼び出す
    query = """
        SELECT
            a.id, a.name,
            a.attribute, a.affiliation, a.attack_area, a.attack_range,
            a.type_1, a.type_2, a.type_3,
            attr.name AS attribute_name,
            aff.name AS affiliation_name
        FROM
            alien AS a
        LEFT JOIN attributes_a AS attr ON a.attribute = attr.id
        LEFT JOIN affiliations_b AS aff ON a.affiliation = aff.id
    """
    cur.execute(query) # 作業員に命令する
    aliens = cur.fetchall() # 結果を受け取る
    cur.close() # 作業員を帰す
    conn.close()
    
    # 全要求データを取得する部分も同様に修正
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM skill')
    all_requirements_raw = cur.fetchall()
    cur.close()
    conn.close()

    all_requirements = {}
    for req in all_requirements_raw:
        alien_id_str = str(req['id'])
        if alien_id_str not in all_requirements:
            all_requirements[alien_id_str] = []
        all_requirements[alien_id_str].append(dict(req))
    
    return render_template(
        'index.html',
        aliens=aliens,
        all_requirements_json=json.dumps(all_requirements)
    )

@app.route('/check', methods=['POST'])
def check_party():
    selected_ids = request.json.get('ids', [])
    if not selected_ids:
        return jsonify([])

    conn = get_db_connection()
    cur = conn.cursor() # 作業員を呼び出す

    # PostgreSQLではプレースホルダが ? ではなく %s になります
    placeholders_pg = ','.join(['%s'] * len(selected_ids))
    cur.execute(f'SELECT * FROM alien WHERE id IN ({placeholders_pg})', selected_ids)
    party_members = cur.fetchall()

    # パーティの構成を集計
    party_composition = {
        'a': {}, 'b': {}, 'c': {}, 'd': {}, 'e': {}
    }
    for member in party_members:
        if member['attribute']: party_composition['a'][str(member['attribute'])] = party_composition['a'].get(str(member['attribute']), 0) + 1
        if member['affiliation']: party_composition['b'][str(member['affiliation'])] = party_composition['b'].get(str(member['affiliation']), 0) + 1
        if member['attack_area']: party_composition['c'][str(member['attack_area'])] = party_composition['c'].get(str(member['attack_area']), 0) + 1
        if member['attack_range']: party_composition['d'][str(member['attack_range'])] = party_composition['d'].get(str(member['attack_range']), 0) + 1
        for type_col in ['type_1', 'type_2', 'type_3']:
            if member[type_col]:
                type_code = member[type_col]
                party_composition['e'][type_code] = party_composition['e'].get(type_code, 0) + 1
    
    final_results = []
    for member in party_members:
        alien_id = member['id']
        
        base_info = {
            'a': member['attribute'], 'b': member['affiliation'],
            'c': member['attack_area'], 'd': member['attack_range'],
            'e': [t for t in [member['type_1'], member['type_2'], member['type_3']] if t]
        }

        requirements_list = []
        cur.execute('SELECT * FROM skill WHERE id = %s ORDER BY skill_number', (alien_id,))
        requirements = cur.fetchall()
        for req in requirements:
            cond_type = req['condition_type']
            cond_value = str(req['condition_value'])
            cond_count = req['condition_count']
            
            actual_count = party_composition.get(cond_type, {}).get(cond_value, 0)
            
            is_self_condition = False
            if cond_type == 'a' and str(member['attribute']) == cond_value: is_self_condition = True
            if cond_type == 'b' and str(member['affiliation']) == cond_value: is_self_condition = True
            if cond_type == 'e' and cond_value in [member['type_1'], member['type_2'], member['type_3']]: is_self_condition = True
            
            if is_self_condition:
                actual_count -= 1

            is_satisfied = actual_count >= cond_count
            
            requirements_list.append({
                "skill_number": req['skill_number'], "condition_type": cond_type,
                "condition_value": cond_value, "condition_count": cond_count,
                "is_satisfied": is_satisfied
            })

        final_results.append({
            "id": alien_id, "name": member['name'],
            "base_info": base_info, "requirements": requirements_list
        })
    
    cur.close()
    conn.close()
    return jsonify(final_results)

if __name__ == '__main__':
    app.run(debug=False)