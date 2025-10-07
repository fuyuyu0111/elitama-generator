import os
import re
import time
import requests
import psycopg2
from bs4 import BeautifulSoup
from pprint import pprint
from urllib.parse import urljoin, urlparse, parse_qs

# --- 定数定義 ---
# 変換マップ
CONVERSION_MAP = {
    # attribute
    'icn_equ_res_1_1.png': 1, 'icn_equ_res_1_2.png': 2,
    'icn_equ_res_1_3.png': 3, 'icn_equ_res_1_4.png': 4,
    # affiliation
    'icn_equ_res_2_0.png': 1, 'icn_equ_res_2_1.png': 2,
    'icn_equ_res_2_2.png': 3, 'icn_equ_res_2_3.png': 4,
    'icn_equ_res_2_5.png': 5,
    # attack_range (きょり)
    'icn_equ_res_4_0.png': 1, 'icn_equ_res_4_1.png': 2, 'icn_equ_res_4_2.png': 3,
    # attack_area (はんい)
    'icn_equ_res_3_0.png': 1, 'icn_equ_res_3_1.png': 2,
    # types
    'icn_equ_res_5_6.png': 'A', 'icn_equ_res_5_18.png': 'B',
    'icn_equ_res_5_21.png': 'C', 'icn_equ_res_5_22.png': 'D',
    'icn_equ_res_5_26.png': 'E', 'icn_equ_res_5_27.png': 'F',
    'icn_equ_res_5_30.png': 'G', 'icn_equ_res_5_31.png': 'H',
    'icn_equ_res_5_38.png': 'I', 'icn_equ_res_5_23.png': 'J',
    'icn_equ_res_5_24.png': 'K', 'icn_equ_res_5_25.png': 'L',
    'icn_equ_res_5_34.png': 'M', 'icn_equ_res_5_46.png': 'N',
    'icn_equ_res_5_47.png': 'O', 'icn_equ_res_5_48.png': 'P',
    'icn_equ_res_5_49.png': 'Q', 
    'icn_equ_res_5_1.png': 'AA',
    'icn_equ_res_5_2.png': 'AB', 'icn_equ_res_5_17.png': 'AC',
    'icn_equ_res_5_5.png': 'AD', 'icn_equ_res_5_32.png': 'AH',
    'icn_equ_res_5_33.png': 'AI', 'icn_equ_res_5_35.png': 'AJ',
    'icn_equ_res_5_39.png': 'AK', 'icn_equ_res_5_40.png': 'AL',
    'icn_equ_res_5_7.png': 'AM', 'icn_equ_res_5_8.png': 'AN',
    'icn_equ_res_5_9.png': 'AO', 'icn_equ_res_5_10.png': 'AP',
    'icn_equ_res_5_11.png': 'AQ', 'icn_equ_res_5_12.png': 'AR',
    'icn_equ_res_5_13.png': 'AS', 'icn_equ_res_5_14.png': 'AT',
    'icn_equ_res_5_15.png': 'AU', 'icn_equ_res_5_16.png': 'AV',
    # role
    'icn_equ_res_5_41.png': '1', 'icn_equ_res_5_42.png': '2',
    'icn_equ_res_5_43.png': '3', 'icn_equ_res_5_44.png': '4',
}


# --- ヘルパー関数 ---
def get_db_connection():
    """環境変数から接続情報を読み取り、データベース接続を返す"""
    conn_str = os.environ.get('DATABASE_URL')
    if not conn_str:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(conn_str, sslmode='require')

def get_image_filename(img_tag):
    """imgタグから画像ファイル名を取得する"""
    if not img_tag or not img_tag.has_attr('src'):
        return None
    return img_tag['src'].split('/')[-1]

def get_total_pages(soup):
    """
    ページネーション用のselect要素から総ページ数を取得する関数
    """
    try:
        pagination_select = soup.find('select', class_='js-pagenation-select')
        if not pagination_select:
            return 1
        
        last_option_text = pagination_select.find_all('option')[-1].text
        
        match = re.search(r'(\d+)\s*ページ', last_option_text)
        if match:
            return int(match.group(1))
        
        return 1
    except (IndexError, AttributeError):
        return 1

