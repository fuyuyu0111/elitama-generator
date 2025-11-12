import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from scripts.scraping.full_scraper import (
    get_db_connection,
    get_detail_urls_from_page,
    get_total_pages,
)
from scripts.scraping.combined_scraper import get_latest_alien_id_from_db


def extract_alien_id_from_detail(session, url):
    """詳細ページから図鑑No.だけを取得する（URL上のIDは信用しない）"""
    from bs4 import BeautifulSoup
    import re

    try:
        response = session.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        detail_section = soup.find("section", id="alien-detail")
        if not detail_section:
            return None

        raw_id = None
        for p_tag in detail_section.find_all("p"):
            text_value = p_tag.get_text(separator="", strip=True)
            if "図鑑No." not in text_value:
                continue
            raw_id = text_value.replace("図鑑No.", "").strip()
            break

        if raw_id is None:
            return None

        return int(raw_id)
    except Exception as e:
        print(f"     - {url} で図鑑No.取得に失敗: {e}")
        return None


def main():
    print("== compare latest IDs ==")
    list_url = (
        "https://wiki.alienegg.jp/data/Alien?ra0=0&ra1=0&ra2=0&ra3=0&ra4=0&ra5=0&ra6=0&ra7=1&ra8=1&"
        "el1=1&el2=1&el3=1&el4=1&gr0=1&gr1=1&gr2=1&gr3=1&gr4=0&gr5=1&rn0=1&rn1=1&re0=1&re1=1&re2=1&"
        "sy3=0&sy1=0&sy2=0&sy4=0&mc0=1&mc2=1&mc3=1&mc4=1&mc5=1&mc6=1&mc7=1&mc8=1&mc9=1&mc10=1&mc11=1&"
        "ca6=1&ca18=1&ca21=1&ca22=1&ca26=1&ca27=1&ca30=1&ca31=1&ca38=1&ca23=1&ca24=1&ca25=1&ca34=1&"
        "ca1=1&ca2=1&ca3=1&ca4=1&ca17=1&ca5=1&ca19=1&ca7=1&ca8=1&ca9=1&ca10=1&ca11=1&ca12=1&ca13=1&"
        "ca14=1&ca15=1&ca16=1&ca32=1&ca33=1&ca35=1&ca39=1&ca40=1&ca41=1&ca42=1&ca43=1&ca44=1&ca45=0&"
        "ca46=1&ca47=1&ca48=1&ca49=1&ca50=1&page=1&narrowed=1"
    )

    from bs4 import BeautifulSoup

    print("一覧ページから詳細URLを収集しています...")
    with requests.Session() as session:
        response = session.get(list_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        total_pages = get_total_pages(soup)
        print(f"Wikiの総ページ数: {total_pages}")

        base_url_for_pages = list_url.rsplit("page=", 1)[0] + "page="
        max_site_id = 0
        processed = 0

        for page in range(total_pages, 0, -1):
            page_url = list_url if page == 1 else f"{base_url_for_pages}{page}&narrowed=1"
            detail_urls = get_detail_urls_from_page(session, page_url)
            if not detail_urls:
                print(f"  -> {page}ページ目の詳細URL取得に失敗または0件")
                continue

            print(f"  -> {page}ページ目の詳細ページを解析 ({len(detail_urls)}件)")

            for url in detail_urls:
                alien_id = extract_alien_id_from_detail(session, url)
                if alien_id is None:
                    continue
                processed += 1
                if alien_id > max_site_id:
                    max_site_id = alien_id

                if processed % 10 == 0:
                    print(f"     - {processed}件処理済み (現在最大ID: {max_site_id})")

            if max_site_id:
                break

    print(f"詳細ページ解析数: {processed}")

    print(f"Wiki側の最新ID: {max_site_id}")

    try:
        conn = get_db_connection()
    except Exception as e:
        print(f"DB接続エラー: {e}")
        return

    try:
        db_latest_id = get_latest_alien_id_from_db(conn)
        print(f"DB側の最新ID: {db_latest_id}")

        if max_site_id and db_latest_id:
            if max_site_id > db_latest_id:
                new_ids = list(range(db_latest_id + 1, max_site_id + 1))
                print(f"→ 新規候補ID: {new_ids}")
            else:
                print("→ 新規候補はありません。")
    except Exception as e:
        print(f"DBクエリエラー: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

