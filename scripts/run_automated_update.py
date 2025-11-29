"""
自動更新統合スクリプト
スクレイピング → 画像取得 → 変更検知 → 解析（変更・追加された個性・特技のみ） → Discord通知
"""

import os
import json
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Tuple, List, Set, Dict, Optional
import psycopg2
from psycopg2.extras import DictCursor

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

try:
    if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr and sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# 環境変数読み込み
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')

AUTO_GIT_DEFAULT_TARGETS = [
    'static/images',
    'backups/skill_list_fixed.jsonl'
]


def _strtobool(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def auto_push_updated_assets_if_needed():
    """
    画像などの生成物を自動でコミット＆プッシュ（環境変数で有効化）
    """
    if not _strtobool(os.environ.get('AUTO_GIT_PUSH')):
        return

    repo_path = PROJECT_ROOT
    if not (repo_path / '.git').exists():
        print("[auto-push] Git リポジトリが見つからないためスキップします。")
        return

    target_paths_env = os.environ.get('AUTO_GIT_TARGETS')
    if target_paths_env:
        target_paths = [path.strip() for path in target_paths_env.split(',') if path.strip()]
    else:
        target_paths = AUTO_GIT_DEFAULT_TARGETS

    existing_targets = [
        rel_path for rel_path in target_paths
        if (repo_path / rel_path).exists()
    ]

    if not existing_targets:
        print("[auto-push] コミット対象のファイルが存在しないためスキップします。")
        return

    def run_git(args: List[str], check: bool = True):
        result = subprocess.run(
            ['git'] + args,
            cwd=str(repo_path),
            capture_output=True,
            text=True
        )
        if check and result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            message = stderr or stdout or f"git {' '.join(args)} failed"
            raise RuntimeError(message)
        return result

    user_name = os.environ.get('AUTO_GIT_USER_NAME', 'auto-updater')
    user_email = os.environ.get('AUTO_GIT_USER_EMAIL', 'auto-updater@example.com')
    remote_name = os.environ.get('AUTO_GIT_REMOTE', 'origin')
    branch_name = os.environ.get('AUTO_GIT_BRANCH', 'main')
    commit_message = os.environ.get('AUTO_GIT_COMMIT_MESSAGE', 'chore: sync scraped assets')

    try:
        run_git(['config', 'user.name', user_name])
        run_git(['config', 'user.email', user_email])

        for rel_path in existing_targets:
            run_git(['add', '-f', rel_path])

        diff_check = subprocess.run(
            ['git', 'diff', '--cached', '--quiet'],
            cwd=str(repo_path)
        )
        if diff_check.returncode == 0:
            print("[auto-push] コミット対象の変更はありませんでした。")
            return

        run_git(['commit', '-m', commit_message])
        run_git(['push', remote_name, f'HEAD:{branch_name}'])
        print(f"[auto-push] {branch_name} へ変更をプッシュしました。")
    except Exception as git_error:
        print(f"[auto-push] プッシュに失敗しました: {git_error}")

# 必要なモジュールをインポート
scraping_dir = PROJECT_ROOT / 'scripts' / 'scraping'
sys.path.insert(0, str(scraping_dir))
utils_dir = PROJECT_ROOT / 'scripts' / 'utils'
sys.path.insert(0, str(utils_dir))

from utils.discord_notifier import DiscordNotifier, send_scraping_result_detailed

# combined_scraperのインポート
import importlib.util
combined_scraper_path = scraping_dir / 'combined_scraper.py'
spec = importlib.util.spec_from_file_location("combined_scraper", combined_scraper_path)
combined_scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(combined_scraper)
scraping_main = combined_scraper.main
get_db_connection = combined_scraper.get_db_connection


def get_alien_names_by_ids(conn, alien_ids: List[int]) -> Dict[int, str]:
    """
    エイリアンIDから名前を取得
    
    Args:
        conn: データベース接続
        alien_ids: エイリアンIDリスト
    
    Returns:
        {alien_id: name} の辞書
    """
    if not alien_ids:
        return {}
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(alien_ids))
            cur.execute(
                f"SELECT id, name FROM alien WHERE id IN ({placeholders})",
                alien_ids
            )
            return {row['id']: row['name'] for row in cur.fetchall()}
    except Exception as e:
        print(f"エイリアン名取得エラー: {e}")
        return {}