# --- スクレイピング用関数 ---
def get_detail_urls_from_page(session, page_url):
    """一覧ページから個別の詳細ページのURLリストを取得する"""
    try:
        response = session.get(page_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        detail_urls = set()
        base_url = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/data/"
        alien_table = soup.find('table', class_='data-list')

        if not alien_table:
            return []

        for a_tag in alien_table.find_all('a'):
            if a_tag.has_attr('href') and 'Alien_detail' in a_tag['href']:
                full_url = urljoin(base_url, a_tag['href'])
                detail_urls.add(full_url)
        
        return list(detail_urls)
    except requests.exceptions.RequestException as e:
        print(f"  -> ページ {page_url} の取得に失敗: {e}")
        return []

def scrape_alien_data(session, url):
    """個別の詳細ページからエイリアンの全データを取得する"""
    try:
        time.sleep(1)
        response = session.get(url)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        data = {}
        
        detail_section = soup.find('section', id='alien-detail')
        if not detail_section:
            raise ValueError("ID 'alien-detail' を持つセクションが見つかりません。")

        # 基本情報の抽出 (warningが出ないように string= に修正)
        id_tag = detail_section.find('p', string=re.compile(r'図鑑No.'))
        if id_tag:
            data['id'] = id_tag.text.replace('図鑑No.', '').strip()
        
        data['name'] = detail_section.find('h1').text.strip()
        
        attr_p = detail_section.find(lambda tag: tag.name == 'p' and '属性' in tag.get_text())
        data['attribute'] = get_image_filename(attr_p.find('img'))

        affil_p = detail_section.find(lambda tag: tag.name == 'p' and '所属' in tag.get_text())
        data['affiliation'] = get_image_filename(affil_p.find('img'))

        # data-detail-common テーブルを一度だけ取得
        all_tables = detail_section.find_all('table', class_='data-detail-common')
        
        # 攻撃範囲などの情報を取得
        common_table = all_tables[0] if all_tables else None
        if common_table:
            kyori_th = common_table.find('th', string='きょり')
            if kyori_th:
                data['attack_range'] = get_image_filename(kyori_th.find_next_sibling('td').find('img'))
            
            hani_th = common_table.find('th', string='はんい')
            if hani_th:
                data['attack_area'] = get_image_filename(hani_th.find_next_sibling('td').find('img'))
            
            data['role'] = None
            data['types'] = []
            type_th = common_table.find('th', string='タイプ')
            if type_th and type_th.find_next_sibling('td'):
                all_type_icons = type_th.find_next_sibling('td').find_all('img')
                role_keys = {'icn_equ_res_5_41.png', 'icn_equ_res_5_42.png', 'icn_equ_res_5_43.png', 'icn_equ_res_5_44.png'}
                for img in all_type_icons:
                    filename = get_image_filename(img)
                    if filename in role_keys:
                        data['role'] = filename
                    else:
                        data['types'].append(filename)

        # 個性と特技の情報を取得
        data['skills'] = []
        data['S_Skill'] = None
        data['S_Skill_text'] = None
        
        skill_table = None
        for table in all_tables:
            # <th>に"個性"か"特技"が含まれるテーブルを探す
            if table.find('th', string=re.compile(r'(個性|特技)')):
                skill_table = table
                break

        if skill_table:
            # 個性の取得
            for i in range(1, 4):
                skill_th = skill_table.find('th', string=f'個性{i}')
                if skill_th and skill_th.find_next_sibling('td'):
                    skill_td = skill_th.find_next_sibling('td')
                    skill_name_tag = skill_td.find('a')
                    skill_effect_container = skill_name_tag.find_parent('p').find_next_sibling('p') if skill_name_tag else None
                    if skill_name_tag and skill_effect_container:
                        skill_name = skill_name_tag.text.strip()
                        raw_text = skill_effect_container.get_text(separator='\n', strip=True)
                        skill_effect = raw_text.replace('\n＜', '＜')
                        data['skills'].append({'name': skill_name, 'text': skill_effect})
            
            # 特技の取得
            s_skill_th = skill_table.find('th', string='特技')
            if s_skill_th and s_skill_th.find_next_sibling('td'):
                s_skill_td = s_skill_th.find_next_sibling('td')
                s_skill_name_tag = s_skill_td.find('span', class_='bold')
                if s_skill_name_tag:
                    data['S_Skill'] = s_skill_name_tag.text.strip()
                    s_skill_text_tag = s_skill_name_tag.find_parent('p').find_next_sibling('p')
                    if s_skill_text_tag:
                        data['S_Skill_text'] = s_skill_text_tag.text.strip()

        return data

    except requests.exceptions.RequestException as e:
        print(f"  -> 詳細ページの取得に失敗: {url}, {e}")
        return None
    except Exception as e:
        print(f"  -> 解析中に予期せぬエラー: {url}, {e}")
        return None
    
# --- データベース書き込み関数 ---
def upsert_alien_to_db(conn, data):
    """スクレイピングしたデータをDBに書き込む (存在すれば更新、なければ追加)"""
    
    db_data = {
        'id': int(data['id']) if data.get('id') else None,
        'name': data.get('name'),
        'attribute': CONVERSION_MAP.get(data.get('attribute')),
        'affiliation': CONVERSION_MAP.get(data.get('affiliation')),
        'attack_range': CONVERSION_MAP.get(data.get('attack_range')),
        'attack_area': CONVERSION_MAP.get(data.get('attack_area')),
        'role': int(CONVERSION_MAP.get(data.get('role'))) if data.get('role') else None,
        'S_Skill': data.get('S_Skill'),
        'S_Skill_text': data.get('S_Skill_text'),
    }
    
    types = [CONVERSION_MAP.get(fname) for fname in data.get('types', []) if fname in CONVERSION_MAP]
    for i in range(4):
        db_data[f'type_{i+1}'] = types[i] if i < len(types) else None
        
    skills = data.get('skills', [])
    for i in range(3):
        if i < len(skills):
            db_data[f'skill_no{i+1}'] = skills[i].get('name')
            db_data[f'skill_text{i+1}'] = skills[i].get('text')
        else:
            db_data[f'skill_no{i+1}'] = None
            db_data[f'skill_text{i+1}'] = None

    columns = [
        'id', 'name', 'attribute', 'affiliation', 'attack_range', 'attack_area',
        'type_1', 'type_2', 'type_3', 'type_4', 'role', 'skill_no1', 'skill_text1',
        'skill_no2', 'skill_text2', 'skill_no3', 'skill_text3',
        'S_Skill', 'S_Skill_text'
    ]
    values = [db_data.get(col) for col in columns]

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM alien WHERE id = %s", (db_data['id'],))
        exists = cur.fetchone()

        if exists:
            # ↓↓↓↓ 修正箇所(UPDATE) ↓↓↓↓
            # 各列名をダブルクォーテーションで囲む
            update_cols = [f'"{col}" = %s' for col in columns[1:]]
            sql = f'UPDATE alien SET {", ".join(update_cols)} WHERE id = %s'
            # ↑↑↑↑ 修正箇所(UPDATE) ↑↑↑↑
            update_values = values[1:] + [values[0]]
            cur.execute(sql, update_values)
            print(f"  -> 図鑑No.{db_data['id']} '{db_data['name']}' のデータを更新しました。")
        else:
            # ↓↓↓↓ 修正箇所(INSERT) ↓↓↓↓
            # 各列名をダブルクォーテーションで囲む
            quoted_columns = f'"{ '", "'.join(columns) }"'
            placeholders = ', '.join(['%s'] * len(columns))
            sql = f'INSERT INTO alien ({quoted_columns}) VALUES ({placeholders})'
            # ↑↑↑↑ 修正箇所(INSERT) ↑↑↑↑
            cur.execute(sql, values)
            print(f"  -> 図鑑No.{db_data['id']} '{db_data['name']}' を新規追加しました。")
            
# --- メインの実行部分 ---
if __name__ == '__main__':
    input_url = input("収集を開始したいエイリアン一覧ページのURLを貼り付けてEnterを押してください:\n> ")
    if not input_url.strip():
        print("URLが入力されませんでした。処理を終了します。")
        exit()

    parsed_url = urlparse(input_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?"
    query_params = parse_qs(parsed_url.query)
    query_params.pop('page', None)
    base_query = '&'.join([f"{k}={v[0]}" for k, v in query_params.items()])
    list_page_base_url = f"{base_url}{base_query}&page="

    all_detail_urls = []
    
    with requests.Session() as session:
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

        print("\n--- ステップ1: 全エイリアンの詳細URLを収集中 ---")
        
        # まず1ページ目にアクセスして総ページ数を取得
        first_page_url = f"{list_page_base_url}1"
        print("総ページ数を取得するために1ページ目にアクセスします...")
        try:
            response = session.get(first_page_url)
            response.raise_for_status()
            first_page_soup = BeautifulSoup(response.text, 'html.parser')
            total_pages = get_total_pages(first_page_soup)
            print(f"総ページ数: {total_pages} を確認しました。")
        except requests.exceptions.RequestException as e:
            print(f"エラー: 1ページ目の取得に失敗しました。処理を中断します。\n{e}")
            exit()

        # 確定したページ数でループを実行
        for page in range(1, total_pages + 1):
            page_url = f"{list_page_base_url}{page}"
            print(f"リストの {page} / {total_pages} ページ目をスキャン中...")
            
            # 1ページ目は既に取得済みなので再利用、2ページ目以降は新規取得
            if page == 1:
                urls_on_page = get_detail_urls_from_page(session, first_page_url)
            else:
                urls_on_page = get_detail_urls_from_page(session, page_url)
                time.sleep(1)
            
            if not urls_on_page:
                print(f"  -> {page} ページからはURLが取得できませんでした。スキップします。")
                continue

            all_detail_urls.extend(urls_on_page)
        
        all_detail_urls = sorted(list(set(all_detail_urls)))
        print(f"\n合計 {len(all_detail_urls)} 件のユニークなURLを取得しました。")

        if all_detail_urls:
            print("\n--- ステップ2: 詳細をスクレイピングし、DBに書き込み中 ---")
            conn = None
            try:
                conn = get_db_connection()
                
                for i, url in enumerate(all_detail_urls, 1):
                    print(f"[{i}/{len(all_detail_urls)}] {url} を処理中...")
                    
                    alien_data = scrape_alien_data(session, url)
                    
                    if alien_data and alien_data.get('id'):
                        upsert_alien_to_db(conn, alien_data)
                    else:
                        print("  -> データ取得に失敗、またはID不明のためスキップします。")
                    
                    time.sleep(1)
                
                conn.commit()
                print("\nデータベースへの全ての変更をコミットしました。")

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