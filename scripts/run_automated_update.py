"""
自動更新統合スクリプト
スクレイピング → 画像取得 → 解析（新規データがある場合のみ） → Discord通知
"""

import os
import sys
import argparse
from pathlib import Path
from typing import Tuple, List

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

# 環境変数読み込み
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

# 必要なモジュールをインポート
scraping_dir = PROJECT_ROOT / 'scripts' / 'scraping'
sys.path.insert(0, str(scraping_dir))
utils_dir = PROJECT_ROOT / 'scripts' / 'utils'
sys.path.insert(0, str(utils_dir))

from utils.discord_notifier import DiscordNotifier, send_scraping_result

# combined_scraperのインポート
import importlib.util
combined_scraper_path = scraping_dir / 'combined_scraper.py'
spec = importlib.util.spec_from_file_location("combined_scraper", combined_scraper_path)
combined_scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(combined_scraper)
scraping_main = combined_scraper.main


def run_analysis(new_alien_ids: List[int], skill_type: str = "regular") -> Tuple[bool, str]:
    """
    新規エイリアンの個性または特技テキストをLLMで解析
    
    Args:
        new_alien_ids: 新規エイリアンIDリスト
        skill_type: "regular"（個性）または"special"（特技）
    
    Returns:
        (成功した場合True, メッセージ)
    """
    if not new_alien_ids:
        return True, "新規データがないため、解析をスキップしました。"
    
    try:
        import subprocess
        
        analysis_module_path = PROJECT_ROOT / 'analysis'
        run_stage1_path = analysis_module_path / 'run_stage1.py'
        
        # 個性と特技で異なるオプションを使用
        if skill_type == "regular":
            # 個性テキストのみ解析
            cmd = [
                sys.executable,
                str(run_stage1_path),
                '--unanalyzed-only',
                '--regular-skills-only'
            ]
            skill_type_name = "個性"
        elif skill_type == "special":
            # 特技テキストのみ解析
            cmd = [
                sys.executable,
                str(run_stage1_path),
                '--unanalyzed-only',
                '--special-skills-only'
            ]
            skill_type_name = "特技"
        else:
            return False, f"無効なskill_type: {skill_type}（'regular'または'special'を指定してください）"
        
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            output_lines = result.stdout.split('\n')
            # 出力から処理件数を取得（簡易的）
            return True, f"{skill_type_name}テキスト解析処理が完了しました。\n{result.stdout[-500:]}"
        else:
            return False, f"{skill_type_name}テキスト解析処理でエラーが発生しました:\n{result.stderr}"
    
    except Exception as e:
        return False, f"{skill_type_name}テキスト解析処理中に例外が発生しました: {str(e)}"