def get_existing_alien_ids(conn, alien_ids: List[int]) -> List[int]:
    """
    指定されたエイリアンIDのうち、DBに存在するIDのみを取得
    
    Args:
        conn: データベース接続
        alien_ids: エイリアンIDリスト
    
    Returns:
        DBに存在するエイリアンIDリスト
    """
    if not alien_ids:
        return []
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(alien_ids))
            cur.execute(
                f"SELECT id FROM alien WHERE id IN ({placeholders})",
                alien_ids
            )
            return [row['id'] for row in cur.fetchall()]
    except Exception as e:
        print(f"既存ID取得エラー: {e}")
        return []


def get_skill_texts_for_alien_ids(conn, alien_ids: List[int]) -> Tuple[Set[str], Set[str]]:
    """
    指定したエイリアンIDの個性・特技テキストを取得
    """
    regular_skills = set()
    special_skills = set()
    
    if not alien_ids:
        return regular_skills, special_skills
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(alien_ids))
            cur.execute(f"""
                SELECT id, skill_text1, skill_text2, skill_text3, "S_Skill_text"
                FROM alien
                WHERE id IN ({placeholders})
            """, alien_ids)
            
            for row in cur.fetchall():
                for key in ('skill_text1', 'skill_text2', 'skill_text3'):
                    text = row[key]
                    if text and text != 'なし':
                        regular_skills.add(text)
                
                special_text = row['S_Skill_text']
                if special_text and special_text != 'なし':
                    special_skills.add(special_text)
    except Exception as e:
        print(f"指定IDのスキルテキスト取得エラー: {e}")
    
    return regular_skills, special_skills


def get_unanalyzed_skill_texts_for_alien_ids(conn, alien_ids: List[int]) -> Tuple[Set[str], Set[str]]:
    """
    指定したエイリアンIDから未解析個性・特技テキストを取得
    
    Args:
        conn: データベース接続
        alien_ids: エイリアンIDリスト
    
    Returns:
        (未解析個性テキストのセット, 未解析特技テキストのセット)
    """
    regular_skills = set()
    special_skills = set()
    
    if not alien_ids:
        return regular_skills, special_skills
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(alien_ids))
            # 未解析個性テキストを取得
            cur.execute(f"""
                WITH analyzed_texts AS (
                    SELECT DISTINCT skill_text FROM skill_text_verified_effects
                )
                SELECT DISTINCT skill_text
                FROM (
                    SELECT skill_text1 as skill_text FROM alien WHERE id IN ({placeholders}) AND skill_text1 IS NOT NULL AND skill_text1 != 'なし'
                    UNION 
                    SELECT skill_text2 FROM alien WHERE id IN ({placeholders}) AND skill_text2 IS NOT NULL AND skill_text2 != 'なし'
                    UNION 
                    SELECT skill_text3 FROM alien WHERE id IN ({placeholders}) AND skill_text3 IS NOT NULL AND skill_text3 != 'なし'
                ) all_texts
                WHERE skill_text NOT IN (SELECT skill_text FROM analyzed_texts)
            """, alien_ids * 3)
            for row in cur.fetchall():
                if row['skill_text']:
                    regular_skills.add(row['skill_text'])
            
            # 未解析特技テキストを取得
            cur.execute(f"""
                WITH analyzed_texts AS (
                    SELECT DISTINCT skill_text FROM skill_text_verified_effects
                )
                SELECT DISTINCT "S_Skill_text" as skill_text
                FROM alien
                WHERE id IN ({placeholders})
                  AND "S_Skill_text" IS NOT NULL 
                  AND "S_Skill_text" != 'なし'
                  AND "S_Skill_text" NOT IN (SELECT skill_text FROM analyzed_texts)
            """, alien_ids)
            for row in cur.fetchall():
                if row['skill_text']:
                    special_skills.add(row['skill_text'])
    except Exception as e:
        print(f"未解析スキルテキスト取得エラー: {e}")
    
    return regular_skills, special_skills


