import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
import requests

from scripts.scraping.full_scraper import get_detail_urls_from_page

load_dotenv(PROJECT_ROOT / ".env")


def main():
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
        detail_urls = get_detail_urls_from_page(session, list_url)

    print(f"一覧ページから取得した詳細URL数: {len(detail_urls)}")
    print("サンプルURL (先頭5件):")
    for url in detail_urls[:5]:
        print(f"  {url}")


if __name__ == "__main__":
    main()