def main(
    scraping_url: str,
    skip_images: bool = False,
    skip_analysis: bool = False,
    discord_webhook_url: str = None
) -> int:
    """
    メイン処理
    
    Args:
        scraping_url: スクレイピング開始URL
        skip_images: 画像取得をスキップ
        skip_analysis: 解析をスキップ
        discord_webhook_url: Discord Webhook URL
    
    Returns:
        終了コード（0=成功、1=失敗）
    """
    notifier = None
    if discord_webhook_url:
        try:
            notifier = DiscordNotifier(discord_webhook_url)
            notifier.send_info("自動更新処理を開始しました。")
        except Exception as e:
            print(f"Discord通知初期化エラー: {e}")
            print("（処理は続行します）")
    
    errors = []
    new_count = 0
    updated_count = 0
    new_alien_ids = []
    images_downloaded = 0
    
    try:
        # ステップ1: スクレイピング＋画像取得
        print("=" * 80)
        print("ステップ1: スクレイピングと画像取得")
        print("=" * 80)
        
        try:
            new_count, updated_count, new_alien_ids = scraping_main(
                scraping_url,
                skip_images=skip_images,
                only_new=True
            )
            
            if not skip_images and new_alien_ids:
                # 画像ダウンロード数はcombined_scraper内でカウントされる
                # ここでは簡易的に新規エイリアン数とする
                images_downloaded = len(new_alien_ids)
            
            print(f"\nスクレイピング完了: 新規{new_count}件, 更新{updated_count}件")
        
        except Exception as e:
            error_msg = f"スクレイピングエラー: {str(e)}"
            errors.append(error_msg)
            print(f"エラー: {error_msg}")
            
            if notifier:
                notifier.send_error("スクレイピング処理でエラーが発生しました。", e)
            
            # スクレイピングが失敗した場合は処理を中断
            return 1
        
        # ステップ2: LLM解析（新規データがある場合のみ）
        if not skip_analysis and new_alien_ids:
            print("\n" + "=" * 80)
            print("ステップ2: LLM解析（個性と特技を別々に処理）")
            print("=" * 80)
            
            # 2-1: 個性解析
            try:
                print("\n--- 2-1: 個性テキスト解析 ---")
                success, message = run_analysis(new_alien_ids, skill_type="regular")
                if success:
                    print(f"個性解析完了: {message}")
                    if notifier:
                        notifier.send_info("個性テキストのLLM解析が完了しました。")
                else:
                    errors.append(f"個性解析エラー: {message}")
                    print(f"エラー: {message}")
                    if notifier:
                        notifier.send_warning("個性テキストのLLM解析でエラーが発生しました。", {"エラー": message[:200]})
            
            except Exception as e:
                error_msg = f"個性解析処理例外: {str(e)}"
                errors.append(error_msg)
                print(f"エラー: {error_msg}")
                if notifier:
                    notifier.send_warning("個性テキスト解析処理で例外が発生しました。", {"エラー": str(e)[:200]})
            
            # 2-2: 特技解析
            try:
                print("\n--- 2-2: 特技テキスト解析 ---")
                success, message = run_analysis(new_alien_ids, skill_type="special")
                if success:
                    print(f"特技解析完了: {message}")
                    if notifier:
                        notifier.send_info("特技テキストのLLM解析が完了しました。")
                else:
                    errors.append(f"特技解析エラー: {message}")
                    print(f"エラー: {message}")
                    if notifier:
                        notifier.send_warning("特技テキストのLLM解析でエラーが発生しました。", {"エラー": message[:200]})
            
            except Exception as e:
                error_msg = f"特技解析処理例外: {str(e)}"
                errors.append(error_msg)
                print(f"エラー: {error_msg}")
                if notifier:
                    notifier.send_warning("特技テキスト解析処理で例外が発生しました。", {"エラー": str(e)[:200]})
        
        # ステップ3: 最終結果をDiscordに通知
        if notifier:
            send_scraping_result(
                discord_webhook_url,
                new_count,
                updated_count,
                new_alien_ids,
                images_downloaded,
                errors if errors else None
            )
        
        # 結果サマリー
        print("\n" + "=" * 80)
        print("処理完了サマリー")
        print("=" * 80)
        print(f"新規追加: {new_count}件")
        print(f"更新: {updated_count}件")
        print(f"新規エイリアンID: {new_alien_ids}")
        print(f"画像ダウンロード: {images_downloaded}件")
        if errors:
            print(f"エラー数: {len(errors)}件")
            for error in errors:
                print(f"  - {error}")
        
        return 0 if not errors else 1
    
    except Exception as e:
        error_msg = f"致命的なエラー: {str(e)}"
        print(f"エラー: {error_msg}")
        
        if notifier:
            notifier.send_error("自動更新処理で致命的なエラーが発生しました。", e)
        
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='自動更新統合スクリプト')
    parser.add_argument(
        '--url',
        type=str,
        help='スクレイピング開始URL（環境変数SCRAPING_BASE_URLでも指定可能）'
    )
    parser.add_argument(
        '--skip-images',
        action='store_true',
        help='画像取得をスキップ'
    )
    parser.add_argument(
        '--skip-analysis',
        action='store_true',
        help='LLM解析をスキップ'
    )
    parser.add_argument(
        '--discord-webhook',
        type=str,
        help='Discord Webhook URL（環境変数DISCORD_WEBHOOK_URLでも指定可能）'
    )
    
    args = parser.parse_args()
    
    # URL取得
    scraping_url = args.url or os.environ.get('SCRAPING_BASE_URL')
    if not scraping_url:
        print("エラー: スクレイピングURLが指定されていません。")
        print("使用方法:")
        print("  1. コマンドライン引数: python run_automated_update.py --url <URL>")
        print("  2. 環境変数: SCRAPING_BASE_URL=<URL> python run_automated_update.py")
        sys.exit(1)
    
    # Discord Webhook URL取得
    discord_webhook_url = args.discord_webhook or os.environ.get('DISCORD_WEBHOOK_URL')
    
    exit_code = main(
        scraping_url,
        skip_images=args.skip_images,
        skip_analysis=args.skip_analysis,
        discord_webhook_url=discord_webhook_url
    )
    
    sys.exit(exit_code)