def get_analysis_results_for_skills(conn, skill_texts: Set[str], skill_type: str = "regular") -> Dict[str, List[Dict]]:
    """
    指定されたスキルテキストの解析結果を取得
    
    Args:
        conn: データベース接続
        skill_texts: スキルテキストセット
        skill_type: "regular"（個性）または"special"（特技）
    
    Returns:
        {skill_text: [効果情報のリスト]} の辞書
    """
    if not skill_texts:
        return {}
    
    results = {}
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(skill_texts))
            cur.execute(f"""
                SELECT skill_text, effect_name, target, condition_target, 
                       has_requirement, requirement_details, requirement_count
                FROM skill_text_verified_effects
                WHERE skill_text IN ({placeholders})
                ORDER BY skill_text, effect_name
            """, list(skill_texts))
            
            for row in cur.fetchall():
                skill_text = row['skill_text']
                if skill_text not in results:
                    results[skill_text] = []
                
                effect_info = {
                    'effect_name': row['effect_name'],
                    'target': row['target'],
                    'condition_target': row['condition_target'],
                    'has_requirement': row['has_requirement'],
                    'requirement_details': row['requirement_details'],
                    'requirement_count': row['requirement_count']
                }
                results[skill_text].append(effect_info)
    except Exception as e:
        print(f"解析結果取得エラー: {e}")
    
    return results


def delete_existing_analysis(conn, skill_texts: Set[str]) -> None:
    """
    指定されたスキルテキストの既存解析データを削除（再解析のため）
    
    Args:
        conn: データベース接続
        skill_texts: 削除対象のスキルテキストセット
    """
    if not skill_texts:
        return
    
    try:
        with conn.cursor() as cur:
            # skill_text_verified_effectsテーブルから該当するレコードを削除
            placeholders = ','.join(['%s'] * len(skill_texts))
            cur.execute(
                f"DELETE FROM skill_text_verified_effects WHERE skill_text IN ({placeholders})",
                list(skill_texts)
            )
            deleted_count = cur.rowcount
            conn.commit()
            print(f"  既存解析データを削除しました: {deleted_count}件")
    except Exception as e:
        print(f"  既存解析データ削除エラー: {e}")
        conn.rollback()


def run_analysis_for_skill_texts(conn, skill_texts: Set[str], skill_type: str = "regular", alien_ids: Optional[List[int]] = None) -> Tuple[bool, str]:
    """
    指定された個性または特技テキストをLLMで解析
    
    Args:
        conn: データベース接続
        skill_texts: 解析対象のスキルテキストセット
        skill_type: "regular"（個性）または"special"（特技）
        alien_ids: 解析対象のエイリアンIDリスト（指定された場合、そのIDのみを解析）
    
    Returns:
        (成功した場合True, メッセージ)
    """
    if not skill_texts:
        skill_type_name = "個性" if skill_type == "regular" else "特技"
        return True, f"変更・追加された{skill_type_name}テキストがないため、解析をスキップしました。"
    
    try:
        # 変更・追加されたテキストの既存解析データを削除（再解析のため）
        print(f"  変更・追加されたテキストの既存解析データを削除中...")
        delete_existing_analysis(conn, skill_texts)
        
        analysis_module_path = PROJECT_ROOT / 'analysis'
        run_stage1_path = analysis_module_path / 'run_stage1.py'
        
        # 個性と特技で異なるオプションを使用
        if skill_type == "regular":
            # 個性テキストのみ解析
            # --unanalyzed-onlyを使用（既存データを削除したので、未解析として扱われる）
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
        
        # alien_idsが指定されている場合、--alien-idsオプションを追加
        if alien_ids:
            ids_arg = ','.join(str(i) for i in alien_ids)
            cmd.extend(['--alien-ids', ids_arg])
        
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        stdout_text = result.stdout or ""
        stderr_text = result.stderr or ""

        if result.returncode == 0:
            truncated_stdout = stdout_text[-500:] if stdout_text else ""
            return True, f"{skill_type_name}テキスト解析処理が完了しました（{len(skill_texts)}件）。\n{truncated_stdout}"
        else:
            return False, f"{skill_type_name}テキスト解析処理でエラーが発生しました:\n{stderr_text}"
    
    except Exception as e:
        skill_type_name = "個性" if skill_type == "regular" else "特技"
        return False, f"{skill_type_name}テキスト解析処理中に例外が発生しました: {str(e)}"


def ensure_connection(conn):
    """
    DB接続が有効か確認し、必要に応じて再接続する
    """
    if conn is None or conn.closed != 0:
        print("  -> データベース接続が閉じています。再接続します。")
        return get_db_connection()
    
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except (psycopg2.Error, psycopg2.InterfaceError):
        print("  -> データベース接続が無効になりました。再接続します。")
        try:
            conn.close()
        except Exception:
            pass
        return get_db_connection()
    
    return conn


