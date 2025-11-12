import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import requests

from scripts.scraping.full_scraper import (
    get_detail_urls_from_page,
    scrape_alien_data,
    get_db_connection,
    get_total_pages,
)
from scripts.scraping.combined_scraper import get_latest_alien_id_from_db

load_dotenv(PROJECT_ROOT / ".env")


def extract_alien_id_from_detail(session, url):
    """詳細ページ内の図鑑No.を取得（URLの番号は信用しない）"""
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


def get_new_ids():
    list_url = (
        "https://wiki.alienegg.jp/data/Alien?ra0=0&ra1=0&ra2=0&ra3=0&ra4=0&ra5=0&ra6=0&ra7=1&ra8=1&"
        "el1=1&el2=1&el3=1&el4=1&gr0=1&gr1=1&gr2=1&gr3=1&gr4=0&gr5=1&rn0=1&rn1=1&re0=1&re1=1&re2=1&"
        "sy3=0&sy1=0&sy2=0&sy4=0&mc0=1&mc2=1&mc3=1&mc4=1&mc5=1&mc6=1&mc7=1&mc8=1&mc9=1&mc10=1&mc11=1&"
        "ca6=1&ca18=1&ca21=1&ca22=1&ca26=1&ca27=1&ca30=1&ca31=1&ca38=1&ca23=1&ca24=1&ca25=1&ca34=1&"
        "ca1=1&ca2=1&ca3=1&ca4=1&ca17=1&ca5=1&ca19=1&ca7=1&ca8=1&ca9=1&ca10=1&ca11=1&ca12=1&ca13=1&"
        "ca14=1&ca15=1&ca16=1&ca32=1&ca33=1&ca35=1&ca39=1&ca40=1&ca41=1&ca42=1&ca43=1&ca44=1&ca45=0&"
        "ca46=1&ca47=1&ca48=1&ca49=1&ca50=1&page=1&narrowed=1"
    )

    with requests.Session() as session:
        response = session.get(list_url)
        response.raise_for_status()

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")
        total_pages = get_total_pages(soup)
        print(f"Wikiの総ページ数: {total_pages}")
        conn = get_db_connection()
        try:
            db_latest_id = get_latest_alien_id_from_db(conn)
        finally:
            conn.close()
        print(f"DB側の最新ID: {db_latest_id}")

        new_ids = []
        base_url_for_pages = list_url.rsplit("page=", 1)[0] + "page="

        for page in range(total_pages, 0, -1):
            page_url = list_url if page == 1 else f"{base_url_for_pages}{page}&narrowed=1"
            detail_urls = get_detail_urls_from_page(session, page_url)
            if not detail_urls:
                print(f"  -> {page}ページ目の詳細URL取得に失敗または0件")
                continue

            print(f"  -> {page}ページ目の詳細ページを解析 ({len(detail_urls)}件)")
            page_has_new = False

            for url in detail_urls:
                alien_id = extract_alien_id_from_detail(session, url)
                if alien_id is None:
                    continue

                if not db_latest_id or alien_id > db_latest_id:
                    new_ids.append(alien_id)
                    page_has_new = True

            if new_ids and not page_has_new:
                break

    return sorted(set(new_ids))


def main():
    print("== check scrape details ==")
    new_ids = get_new_ids()
    print(f"検出した新規候補ID: {new_ids}")
    if not new_ids:
        print("新規候補が無いため終了します。")
        return

    with requests.Session() as session:
        for alien_id in new_ids[:3]:  # サンプルとして最大3件
            detail_url = f"https://wiki.alienegg.jp/data/Alien_detail?cha=cha{alien_id:04d}"
            data = scrape_alien_data(session, detail_url)
            if not data:
                print(f"ID {alien_id}: データ取得に失敗しました。")
                continue

            print(f"\nID {alien_id} のスクレイピング結果:")
            print(f"  名前: {data.get('name')}")
            print(f"  HP: {data.get('hp')}")
            print(f"  Power: {data.get('power')}")
            print(f"  Motivation: {data.get('motivation')}")
            print(f"  Size: {data.get('size')}")
            print(f"  Speed: {data.get('speed')}")
            print(f"  個性: {[skill.get('text') for skill in data.get('skills', [])]}")
            print(f"  特技: {data.get('special_skill')} / {data.get('special_skill_text')}")


if __name__ == "__main__":
    main()

