import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

# --- 設定項目 ---
# このスクリプトの場所を基準に、画像保存フォルダのパスを自動的に設定
# スクリプトの場所: .../alien_egg/scripts/scraping/
# 保存先の場所:   .../alien_egg/static/images/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
SAVE_DIRECTORY = os.path.join(PROJECT_ROOT, 'static', 'images', 'aliens')
# --- ヘルパー関数 ---
def get_total_pages(soup):
    """ページネーション用のselect要素から総ページ数を取得する"""
    try:
        # クラス名が 'js-pagenation-select' の select タグを探す
        pagination_select = soup.find('select', class_='js-pagenation-select')
        if not pagination_select:
            return 1
        
        # 最後の <option> タグのテキストを取得 (例: "22 / 22ページ")
        last_option_text = pagination_select.find_all('option')[-1].text
        
        # 正規表現でテキストからページ数を抽出
        match = re.search(r'(\d+)\s*ページ', last_option_text)
        if match:
            return int(match.group(1))
        
        return 1
    except (IndexError, AttributeError):
        # 要素が見つからない場合などは1ページのみと判断
        return 1

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


# --- メインの実行部分 ---
if __name__ == '__main__':
    # 保存先フォルダが存在しない場合は作成
    if not os.path.exists(SAVE_DIRECTORY):
        os.makedirs(SAVE_DIRECTORY)
        print(f"保存フォルダ '{SAVE_DIRECTORY}' を作成しました。")

    input_url = input("収集を開始したいエイリアン一覧ページのURLを貼り付けてEnterを押してください:\n> ")
    if not input_url.strip():
        print("URLが入力されませんでした。処理を終了します。")
        exit()

    # URLを解析して、ページ番号を差し替えるためのベースURLを作成
    parsed_url = urlparse(input_url)
    base_url_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    path = parsed_url.path
    query_params = parse_qs(parsed_url.query)
    query_params.pop('page', None) # 既存のpageパラメータは削除
    base_query = '&'.join([f"{k}={v[0]}" for k, v in query_params.items()])
    list_page_base_url = f"{base_url_domain}{path}?{base_query}&page="

    with requests.Session() as session:
        session.headers.update({'User-Agent': 'Mozilla/5.0'})

        print("\n--- ステップ1: 総ページ数を取得中 ---")
        try:
            first_page_url = f"{list_page_base_url}1"
            response = session.get(first_page_url)
            response.raise_for_status()
            first_page_soup = BeautifulSoup(response.text, 'html.parser')
            total_pages = get_total_pages(first_page_soup)
            print(f"-> 総ページ数: {total_pages} を確認しました。")
        except requests.exceptions.RequestException as e:
            print(f"エラー: 1ページ目の取得に失敗しました。処理を中断します。\n{e}")
            exit()

        print("\n--- ステップ2: 全ページのアイコン画像を収集中 ---")
        total_images_downloaded = 0
        
        for page in range(1, total_pages + 1):
            page_url = f"{list_page_base_url}{page}"
            print(f"\n[{page}/{total_pages}ページ目] {page_url} を処理中...")
            
            try:
                # 1ページ目は取得済みなので再利用
                if page == 1:
                    soup = first_page_soup
                else:
                    time.sleep(1) # サーバー負荷軽減のため待機
                    response = session.get(page_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, 'html.parser')

                # classが'data-list'のテーブルを探す
                alien_table = soup.find('table', class_='data-list')
                if not alien_table:
                    print("  -> エイリアン一覧テーブルが見つかりませんでした。スキップします。")
                    continue

                # テーブル内の全tr（行）タグをループ
                for row in alien_table.find_all('tr'):
                    # th（ヘッダー行）はスキップ
                    if row.find('th'):
                        continue

                    # td（データセル）が3つある行のみを対象
                    cells = row.find_all('td')
                    if len(cells) == 3:
                        # 1番目のセルから画像URLを取得
                        img_tag = cells[0].find('img')
                        # 2番目のセルから図鑑Noを取得
                        no_text = cells[1].text.strip()
                        
                        if img_tag and 'src' in img_tag.attrs and no_text:
                            # 相対URLを絶対URLに変換 (例: /image/... -> https://.../image/...)
                            relative_img_url = img_tag['src']
                            full_image_url = urljoin(base_url_domain, relative_img_url)
                            
                            # 図鑑Noから数字だけを抽出 (例: "No. 925" -> "925")
                            alien_id = no_text.replace('No.', '').strip()
                            
                            # 保存ファイル名を決定 (例: 925.png)
                            save_filename = f"{alien_id}.png"
                            save_filepath = os.path.join(SAVE_DIRECTORY, save_filename)
                            
                            print(f"  -> 図鑑No.{alien_id} の画像をダウンロード中...")
                            if download_image(session, full_image_url, save_filepath):
                                total_images_downloaded += 1
                                print(f"    -> 完了: {save_filename}")

            except requests.exceptions.RequestException as e:
                print(f"  -> ページ取得エラー: {e}")
            except Exception as e:
                print(f"  -> 予期せぬエラー: {e}")

    print(f"\n--- 全ての処理が完了しました ---")
    print(f"合計 {total_images_downloaded} 個の画像をダウンロードしました。")