def export_skill_list_backup(conn, output_path: Path) -> int:
    """
    skill_text_verified_effectsテーブルの最新状態をJSONLにエクスポートする
    """
    conn = ensure_connection(conn)
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("""
            SELECT
                skill_text,
                effect_name,
                effect_type,
                category,
                condition_target,
                requires_awakening,
                target,
                has_requirement,
                COALESCE(requirement_details, '') AS requirement_details,
                COALESCE(requirement_count, 1) AS requirement_count
            FROM skill_text_verified_effects
            ORDER BY skill_text, effect_name
        """)
        rows = cur.fetchall()
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8') as f:
        for row in rows:
            record = {
                "skill_text": row["skill_text"],
                "effect_name": row["effect_name"],
                "effect_type": row["effect_type"],
                "category": row["category"],
                "condition_target": row["condition_target"],
                "requires_awakening": row["requires_awakening"],
                "target": row["target"],
                "has_requirement": row["has_requirement"],
                "requirement_details": row["requirement_details"],
                "requirement_count": row["requirement_count"],
            }
            f.write(json.dumps(record, ensure_ascii=False))
            f.write('\n')
    
    return len(rows)


def expand_id_argument(raw: str) -> List[int]:
    """
    文字列で指定されたIDリスト/範囲を展開して整数配列に変換
    例: "1611,1614-1616" -> [1611, 1614, 1615, 1616]
    """
    if not raw:
        return []
    sanitized = raw.replace('、', ',').replace(' ', '')
    tokens = [token for token in sanitized.split(',') if token]
    ids: Set[int] = set()
    for token in tokens:
        if '-' in token:
            parts = token.split('-', 1)
            if len(parts) != 2:
                raise ValueError('IDの形式が正しくありません (例: 1601,1603-1605)')
            start_str, end_str = parts
            if not start_str.isdigit() or not end_str.isdigit():
                raise ValueError('IDは整数で指定してください')
            start = int(start_str)
            end = int(end_str)
            step = 1 if end >= start else -1
            for value in range(start, end + step, step):
                ids.add(value)
        else:
            if not token.isdigit():
                raise ValueError('IDは整数で指定してください')
            ids.add(int(token))
    if not ids:
        raise ValueError('1つ以上のIDを指定してください')
    return sorted(ids)


