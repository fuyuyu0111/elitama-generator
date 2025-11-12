import os
import re
import time
import requests
import psycopg2
import argparse
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
    'icn_equ_res_5_50.png': 'AW',
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
def get_detail_entries_from_page(session, page_url):
    """一覧ページから個別の詳細ページ情報（URLとアイコンURL、ID）を取得する"""
    try:
        response = session.get(page_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        base_domain = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
        detail_entries = []
        alien_table = soup.find('table', class_='data-list')

        if not alien_table:
            return []

        for row in alien_table.find_all('tr'):
            if row.find('th'):
                # ヘッダ行はスキップ
                continue

            cells = row.find_all('td')
            if len(cells) < 3:
                continue

            link_cell = cells[0]
            id_cell = cells[1]

            link_tag = link_cell.find('a', href=re.compile(r'Alien_detail'))
            if not link_tag or not link_tag.has_attr('href'):
                continue

            detail_url = urljoin(f"{base_domain}/data/", link_tag['href'])

            icon_url = None
            icon_tag = link_tag.find('img')
            if icon_tag and icon_tag.has_attr('src'):
                icon_url = urljoin(base_domain, icon_tag['src'])

            alien_id = None
            if id_cell:
                match = re.search(r'(\d+)', id_cell.get_text(strip=True))
                if match:
                    try:
                        alien_id = int(match.group(1))
                    except ValueError:
                        alien_id = None

            detail_entries.append({
                'detail_url': detail_url,
                'icon_url': icon_url,
                'id': alien_id,
            })
        
        return detail_entries
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

        # 基本情報の抽出 (セレクタを少し安定なものに修正)
        data['id'] = detail_section.find('p', text=re.compile(r'図鑑No.')).text.replace('図鑑No.', '').strip()
        data['name'] = detail_section.find('h1').text.strip()
        
        attr_p = detail_section.find(lambda tag: tag.name == 'p' and '属性' in tag.get_text())
        data['attribute'] = get_image_filename(attr_p.find('img'))

        affil_p = detail_section.find(lambda tag: tag.name == 'p' and '所属' in tag.get_text())
        data['affiliation'] = get_image_filename(affil_p.find('img'))

        common_table = detail_section.find('table', class_='data-detail-common')
        kyori_th = common_table.find('th', text='きょり')
        data['attack_range'] = get_image_filename(kyori_th.find_next_sibling('td').find('img'))
        
        hani_th = common_table.find('th', text='はんい')
        data['attack_area'] = get_image_filename(hani_th.find_next_sibling('td').find('img'))
        
        # ★★★ ここから role と types を分離するロジック ★★★
        data['role'] = None
        data['types'] = [] # typesはファイル名のリストとして初期化

        type_th = common_table.find('th', text='タイプ')
        if type_th and type_th.find_next_sibling('td'):
            all_type_icons = type_th.find_next_sibling('td').find_all('img')
            
            role_keys = {
                'icn_equ_res_5_41.png', 'icn_equ_res_5_42.png',
                'icn_equ_res_5_43.png', 'icn_equ_res_5_44.png'
            }
            
            for img in all_type_icons:
                filename = get_image_filename(img)
                if filename in role_keys:
                    # ファイル名がロールのキーと一致した場合
                    data['role'] = filename
                else:
                    # それ以外はタイプとして扱う
                    data['types'].append(filename)
        # ★★★ ここまで ★★★

        # メイン画像の取得
        image_container = detail_section.find('div', class_='detail-alien-image')
        main_image_tag = image_container.find('img') if image_container else None
        if main_image_tag and main_image_tag.has_attr('src'):
            image_src = main_image_tag['src']
            data['image_src'] = image_src
            data['image_filename'] = get_image_filename(main_image_tag)
            data['image_url'] = urljoin(url, image_src)
        else:
            data['image_src'] = None
            data['image_filename'] = None
            data['image_url'] = None

        # 個性はリストとして取得 (変更なし)
        data['skills'] = []
        skill_table = None
        for table in detail_section.find_all('table', class_='data-detail-common'):
            if table.find('th', text=re.compile(r'個性\d')):
                skill_table = table
                break
        
        if skill_table:
            for i in range(1, 4):
                skill_th = skill_table.find('th', text=f'個性{i}')
                if skill_th and skill_th.find_next_sibling('td'):
                    skill_td = skill_th.find_next_sibling('td')
                    skill_name = skill_td.find('a').text.strip()
                    
                    skill_effect_container = skill_td.find('a').find_parent('p').find_next_sibling('p')
                    if skill_effect_container:
                        raw_text = skill_effect_container.get_text(separator='\n', strip=True)
                        skill_effect = raw_text.replace('\n＜', '＜')
                        data['skills'].append({'name': skill_name, 'text': skill_effect})
        
        # ★★★ 特技データの取得 ★★★
        data['special_skill'] = None
        data['special_skill_text'] = None
        
        # 特技テーブルを検索
        special_skill_table = None
        for table in detail_section.find_all('table', class_='data-detail-common'):
            if table.find('th', text=re.compile(r'特技')):
                special_skill_table = table
                break
        
        if special_skill_table:
            special_th = special_skill_table.find('th', text=re.compile(r'特技'))
            if special_th and special_th.find_next_sibling('td'):
                special_td = special_th.find_next_sibling('td')
                # 特技名を取得（<a>, <p>, <span class="bold">の順で検索）
                special_name_elem = (special_td.find('a') or 
                                    special_td.find('span', class_='bold') or 
                                    special_td.find('p'))
                if special_name_elem:
                    data['special_skill'] = special_name_elem.text.strip()
                
                # 特技テキストを取得
                if special_name_elem:
                    special_effect_container = special_name_elem.find_parent('p')
                    if special_effect_container:
                        special_effect_container = special_effect_container.find_next_sibling('p')
                    
                    if special_effect_container:
                        raw_text = special_effect_container.get_text(separator='\n', strip=True)
                        special_effect = raw_text.replace('\n＜', '＜')
                        data['special_skill_text'] = special_effect
                else:
                    # リンクがない場合のフォールバック
                    all_p_tags = special_td.find_all('p')
                    if len(all_p_tags) > 1:
                        # 2つ目以降のpタグから取得
                        raw_text = all_p_tags[1].get_text(separator='\n', strip=True)
                        special_effect = raw_text.replace('\n＜', '＜')
                        data['special_skill_text'] = special_effect
        
        # ★★★ ステータス値の取得 ★★★
        data['hp'] = None
        data['power'] = None
        data['size'] = None
        data['motivation'] = None
        data['speed'] = None
        
        ability_table = detail_section.find('table', class_='ability')
        if ability_table:
            # たいりょく（hp）を取得 - Lv 450またはLv 420の値（最後の値）
            for tr in ability_table.find_all('tr'):
                hp_th = tr.find('th', text=re.compile(r'たいりょく'))
                if hp_th:
                    # <tr>内の最後の<td>要素を取得
                    hp_td = tr.find_all('td')
                    if hp_td:
                        hp_text = hp_td[-1].get_text(separator='\n', strip=True)
                        hp_values = [v.strip() for v in hp_text.split('\n') if v.strip()]
                        if hp_values:
                            # 最後の値（Lv 450またはLv 420の値）を取得
                            try:
                                data['hp'] = int(hp_values[-1])
                            except ValueError:
                                pass
                    break
            
            # つよさ（power）を取得 - Lv 450またはLv 420の値（最後の値）
            for tr in ability_table.find_all('tr'):
                power_th = tr.find('th', text=re.compile(r'つよさ'))
                if power_th:
                    # <tr>内の最後の<td>要素を取得
                    power_td = tr.find_all('td')
                    if power_td:
                        power_text = power_td[-1].get_text(separator='\n', strip=True)
                        power_values = [v.strip() for v in power_text.split('\n') if v.strip()]
                        if power_values:
                            # 最後の値（Lv 450またはLv 420の値）を取得
                            try:
                                data['power'] = int(power_values[-1])
                            except ValueError:
                                pass
                    break
            
            # ごはんセクションからおおきさ、やるき、いどうを取得
            for tr in ability_table.find_all('tr'):
                gohan_th = tr.find('th', text=re.compile(r'ごはん'))
                if gohan_th:
                    # <tr>内の最後の<td>要素を取得
                    gohan_td = tr.find_all('td')
                    if gohan_td:
                        gohan_text = gohan_td[-1].get_text(separator='\n', strip=True)
                        gohan_values = [v.strip() for v in gohan_text.split('\n') if v.strip()]
                        if len(gohan_values) >= 3:
                            try:
                                data['size'] = int(gohan_values[0])  # おおきさ
                                data['motivation'] = int(gohan_values[1])  # やるき
                                data['speed'] = int(gohan_values[2])  # いどう
                            except ValueError:
                                pass
                    break
        
        return data

    except requests.exceptions.RequestException as e:
        print(f"  -> 詳細ページの取得に失敗: {url}, {e}")
        return None
    except Exception as e:
        print(f"  -> 解析中に予期せぬエラー: {url}, {e}")
        return None
    
# --- データベース書き込み関数 ---
def upsert_alien_to_db(conn, data):
    """スクレイピングしたデータをDBに書き込む (存在すれば更新、なければ追加)"""
    
    # NBSPなどの特殊スペースを通常のスペースに置換
    def normalize_value(value):
        if isinstance(value, str):
            return value.replace('\xa0', ' ').strip()
        return value

    # データベースの列名とデータ型に合わせて値を準備
    db_data = {
        'id': int(data['id']) if data.get('id') else None,
        'name': normalize_value(data.get('name')),
        'attribute': CONVERSION_MAP.get(data.get('attribute')),
        'affiliation': CONVERSION_MAP.get(data.get('affiliation')),
        'attack_range': CONVERSION_MAP.get(data.get('attack_range')),
        'attack_area': CONVERSION_MAP.get(data.get('attack_area')),
        # ★★★ roleを追加 ★★★
        'role': int(CONVERSION_MAP.get(data.get('role'))) if data.get('role') else None,
    }
    
    # タイプをtype_1, type_2...に割り振り
    types = [CONVERSION_MAP.get(fname) for fname in data.get('types', []) if fname in CONVERSION_MAP]
    for i in range(4):
        db_data[f'type_{i+1}'] = types[i] if i < len(types) else None
        
    # 個性をskill_no1, skill_text1...に割り振り
    skills = data.get('skills', [])
    for i in range(3):
        if i < len(skills):
            db_data[f'skill_no{i+1}'] = normalize_value(skills[i].get('name'))
            db_data[f'skill_text{i+1}'] = normalize_value(skills[i].get('text'))
        else:
            db_data[f'skill_no{i+1}'] = None
            db_data[f'skill_text{i+1}'] = None

    # ★★★ 特技データを追加 ★★★
    db_data['S_Skill'] = normalize_value(data.get('special_skill') or None)
    db_data['S_Skill_text'] = normalize_value(data.get('special_skill_text') or None)

    # ★★★ ステータス値を追加 ★★★
    db_data['hp'] = data.get('hp')
    db_data['power'] = data.get('power')
    db_data['motivation'] = data.get('motivation')
    db_data['size'] = data.get('size')
    db_data['speed'] = data.get('speed')

    # ★★★ INSERT/UPDATE文で使う列の順番に role、特技、ステータス値を追加 ★★★
    column_mappings = [
        ('id', 'id'),
        ('name', 'name'),
        ('attribute', 'attribute'),
        ('affiliation', 'affiliation'),
        ('attack_range', 'attack_range'),
        ('attack_area', 'attack_area'),
        ('type_1', 'type_1'),
        ('type_2', 'type_2'),
        ('type_3', 'type_3'),
        ('type_4', 'type_4'),
        ('role', 'role'),
        ('skill_no1', 'skill_no1'),
        ('skill_text1', 'skill_text1'),
        ('skill_no2', 'skill_no2'),
        ('skill_text2', 'skill_text2'),
        ('skill_no3', 'skill_no3'),
        ('skill_text3', 'skill_text3'),
        ('"S_Skill"', 'S_Skill'),  # 特技データ（大文字列はクォートが必要）
        ('"S_Skill_text"', 'S_Skill_text'),
        ('hp', 'hp'),
        ('power', 'power'),
        ('motivation', 'motivation'),
        ('size', 'size'),
        ('speed', 'speed')
    ]
    columns = [col for col, _ in column_mappings]
    values = [db_data.get(key) for _, key in column_mappings]

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM alien WHERE id = %s", (db_data['id'],))
        exists = cur.fetchone()

        if exists:
            update_cols = [f"{col} = %s" for col in columns[1:]]
            sql = f"UPDATE alien SET {', '.join(update_cols)} WHERE id = %s"
            update_values = values[1:] + [values[0]]
            cur.execute(sql, update_values)
            print(f"  -> 図鑑No.{db_data['id']} '{db_data['name']}' のデータを更新しました。")
        else:
            placeholders = ', '.join(['%s'] * len(columns))
            sql = f"INSERT INTO alien ({', '.join(columns)}) VALUES ({placeholders})"
            cur.execute(sql, values)
            print(f"  -> 図鑑No.{db_data['id']} '{db_data['name']}' を新規追加しました。")

# --- メインの実行部分 ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='エイリアンデータをスクレイピングしてDBに保存')
    parser.add_argument('--url', type=str, help='スクレイピング開始URL（環境変数SCRAPING_BASE_URLでも指定可能）')
    args = parser.parse_args()
    
    # URL取得: コマンドライン引数 > 環境変数
    input_url = args.url or os.environ.get('SCRAPING_BASE_URL')
    if not input_url or not input_url.strip():
        print("エラー: URLが指定されていません。")
        print("使用方法:")
        print("  1. コマンドライン引数: python full_scraper.py --url <URL>")
        print("  2. 環境変数: SCRAPING_BASE_URL=<URL> python full_scraper.py")
        exit(1)

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
        detail_entry_list = []

        for page in range(1, total_pages + 1):
            page_url = f"{list_page_base_url}{page}"
            print(f"リストの {page} / {total_pages} ページ目をスキャン中...")
            
            # 1ページ目は既に取得済みなので再利用、2ページ目以降は新規取得
            if page == 1:
                entries_on_page = get_detail_entries_from_page(session, first_page_url)
            else:
                entries_on_page = get_detail_entries_from_page(session, page_url)
                time.sleep(1)
            
            if not entries_on_page:
                print(f"  -> {page} ページからはURLが取得できませんでした。スキップします。")
                continue
            
            detail_entry_list.extend(entries_on_page)

        if detail_entry_list:
            # detail_urlをキーに重複を排除しつつ、ID昇順にソート
            unique_entries = {}
            for entry in detail_entry_list:
                detail_url = entry['detail_url']
                if detail_url not in unique_entries:
                    unique_entries[detail_url] = entry
            all_detail_entries = sorted(
                unique_entries.values(),
                key=lambda item: item.get('id') or 0
            )
        else:
            all_detail_entries = []
        
        print(f"\n合計 {len(all_detail_entries)} 件のユニークなURLを取得しました。")

        if all_detail_entries:
            print("\n--- ステップ2: 詳細をスクレイピングし、DBに書き込み中 ---")
            conn = None
            try:
                conn = get_db_connection()
                
                for i, entry in enumerate(all_detail_entries, 1):
                    url = entry['detail_url']
                    print(f"[{i}/{len(all_detail_entries)}] {url} を処理中...")
                    
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