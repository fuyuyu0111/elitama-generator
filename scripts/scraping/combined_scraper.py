"""
スクレイピングと画像取得を統合したスクリプト
新規・更新データのみを処理する機能付き
"""

import os
import re
import time
import requests
import psycopg2
import argparse
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from typing import List, Set, Dict, Optional, Tuple

# full_scraper.pyから必要な関数と定数をインポート
# 同じディレクトリからインポート
import importlib.util

_script_dir = os.path.dirname(os.path.abspath(__file__))
_full_scraper_path = os.path.join(_script_dir, 'full_scraper.py')

spec = importlib.util.spec_from_file_location("full_scraper", _full_scraper_path)
full_scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full_scraper)

CONVERSION_MAP = full_scraper.CONVERSION_MAP
get_db_connection = full_scraper.get_db_connection
get_image_filename = full_scraper.get_image_filename
get_total_pages = full_scraper.get_total_pages
get_detail_entries_from_page = full_scraper.get_detail_entries_from_page
scrape_alien_data = full_scraper.scrape_alien_data
upsert_alien_to_db = full_scraper.upsert_alien_to_db

# 画像ダウンロード機能（統合済み）
# パスの解決
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
SAVE_DIRECTORY = os.path.join(PROJECT_ROOT, 'static', 'images')


def download_image(session, image_url, save_path):
    """指定されたURLから画像をダウンロードして保存する"""
    try:
        response = session.get(image_url, stream=True)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"      -> 画像ダウンロード失敗: {e}")
        return False


def get_existing_alien_ids(conn) -> Set[int]:
    """データベースから既存のエイリアンIDを取得"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM alien")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"既存ID取得エラー: {e}")
        return set()


def get_latest_alien_id_from_db(conn) -> Optional[int]:
    """データベースから最新のエイリアンIDを取得"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM alien")
            result = cur.fetchone()
            return result[0] if result and result[0] else None
    except Exception as e:
        print(f"最新ID取得エラー: {e}")
        return None