def main(
    scraping_url: str,
    skip_images: bool = False,
    skip_analysis: bool = False,
    discord_webhook_url: str = None,
    full_scrape: bool = False,
    analysis_ids: Optional[List[int]] = None,
    skip_scraping: bool = False,
    scrape_ids: Optional[List[int]] = None
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
    print("=== run_automated_update start ===")
    notifier = None
    if discord_webhook_url:
        try:
            notifier = DiscordNotifier(discord_webhook_url)
        except Exception as e:
            print(f"Discord通知初期化エラー: {e}")
            print("（処理は続行します）")
    
    errors = []
    new_count = 0
    updated_count = 0
    new_alien_ids = []
    images_downloaded = 0
    changed_regular_skills = set()
    changed_special_skills = set()
    regular_analysis_results = {}
    special_analysis_results = {}
    scrape_id_list: List[int] = sorted(set(scrape_ids or []))
    effective_analysis_ids: List[int] = sorted(set(analysis_ids or []))
    if scrape_id_list:
        print(f"指定スクレイピング対象ID: {scrape_id_list}")
    if effective_analysis_ids:
        print(f"再解析指定ID: {effective_analysis_ids}")
    
    # データベース接続を取得（変更検知用）
    conn = None
    try:
        conn = get_db_connection()
    except Exception as e:
        error_msg = f"データベース接続エラー: {str(e)}"
        errors.append(error_msg)
        print(f"エラー: {error_msg}")
        if notifier and discord_webhook_url:
            send_scraping_result_detailed(
                discord_webhook_url,
                new_alien_names={},
                updated_alien_names={},
                changed_regular_skills=set(),
                changed_special_skills=set(),
                regular_analysis_results={},
                special_analysis_results={},
                images_downloaded=0,
                error_info={
                    "step": "データベース接続",
                    "message": str(e),
                    "progress": "処理開始前にエラーが発生しました"
                }
            )
        return 1
    
    try:
        # ステップ1: スクレイピング＋画像取得
        print("\n" + "=" * 80)
        print("ステップ1: スクレイピングと画像取得")
        print("=" * 80)
        
        if skip_scraping:
            print("スクレイピング処理をスキップします (--skip-scraping 指定)。")
        else:
            try:
                # 逆順スクレイピング（デフォルト）・全体スクレイピング・指定IDスクレイピング
                if scrape_id_list:
                    print("指定IDスクレイピングモードで実行します...")
                    scrape_result = scraping_main(
                        scraping_url,
                        skip_images=skip_images,
                        only_new=False,
                        reverse_order=False,
                        specific_ids=scrape_id_list
                    )
                elif full_scrape:
                    print("全体スクレイピングモードで実行します...")
                    scrape_result = scraping_main(
                        scraping_url,
                        skip_images=skip_images,
                        only_new=True,
                        reverse_order=False
                    )
                else:
                    print("逆順スクレイピングモードで実行します（最新から）...")
                    scrape_result = scraping_main(
                        scraping_url,
                        skip_images=skip_images,
                        only_new=True,
                        reverse_order=True
                    )
                
                if isinstance(scrape_result, tuple):
                    if len(scrape_result) == 4:
                        new_count, updated_count, new_alien_ids, images_downloaded = scrape_result
                    elif len(scrape_result) == 3:
                        new_count, updated_count, new_alien_ids = scrape_result
                        images_downloaded = 0 if skip_images else len(new_alien_ids)
                    else:
                        raise ValueError("scraping_main から予期しない戻り値フォーマットを受け取りました。")
                else:
                    raise ValueError("scraping_main からタプル以外の戻り値を受け取りました。")
                
                print(f"\nスクレイピング完了: 新規{new_count}件, 更新{updated_count}件")
            
            except Exception as e:
                error_msg = f"スクレイピングエラー: {str(e)}"
                errors.append(error_msg)
                print(f"エラー: {error_msg}")
                
                if notifier and discord_webhook_url:
                    send_scraping_result_detailed(
                        discord_webhook_url,
                        new_alien_names={},
                        updated_alien_names={},
                        changed_regular_skills=set(),
                        changed_special_skills=set(),
                        regular_analysis_results={},
                        special_analysis_results={},
                        images_downloaded=0,
                        error_info={
                            "step": "スクレイピング",
                            "message": str(e),
                            "progress": "スクレイピング処理中にエラーが発生しました"
                        }
                    )
                
                # スクレイピングが失敗した場合は処理を中断
                return 1
        
        # ステップ2: スクレイピング対象IDから未解析個性・特技を取得
        print("\n" + "=" * 80)
        print("ステップ2: スクレイピング対象IDから未解析個性・特技を取得")
        print("=" * 80)
        
        # スクレイピングに時間を要した場合に備え、接続を確認
        conn = ensure_connection(conn)
        
        scraped_target_ids_set: Set[int] = set()
        if skip_scraping:
            print("スクレイピング工程はスキップされました。")
        else:
            if scrape_id_list:
                scraped_ids = set(new_alien_ids) if new_alien_ids else set()
                existing_from_scrape_list = get_existing_alien_ids(conn, scrape_id_list)
                scraped_ids.update(existing_from_scrape_list)
                scraped_target_ids_set = scraped_ids
                if len(scraped_ids) < len(scrape_id_list):
                    missing_ids = sorted(set(scrape_id_list) - scraped_ids)
                    print(f"  -> 警告: 以下のIDはスクレイピングされませんでした: {missing_ids}")
            elif new_alien_ids:
                scraped_target_ids_set = set(new_alien_ids)
        
        analysis_target_ids: List[int] = []
        if effective_analysis_ids:
            existing_ids = get_existing_alien_ids(conn, effective_analysis_ids)
            analysis_target_ids = sorted(existing_ids)
            if len(existing_ids) < len(effective_analysis_ids):
                missing_ids = sorted(set(effective_analysis_ids) - set(existing_ids))
                print(f"  -> 警告: 以下のIDはDBに存在しません: {missing_ids}")
        
        scraped_target_ids = sorted(scraped_target_ids_set)
        combined_target_ids: List[int] = []
        if not scraped_target_ids and not analysis_target_ids:
            print("解析対象となるエイリアンIDがありません。")
            changed_regular_skills = set()
            changed_special_skills = set()
        else:
            if scraped_target_ids:
                print(f"スクレイピング対象ID: {scraped_target_ids}")
                new_regular_skills, new_special_skills = get_unanalyzed_skill_texts_for_alien_ids(conn, scraped_target_ids)
                changed_regular_skills = set(new_regular_skills)
                changed_special_skills = set(new_special_skills)
                print(f"未解析個性・特技検出: 個性テキスト {len(new_regular_skills)}件, 特技テキスト {len(new_special_skills)}件")
                if new_regular_skills:
                    print(f"未解析個性テキスト（最初の5件）: {list(new_regular_skills)[:5]}")
                if new_special_skills:
                    print(f"未解析特技テキスト（最初の5件）: {list(new_special_skills)[:5]}")
            else:
                print("スクレイピング対象ID: なし")
                changed_regular_skills = set()
                changed_special_skills = set()
            
            if analysis_target_ids:
                print(f"再解析指定ID: {analysis_target_ids}")
                forced_regular_skills, forced_special_skills = get_skill_texts_for_alien_ids(conn, analysis_target_ids)
                print(f"再解析対象個性テキスト: {len(forced_regular_skills)}件, 特技テキスト: {len(forced_special_skills)}件")
                if forced_regular_skills:
                    print(f"再解析個性テキスト（最初の5件）: {list(forced_regular_skills)[:5]}")
                if forced_special_skills:
                    print(f"再解析特技テキスト（最初の5件）: {list(forced_special_skills)[:5]}")
                changed_regular_skills.update(forced_regular_skills)
                changed_special_skills.update(forced_special_skills)
            else:
                print("再解析指定ID: なし")
            
            combined_target_ids = sorted(set(scraped_target_ids) | set(analysis_target_ids))
        
        # ステップ3: LLM解析（未解析個性・特技がある場合のみ）
        if not skip_analysis and (changed_regular_skills or changed_special_skills):
            print("\n" + "=" * 80)
            print("ステップ3: LLM解析（変更・追加された個性・特技テキストを解析）")
            print("=" * 80)
            
            # 3-1: 個性解析
            if changed_regular_skills:
                try:
                    print("\n--- 3-1: 個性テキスト解析 ---")
                    conn = ensure_connection(conn)
                    success, message = run_analysis_for_skill_texts(conn, changed_regular_skills, skill_type="regular", alien_ids=combined_target_ids if combined_target_ids else None)
                    if success:
                        print(f"個性解析完了: {message}")
                        # 解析結果を取得
                        conn = ensure_connection(conn)
                        regular_analysis_results = get_analysis_results_for_skills(conn, changed_regular_skills, skill_type="regular")
                    else:
                        errors.append(f"個性解析エラー: {message}")
                        print(f"エラー: {message}")
                
                except Exception as e:
                    error_msg = f"個性解析処理例外: {str(e)}"
                    errors.append(error_msg)
                    print(f"エラー: {error_msg}")
            else:
                print("\n--- 3-1: 個性テキスト解析 ---")
                print("変更・追加された個性テキストがないため、解析をスキップしました。")
            
            # 3-2: 特技解析
            if changed_special_skills:
                try:
                    print("\n--- 3-2: 特技テキスト解析 ---")
                    conn = ensure_connection(conn)
                    success, message = run_analysis_for_skill_texts(conn, changed_special_skills, skill_type="special", alien_ids=combined_target_ids if combined_target_ids else None)
                    if success:
                        print(f"特技解析完了: {message}")
                        # 解析結果を取得
                        conn = ensure_connection(conn)
                        special_analysis_results = get_analysis_results_for_skills(conn, changed_special_skills, skill_type="special")
                    else:
                        errors.append(f"特技解析エラー: {message}")
                        print(f"エラー: {message}")
                
                except Exception as e:
                    error_msg = f"特技解析処理例外: {str(e)}"
                    errors.append(error_msg)
                    print(f"エラー: {error_msg}")
            else:
                print("\n--- 3-2: 特技テキスト解析 ---")
                print("変更・追加された特技テキストがないため、解析をスキップしました。")
        
        # skill_list_fixed.jsonl を最新状態に更新
        try:
            exported_count = export_skill_list_backup(conn, PROJECT_ROOT / 'backups' / 'skill_list_fixed.jsonl')
            print(f"  -> skill_list_fixed.jsonl を更新しました ({exported_count}件)")
        except Exception as e:
            error_msg = f"バックアップ出力エラー: {str(e)}"
            errors.append(error_msg)
            print(f"エラー: {error_msg}")
        
        # ステップ4: 最終結果をDiscordに通知（追加・更新・エラーのときのみ、1つのメッセージに統合）
        if notifier and discord_webhook_url:
            # エラー情報を準備
            error_info = None
            if errors:
                error_info = {
                    "step": "処理中",
                    "message": "\n".join(errors[:3]),  # 最大3件
                    "progress": f"新規追加: {new_count}件, 更新: {updated_count}件"
                }
            
            # 追加エイリアン名を取得
            new_alien_names = {}
            if new_alien_ids:
                conn = ensure_connection(conn)
                new_alien_names = get_alien_names_by_ids(conn, new_alien_ids)
            
            # 更新エイリアン名を取得（スクレイピング対象IDから新規追加IDを除外）
            updated_alien_names = {}
            if scraped_target_ids:
                new_id_set = set(new_alien_ids or [])
                updated_target_ids = [aid for aid in scraped_target_ids if aid not in new_id_set]
                if updated_target_ids:
                    conn = ensure_connection(conn)
                    updated_alien_names = get_alien_names_by_ids(conn, updated_target_ids)
            
            # 追加・更新・エラー・解析結果のいずれかがある場合のみ送信
            if new_alien_names or updated_alien_names or error_info or changed_regular_skills or changed_special_skills:
                send_scraping_result_detailed(
                    discord_webhook_url,
                    new_alien_names=new_alien_names,
                    updated_alien_names=updated_alien_names,
                    changed_regular_skills=changed_regular_skills,
                    changed_special_skills=changed_special_skills,
                    regular_analysis_results=regular_analysis_results,
                    special_analysis_results=special_analysis_results,
                    images_downloaded=images_downloaded,
                    error_info=error_info
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

        if not errors:
            auto_push_updated_assets_if_needed()
        
        return 0 if not errors else 1
    
    except Exception as e:
        error_msg = f"致命的なエラー: {str(e)}"
        print(f"エラー: {error_msg}")
        
        if notifier and discord_webhook_url:
            send_scraping_result_detailed(
                discord_webhook_url,
                new_alien_names={},
                updated_alien_names={},
                changed_regular_skills=set(),
                changed_special_skills=set(),
                regular_analysis_results={},
                special_analysis_results={},
                images_downloaded=0,
                error_info={
                    "step": "致命的なエラー",
                    "message": str(e),
                    "progress": "処理が中断されました"
                }
            )
        
        return 1
    
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


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
    parser.add_argument(
        '--full-scrape',
        action='store_true',
        help='全体スクレイピングを実行（デフォルトは逆順スクレイピング）'
    )
    parser.add_argument(
        '--analysis-ids',
        type=str,
        help='LLM解析を強制実行するエイリアンID（カンマ区切り、範囲指定は例: 1611-1623）'
    )
    parser.add_argument(
        '--scrape-ids',
        type=str,
        help='スクレイピング対象とするエイリアンID（カンマ区切り、範囲指定は例: 1611-1615）'
    )
    parser.add_argument(
        '--skip-scraping',
        action='store_true',
        help='スクレイピング工程をスキップし、解析のみを実行'
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
    
    analysis_ids: List[int] = []
    scrape_ids: List[int] = []
    try:
        if args.analysis_ids:
            analysis_ids = expand_id_argument(args.analysis_ids)
        if args.scrape_ids:
            scrape_ids = expand_id_argument(args.scrape_ids)
    except ValueError as exc:
        parser.error(str(exc))
    
    if scrape_ids and args.full_scrape:
        parser.error('--scrape-ids と --full-scrape は同時に指定できません')
    if scrape_ids and args.skip_scraping:
        parser.error('--scrape-ids と --skip-scraping は同時に指定できません')
    
    exit_code = main(
        scraping_url,
        skip_images=args.skip_images,
        skip_analysis=args.skip_analysis,
        discord_webhook_url=discord_webhook_url,
        full_scrape=args.full_scrape,
        analysis_ids=analysis_ids if analysis_ids else None,
        skip_scraping=args.skip_scraping,
        scrape_ids=scrape_ids if scrape_ids else None
    )
    
    sys.exit(exit_code)