def get_latest_alien_id_from_last_page(session, list_page_base_url: str, total_pages: int) -> Optional[int]:
    """最後のページから最新のエイリアンIDを取得"""
    try:
        last_page_url = f"{list_page_base_url}{total_pages}"
        print(f"最後のページ（{total_pages}ページ目）から最新エイリアンIDを取得中...")
        response = session.get(last_page_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        alien_table = soup.find('table', class_='data-list')
        if not alien_table:
            return None
        
        max_id = 0
        for row in alien_table.find_all('tr'):
            if row.find('th'):
                continue
            cells = row.find_all('td')
            if len(cells) >= 2:
                no_text = cells[1].text.strip()
                try:
                    alien_id = int(no_text.replace('No.', '').strip())
                    max_id = max(max_id, alien_id)
                except ValueError:
                    continue
        
        return max_id if max_id > 0 else None
    except Exception as e:
        print(f"最新ID取得エラー: {e}")
        return None


def scrape_new_aliens_reverse_order(
    session,
    conn,
    list_page_base_url: str,
    total_pages: int,
    website_latest_id: int,
    db_latest_id: int,
    skip_images: bool = False
) -> Tuple[int, int, List[int], int]:
    """最新から逆順に新キャラをスクレイピング（一覧ページの並びを基準に処理）"""
    new_count = 0
    updated_count = 0
    new_alien_ids = []
    images_downloaded_total = 0
    image_url_map: Dict[int, Optional[str]] = {}
    
    print(f"\n--- 逆順スクレイピング開始 ---")
    print(f"データベース最新ID: {db_latest_id}")
    print(f"サイト最新ID: {website_latest_id}")
    
    if website_latest_id <= db_latest_id:
        print("新しいエイリアンはありません。")
        return (0, 0, [], 0)
    
    stop_scraping = False
    
    for page in range(total_pages, 0, -1):
        page_url = f"{list_page_base_url}{page}"
        entries = get_detail_entries_from_page(session, page_url)
        if not entries:
            continue
        
        # 図鑑No.の大きい順に処理する（一覧は昇順のため逆順にする）
        for entry in sorted(entries, key=lambda e: e.get('id') or 0, reverse=True):
            alien_id = entry.get('id')
            if not alien_id:
                continue
            
            if alien_id <= db_latest_id:
                stop_scraping = True
                break
            
            detail_url = entry['detail_url']
            icon_url = entry.get('icon_url')
            
            print(f"エイリアンNo.{alien_id} をチェック中...")
            alien_data = scrape_alien_data(session, detail_url)
            
            if not alien_data or not alien_data.get('id'):
                print("  -> データ取得に失敗、またはID不明のためスキップします。")
                continue
            
            scraped_id = int(alien_data['id'])
            if scraped_id != alien_id:
                print(f"  -> 取得したID({scraped_id})が一覧のID({alien_id})と一致しません。スキップします。")
                continue
            
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM alien WHERE id = %s", (alien_id,))
                existed = cur.fetchone() is not None
            
            upsert_alien_to_db(conn, alien_data)
            conn.commit()
            
            if icon_url:
                save_filepath = os.path.join(SAVE_DIRECTORY, f"{alien_id}.png")
                if not os.path.exists(save_filepath):
                    image_url_map[alien_id] = icon_url
            
            if existed:
                updated_count += 1
                print(f"  -> エイリアンNo.{alien_id} を更新しました")
            else:
                new_count += 1
                new_alien_ids.append(alien_id)
                print(f"  -> エイリアンNo.{alien_id} を新規追加しました")
            
            time.sleep(1)
        
        if stop_scraping:
            break
    
    images_downloaded_total = 0
    if not skip_images:
        images_downloaded_total = download_images_for_new_aliens(session, {k: v for k, v in image_url_map.items() if v})
    return (new_count, updated_count, new_alien_ids, images_downloaded_total)


def scrape_images_for_aliens(session, base_url_domain, alien_ids: List[int]) -> int:
    """指定されたエイリアンIDの画像をダウンロード"""
    if not alien_ids:
        return 0
    
    # 保存先フォルダが存在しない場合は作成
    if not os.path.exists(SAVE_DIRECTORY):
        os.makedirs(SAVE_DIRECTORY)
        print(f"保存フォルダ '{SAVE_DIRECTORY}' を作成しました。")
    
    downloaded_count = 0
    print(f"\n--- 画像ダウンロード開始（{len(alien_ids)}件）---")
    
    # 一覧ページから画像URLを取得する必要があるため、詳細ページをスクレイピングする際に画像URLも保存するか
    # または一覧ページから画像を取得する必要がある
    # ここでは、詳細ページをスクレイピングした後、画像URLを取得してダウンロードする想定
    # ただし、full_scraper.pyのscrape_alien_dataでは画像URLを取得していないため、
    # 画像は一覧ページから取得する必要がある
    
    # この実装では、一覧ページから画像を取得する方法を使う
    # 実際の画像URLは一覧ページの構造に依存するため、ここでは簡易実装とする
    
    return downloaded_count


def download_images_for_new_aliens(
    session,
    image_url_map: Dict[int, str]
) -> int:
    """新規エイリアンの画像をダウンロード（詳細ページで取得したURLを利用）"""
    if not image_url_map:
        return 0
    
    downloaded_count = 0
    if not os.path.exists(SAVE_DIRECTORY):
        os.makedirs(SAVE_DIRECTORY)
        print(f"保存フォルダ '{SAVE_DIRECTORY}' を作成しました。")
    
    print(f"\n--- 新規エイリアン画像ダウンロード開始（{len(image_url_map)}件）---")

    for alien_id, image_url in image_url_map.items():
        save_filename = f"{alien_id}.png"
        save_filepath = os.path.join(SAVE_DIRECTORY, save_filename)
        
        # 既に存在する場合はスキップ
        if os.path.exists(save_filepath):
            print(f"  -> 図鑑No.{alien_id} の画像は既に存在します。スキップします。")
            continue
        
        print(f"  -> 図鑑No.{alien_id} の画像をダウンロード中...")
        if download_image(session, image_url, save_filepath):
            downloaded_count += 1
            # 保存後の確認
            if os.path.exists(save_filepath):
                file_size = os.path.getsize(save_filepath)
                print(f"    -> 完了: {save_filename} (サイズ: {file_size} bytes, パス: {save_filepath})")
            else:
                print(f"    -> 警告: {save_filename} のダウンロードは成功したが、ファイルが存在しません")
        time.sleep(0.5)  # サーバー負荷軽減
    
    return downloaded_count


def scrape_specific_aliens(
    session,
    conn,
    list_page_base_url: str,
    total_pages: int,
    target_ids: List[int],
    skip_images: bool = False
) -> Tuple[int, int, List[int], int]:
    """指定IDのエイリアンのみスクレイピング"""
    if not target_ids:
        return (0, 0, [], 0)
    
    target_set = set(target_ids)
    detail_map: Dict[int, Dict[str, Optional[str]]] = {}
    
    print(f"\n--- 指定IDスクレイピング開始 ({len(target_ids)}件) ---")
    for page in range(1, total_pages + 1):
        if not target_set:
            break
        page_url = f"{list_page_base_url}{page}"
        entries = get_detail_entries_from_page(session, page_url)
        if not entries:
            continue
        
        for entry in entries:
            alien_id = entry.get('id')
            if alien_id in target_set:
                detail_map[alien_id] = entry
                target_set.remove(alien_id)
        time.sleep(1)
    
    if target_set:
        print(f"  -> 警告: 以下のIDは一覧から見つかりませんでした: {sorted(target_set)}")
    
    new_count = 0
    updated_count = 0
    new_alien_ids: List[int] = []
    image_url_map: Dict[int, str] = {}
    
    for alien_id in sorted(detail_map.keys()):
        entry = detail_map[alien_id]
        detail_url = entry.get('detail_url')
        icon_url = entry.get('icon_url')
        
        if not detail_url:
            print(f"  -> エイリアンNo.{alien_id} の詳細URLが不明なためスキップします。")
            continue
        
        print(f"エイリアンNo.{alien_id} を取得中...")
        alien_data = scrape_alien_data(session, detail_url)
        if not alien_data or not alien_data.get('id'):
            print("  -> データ取得に失敗、またはID不明のためスキップします。")
            continue
        
        scraped_id = int(alien_data['id'])
        if scraped_id != alien_id:
            print(f"  -> 取得したID({scraped_id})が指定したID({alien_id})と一致しません。スキップします。")
            continue
        
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM alien WHERE id = %s", (alien_id,))
            existed = cur.fetchone() is not None
        
        upsert_alien_to_db(conn, alien_data)
        conn.commit()
        
        if not skip_images and icon_url:
            save_filepath = os.path.join(SAVE_DIRECTORY, f"{alien_id}.png")
            if not os.path.exists(save_filepath):
                image_url_map[alien_id] = icon_url
        
        if existed:
            updated_count += 1
            print(f"  -> エイリアンNo.{alien_id} を更新しました")
        else:
            new_count += 1
            new_alien_ids.append(alien_id)
            print(f"  -> エイリアンNo.{alien_id} を新規追加しました")
        
        time.sleep(1)
    
    images_downloaded_total = 0
    if not skip_images and image_url_map:
        images_downloaded_total = download_images_for_new_aliens(session, image_url_map)
    
    return (new_count, updated_count, new_alien_ids, images_downloaded_total)


def main(
    input_url: str,
    skip_images: bool = False,
    only_new: bool = True,
    reverse_order: bool = False,
    specific_ids: Optional[List[int]] = None
) -> Tuple[int, int, List[int], int]:
    """
    メイン処理: スクレイピングと画像取得を実行
    
    Returns:
        (新規追加数, 更新数, 新規エイリアンIDリスト, ダウンロードした画像数)
    """
    parsed_url = urlparse(input_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?"
    query_params = parse_qs(parsed_url.query)
    query_params.pop('page', None)
    base_query = '&'.join([f"{k}={v[0]}" for k, v in query_params.items()])
    list_page_base_url = f"{base_url}{base_query}&page="
    
    images_downloaded_total = 0
    new_count = 0
    updated_count = 0
    new_alien_ids: List[int] = []
    
    conn = None
    try:
        conn = get_db_connection()
        
        with requests.Session() as session:
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })
            
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
                return (0, 0, [], 0)
            
            specific_target_ids = sorted(set(specific_ids or []))
            if specific_target_ids:
                return scrape_specific_aliens(
                    session,
                    conn,
                    list_page_base_url,
                    total_pages,
                    specific_target_ids,
                    skip_images=skip_images
                )
            
            if reverse_order:
                db_latest_id = get_latest_alien_id_from_db(conn)
                if db_latest_id is None:
                    print("データベースにエイリアンが存在しません。全体スクレイピングを実行してください。")
                    return (0, 0, [], 0)
                
                website_latest_id = get_latest_alien_id_from_last_page(session, list_page_base_url, total_pages)
                if website_latest_id is None:
                    print("サイトから最新IDを取得できませんでした。")
                    return (0, 0, [], 0)
                
                new_count, updated_count, new_alien_ids, images_downloaded_total = scrape_new_aliens_reverse_order(
                    session,
                    conn,
                    list_page_base_url,
                    total_pages,
                    website_latest_id,
                    db_latest_id,
                    skip_images=skip_images
                )
                
                print(f"\nデータベースへの全ての変更をコミットしました。")
                print(f"新規追加: {new_count}件, 更新: {updated_count}件")
                
                return (new_count, updated_count, new_alien_ids, images_downloaded_total)
            
            existing_ids = set()
            if only_new:
                existing_ids = get_existing_alien_ids(conn)
                print(f"既存エイリアン数: {len(existing_ids)}")
            
            print("\n--- ステップ1: 全エイリアンの詳細URLを収集中 ---")
            
            detail_entry_list: List[Dict[str, Optional[str]]] = []
            for page in range(1, total_pages + 1):
                page_url = f"{list_page_base_url}{page}"
                print(f"リストの {page} / {total_pages} ページ目をスキャン中...")
                
                if page == 1:
                    entries_on_page = get_detail_entries_from_page(session, first_page_url)
                else:
                    entries_on_page = get_detail_entries_from_page(session, page_url)
                    time.sleep(1)
                
                if not entries_on_page:
                    print(f"  -> {page} ページからはデータが取得できませんでした。スキップします。")
                    continue
                
                detail_entry_list.extend(entries_on_page)
            
            if not detail_entry_list:
                print("スクレイピング対象のエイリアンが見つかりませんでした。")
                return (0, 0, [], 0)
            
            unique_entries: Dict[str, Dict[str, Optional[str]]] = {}
            for entry in detail_entry_list:
                detail_url = entry['detail_url']
                unique_entries[detail_url] = entry
            
            all_detail_entries = sorted(
                unique_entries.values(),
                key=lambda item: item.get('id') or 0
            )
            
            print(f"\n合計 {len(all_detail_entries)} 件のユニークな詳細ページを取得しました。")
            print("\n--- ステップ2: 詳細をスクレイピングし、DBに書き込み中 ---")
            
            missing_image_map: Dict[int, str] = {}
            for i, entry in enumerate(all_detail_entries, 1):
                url = entry['detail_url']
                icon_url = entry.get('icon_url')
                alien_id_from_list = entry.get('id')
                
                print(f"[{i}/{len(all_detail_entries)}] {url} を処理中...")
                
                alien_data = scrape_alien_data(session, url)
                if not alien_data or not alien_data.get('id'):
                    print("  -> データ取得に失敗、またはID不明のためスキップします。")
                    continue
                
                alien_id = int(alien_data['id'])
                
                if alien_id_from_list and alien_id_from_list != alien_id:
                    print(f"  -> 一覧のID({alien_id_from_list})と詳細のID({alien_id})が一致しません。スキップします。")
                    continue
                
                if icon_url:
                    save_filepath = os.path.join(SAVE_DIRECTORY, f"{alien_id}.png")
                    if not os.path.exists(save_filepath):
                        missing_image_map[alien_id] = icon_url
                
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM alien WHERE id = %s", (alien_id,))
                    existed = cur.fetchone() is not None
                
                upsert_alien_to_db(conn, alien_data)
                conn.commit()
                
                if existed:
                    updated_count += 1
                else:
                    new_count += 1
                    new_alien_ids.append(alien_id)
                
                time.sleep(1)
            
            print(f"\nデータベースへの全ての変更をコミットしました。")
            print(f"新規追加: {new_count}件, 更新: {updated_count}件")
            
            if not skip_images and missing_image_map:
                print(f"\n画像が存在しないエイリアン: {len(missing_image_map)}件")
                downloaded = download_images_for_new_aliens(session, missing_image_map)
                images_downloaded_total += downloaded
                print(f"\n画像ダウンロード完了: {downloaded}件")
            elif not skip_images:
                print(f"\n全てのエイリアンの画像が既に存在します。")
    
    except (Exception, psycopg2.Error) as error:
        print(f"\nエラーが発生したため、処理を中断しました: {error}")
        if conn:
            conn.rollback()
            print("データベースへの変更をロールバックしました。")
        raise
    finally:
        if conn:
            conn.close()
            print("データベース接続をクローズしました。")
    
    return (new_count, updated_count, new_alien_ids, images_downloaded_total)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='エイリアンデータと画像をスクレイピングして保存')
    parser.add_argument('--url', type=str, help='スクレイピング開始URL（環境変数SCRAPING_BASE_URLでも指定可能）')
    parser.add_argument('--skip-images', action='store_true', help='画像取得をスキップ')
    parser.add_argument('--all', action='store_true', help='既存データも含めて全て処理（デフォルトは新規のみ）')
    parser.add_argument('--reverse-order', action='store_true', help='逆順スクレイピング（最新から）を実行')
    args = parser.parse_args()
    
    input_url = args.url or os.environ.get('SCRAPING_BASE_URL')
    if not input_url or not input_url.strip():
        print("エラー: URLが指定されていません。")
        print("使用方法:")
        print("  1. コマンドライン引数: python combined_scraper.py --url <URL>")
        print("  2. 環境変数: SCRAPING_BASE_URL=<URL> python combined_scraper.py")
        exit(1)
    
    try:
        new_count, updated_count, new_ids, image_downloads = main(
            input_url,
            skip_images=args.skip_images,
            only_new=not args.all,
            reverse_order=args.reverse_order
        )
        print(f"\n--- 全ての処理が完了しました ---")
        print(f"新規追加: {new_count}件")
        print(f"更新: {updated_count}件")
        print(f"画像ダウンロード: {image_downloads}件")
        print(f"新規エイリアンID: {new_ids}")
    except Exception as e:
        print(f"\n致命的なエラー: {e}")
        exit(1)

