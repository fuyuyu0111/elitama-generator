from dotenv import load_dotenv
import os
import json
import sys
import secrets
import subprocess
import threading
from threading import Lock
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, jsonify, request, session
from functools import lru_cache, wraps

# 共通ヘルパー関数をインポート
PROJECT_ROOT = Path(__file__).resolve().parent
# .envファイルを読み込む（PROJECT_ROOTを明示的に指定）
load_dotenv(dotenv_path=PROJECT_ROOT / '.env')
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
from utils.db_helpers import normalize_alien_row, is_special_skill

app = Flask(__name__)

# セキュリティ設定
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30分

# 環境変数からパスワードを取得
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')  # デフォルトパスワード

# バックグラウンド処理の排他制御
_background_process_lock = Lock()
_background_process_running = False
_background_process_type = None  # "full_scrape", "partial_scrape", "analysis" など
_background_process_start_time = None

# ============================================================================
# ユーティリティ
# ============================================================================
def _strtobool(value: str) -> bool:
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def build_scraper_subprocess_env() -> dict:
    """
    管理モードからスクレイピングスクリプトを起動する際に必要な環境変数を整備
    """
    env = os.environ.copy()
    admin_setting = os.environ.get('ADMIN_AUTO_GIT_PUSH')
    if admin_setting is None or _strtobool(admin_setting):
        env.setdefault('AUTO_GIT_PUSH', '1')
    else:
        env.setdefault('AUTO_GIT_PUSH', os.environ.get('AUTO_GIT_PUSH', '0'))

    if _strtobool(env.get('AUTO_GIT_PUSH', '0')):
        env.setdefault('AUTO_GIT_BRANCH', os.environ.get('AUTO_GIT_BRANCH', 'main'))
        env.setdefault('AUTO_GIT_REMOTE', os.environ.get('AUTO_GIT_REMOTE', 'origin'))
        env.setdefault('AUTO_GIT_USER_NAME', os.environ.get('AUTO_GIT_USER_NAME', 'auto-updater'))
        env.setdefault('AUTO_GIT_USER_EMAIL', os.environ.get('AUTO_GIT_USER_EMAIL', 'auto-updater@example.com'))

    return env


# ============================================================================
# 認証関連
# ============================================================================
def check_admin():
    """管理モードの認証状態を確認"""
    return session.get('admin_logged_in', False)

def require_admin(func):
    """管理機能用デコレータ"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not check_admin():
            return jsonify({'success': False, 'error': '認証が必要です'}), 401
        return func(*args, **kwargs)
    return wrapper


def parse_id_list(value):
    """さまざまな形式のID指定を正規化して昇順リストに変換"""
    if value is None:
        raise ValueError('1つ以上のIDを指定してください')
    if isinstance(value, list):
        try:
            ids = sorted({int(v) for v in value})
        except (ValueError, TypeError):
            raise ValueError('IDは整数で指定してください')
        if not ids:
            raise ValueError('1つ以上のIDを指定してください')
        return ids
    if isinstance(value, str):
        sanitized = value.replace('、', ',').replace(' ', '')
        if not sanitized:
            raise ValueError('1つ以上のIDを指定してください')
        ids = set()
        for token in sanitized.split(','):
            if not token:
                continue
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
                for current in range(start, end + step, step):
                    ids.add(current)
            else:
                if not token.isdigit():
                    raise ValueError('IDは整数で指定してください')
                ids.add(int(token))
        if not ids:
            raise ValueError('1つ以上のIDを指定してください')
        return sorted(ids)
    raise ValueError('IDの形式が正しくありません')


def get_db_connection():
    conn_str = os.environ.get('DATABASE_URL')
    if not conn_str:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(conn_str, sslmode='require', cursor_factory=DictCursor)

@lru_cache(maxsize=None)
def get_all_aliens():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # ！！！ (★修正★) 判定と表示に必要なカラムをすべてSELECTする ！！！
    cur.execute("""
        SELECT 
            id, name, attribute, affiliation, attack_range, attack_area, 
            role, type_1, type_2, type_3, type_4, 
            skill_no1, skill_text1, skill_no2, skill_text2, skill_no3, skill_text3,
            hp, power, motivation, size, speed, "S_Skill", "S_Skill_text" 
        FROM alien 
        ORDER BY id
    """)
    aliens = cur.fetchall()
    cur.close()
    conn.close()
    
    # (新) S_Skill と S_Skill_text のキー名を小文字に統一（共通ヘルパー関数を使用）
    # (index.html が s_skill, s_skill_text を期待しているため)
    aliens_list = [normalize_alien_row(dict(a)) for a in aliens]

    # 辞書を作成 (JavaScriptが使用)
    aliens_dict = {str(a['id']): a for a in aliens_list}
    return aliens_dict

@lru_cache(maxsize=None)
def get_all_skill_requirements_new():
    """
    (新) skill_text_verified_effectsテーブルから、
    味方編成要求(has_requirement = true)を持つデータを取得し、
    skill_textをキーにした辞書として返す。
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)
    
    cur.execute("""
        SELECT skill_text, requirement_details, requirement_count
        FROM skill_text_verified_effects
        WHERE has_requirement = true AND requirement_details IS NOT NULL
        ORDER BY skill_text, requirement_details, requirement_count
    """)
    rows = cur.fetchall()
    
    requirements_by_text = {}
    seen_requirements = {} 

    for row in rows:
        skill_text = row['skill_text']
        if skill_text not in requirements_by_text:
            requirements_by_text[skill_text] = []
            seen_requirements[skill_text] = set()

        details = row['requirement_details']
        is_not = False
        
        # ！！！ (★修正★) コロンが含まれているかチェック ！！！
        if ':' not in details:
            # 想定外のフォーマットの場合はログに記録してスキップ
            # (app.logger.warning を使うとターミナルに出力される)
            # app.logger.warning(f"Skipping invalid requirement_details format: {details} for skill_text: {skill_text}")
            continue # この行の処理をスキップ

        # コロンが含まれている場合のみ処理を続行
        if details.endswith('!'):
            is_not = True
            details = details[:-1]
            
        try:
            req_type, req_value = details.split(':', 1)
        except ValueError:
            # (念のため split エラーもここでキャッチしてスキップ)
            # app.logger.warning(f"Could not split requirement_details: {row['requirement_details']} for skill_text: {skill_text}")
            continue

        # ！！！ (★修正★) requirement_countを整数型に統一（型の不整合を防ぐ） ！！！
        try:
            req_count = int(row['requirement_count']) if row['requirement_count'] is not None else 1
        except (ValueError, TypeError):
            # 変換できない場合はデフォルトで1とする
            req_count = 1

        req_tuple = (req_type, req_value, req_count, is_not)
        if req_tuple not in seen_requirements[skill_text]:
            requirements_by_text[skill_text].append({
                "type": req_type,
                "value": req_value,
                "count": req_count,
                "is_not": is_not 
            })
            seen_requirements[skill_text].add(req_tuple)
        
    cur.close()
    conn.close()
    return requirements_by_text

def migrate_correct_effect_names_table():
    """correct_effect_namesテーブルにtarget/condition_target関連カラムを追加するマイグレーション"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # カラムが存在するかチェック
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'correct_effect_names' 
            AND column_name IN ('target', 'condition_target', 'show_target', 'show_condition_target')
        """)
        existing_columns = [row[0] for row in cur.fetchall()]
        
        # カラムが存在しない場合のみ追加
        if 'target' not in existing_columns:
            cur.execute("ALTER TABLE correct_effect_names ADD COLUMN target TEXT")
        if 'condition_target' not in existing_columns:
            cur.execute("ALTER TABLE correct_effect_names ADD COLUMN condition_target TEXT")
        if 'show_target' not in existing_columns:
            cur.execute("ALTER TABLE correct_effect_names ADD COLUMN show_target BOOLEAN DEFAULT TRUE")
        if 'show_condition_target' not in existing_columns:
            cur.execute("ALTER TABLE correct_effect_names ADD COLUMN show_condition_target BOOLEAN DEFAULT TRUE")
        
        conn.commit()
        app.logger.info("correct_effect_namesテーブルのマイグレーション完了")
    except Exception as e:
        conn.rollback()
        app.logger.error(f"マイグレーションエラー: {e}")
        raise
    finally:
        cur.close()
        conn.close()

# アプリ起動時にマイグレーションを実行（初回のみ）
try:
    migrate_correct_effect_names_table()
except Exception as e:
    app.logger.warning(f"マイグレーション実行時にエラーが発生しました（既に実行済みの可能性があります）: {e}")

def get_correct_effect_names():
    """
    (新) フェーズ1 効果絞り込み機能のために、効果辞書を取得する（個性用）
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)
    # categoryがS_SKILL_*で始まらないものを個性用として取得
    cur.execute("""
        SELECT correct_name as correct_effect_names, effect_type, category, 
               target, condition_target, show_target, show_condition_target
        FROM correct_effect_names 
        WHERE category NOT LIKE 'S_SKILL_%'
        ORDER BY category, correct_name
    """)
    effects = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return effects

@lru_cache(maxsize=None)
def get_s_skill_effect_names():
    """
    特技用効果辞書を取得する
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)
    # categoryがS_SKILL_*で始まるものを特技用として取得
    cur.execute("""
        SELECT correct_name as correct_effect_names, effect_type, category, 
               target, condition_target, show_target, show_condition_target
        FROM correct_effect_names 
        WHERE category LIKE 'S_SKILL_%'
        ORDER BY category, correct_name
    """)
    effects = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return effects

@lru_cache(maxsize=None)
def get_alien_effects():
    """
    (新) エイリアンごとの効果リストを構築する（個性別）
    フェーズ2: targetとcondition_targetの情報も含める
    
    戻り値: {alien_id: {'1': [{effect_name, target, condition_target}], '2': [...], '3': [...]}} の形式
    """
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)
    
    # skill_text_verified_effectsテーブルから効果名、target、condition_target、effect_type、category、requirement情報を取得
    cur.execute("""
        SELECT skill_text, effect_name, target, condition_target, 
               effect_type, category, has_requirement, requirement_details, requirement_count
        FROM skill_text_verified_effects
        WHERE effect_name IS NOT NULL
        ORDER BY skill_text, effect_name
    """)
    rows = cur.fetchall()
    
    # correct_effect_namesからshow_targetとshow_condition_targetを取得
    cur.execute("""
        SELECT correct_name, category, show_target, show_condition_target
        FROM correct_effect_names
    """)
    show_flags = {}
    for flag_row in cur.fetchall():
        key = (flag_row['correct_name'], flag_row['category'] or '')
        show_flags[key] = {
            'show_target': flag_row['show_target'] if flag_row['show_target'] is not None else True,
            'show_condition_target': flag_row['show_condition_target'] if flag_row['show_condition_target'] is not None else True
        }
    
    # skill_textをキーにした効果情報の辞書を作成
    effects_by_text = {}
    for row in rows:
        skill_text = row['skill_text']
        effect_name = row['effect_name']
        category = row['category'] or ''
        # correct_effect_namesからshow_targetとshow_condition_targetを取得
        flag_key = (effect_name, category)
        flags = show_flags.get(flag_key, {'show_target': True, 'show_condition_target': True})
        effect_info = {
            'effect_name': effect_name,
            'target': row['target'] or '',
            'condition_target': row['condition_target'] or '',
            'effect_type': row['effect_type'] or '',
            'category': category,
            'has_requirement': row['has_requirement'] or False,
            'requirement_details': row['requirement_details'] or '',
            'requirement_count': row['requirement_count'] or 0,
            'show_target': flags['show_target'],
            'show_condition_target': flags['show_condition_target']
        }
        if skill_text not in effects_by_text:
            effects_by_text[skill_text] = []
        effects_by_text[skill_text].append(effect_info)
    
    cur.close()
    conn.close()
    
    # エイリアンデータを取得（skill_text1-3を参照するため）
    all_aliens_dict = get_all_aliens()
    
    # エイリアンごとの効果リストを構築（個性別 + 特技）
    alien_effects = {}
    for alien_id, alien_data in all_aliens_dict.items():
        alien_effects[alien_id] = {
            '1': [],
            '2': [],
            '3': [],
            'S': []  # 特技
        }
        # 個性1-3の効果を個別に集める
        for skill_num in [1, 2, 3]:
            skill_text = alien_data.get(f'skill_text{skill_num}')
            if skill_text and skill_text in effects_by_text:
                alien_effects[alien_id][str(skill_num)] = effects_by_text[skill_text]
        
        # 特技の効果を集める
        s_skill_text = alien_data.get('s_skill_text')
        if s_skill_text and s_skill_text in effects_by_text:
            alien_effects[alien_id]['S'] = effects_by_text[s_skill_text]
    
    return alien_effects

@app.route('/')
def index():
    try:
        # 1. 辞書として全エイリアンデータを取得 (JSが使用)
        all_aliens_dict = get_all_aliens() 
        
        # 2. (★重要★) Jinjaの {% for alien in aliens %} のために、
        #    辞書から「リスト」を作成する
        aliens_list_for_template = sorted(all_aliens_dict.values(), key=lambda x: x['id'])

        # 3. 新しい要求データを取得
        requirements_by_text = get_all_skill_requirements_new()
        
        # 4. ALIEN_SKILL_DATA の構築
        alien_skill_data = {}
        for alien_id, alien_data in all_aliens_dict.items():
            alien_skill_data[alien_id] = {
                "1": requirements_by_text.get(alien_data.get('skill_text1'), []),
                "2": requirements_by_text.get(alien_data.get('skill_text2'), []),
                "3": requirements_by_text.get(alien_data.get('skill_text3'), [])
            }
        
        # 5. 効果辞書も取得（個性用）
        all_effects = get_correct_effect_names()
        
        # 5-2. 特技用効果辞書も取得
        s_skill_effects = get_s_skill_effect_names()
        
        # 6. エイリアンごとの効果リストを取得
        alien_effects = get_alien_effects()

    except psycopg2.Error as e:
        app.logger.error(f"Database error: {e}")
        return "データベース接続エラーが発生しました。", 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred: {e}")
        return "サーバーエラーが発生しました。", 500

    # (★重要★) render_template に 'aliens' と 'all_aliens' の両方を渡す
    return render_template('index.html', 
                           # 1. Jinjaの が使うエイリアン「リスト」
                           aliens=aliens_list_for_template, 
                           
                           # 2. JavaScript が使うエイリアン「辞書」
                           all_aliens=all_aliens_dict, 
                           
                           # 3. 新しい要求データ
                           alien_skill_data=alien_skill_data,
                           
                           # 4. 効果絞り込み用データ（個性用）
                           all_effects=all_effects,
                           
                           # 4-2. 特技用効果絞り込みデータ
                           s_skill_effects=s_skill_effects,
                           
                           # 5. エイリアンごとの効果リスト（絞り込み用）
                           alien_effects=alien_effects
                           )

# ============================================================================
# 管理機能API: 認証
# ============================================================================
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    """管理モードへのログイン"""
    try:
        data = request.json
        password = data.get('password')
        
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session.permanent = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'パスワードが正しくありません'}), 401
    except Exception as e:
        app.logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    """管理モードからログアウト"""
    session.pop('admin_logged_in', None)
    return jsonify({'success': True})

@app.route('/api/admin/check-auth', methods=['GET'])
def api_admin_check_auth():
    """認証状態を確認"""
    return jsonify({'logged_in': check_admin()})

@app.route('/api/admin/trigger-full-scrape', methods=['POST'])
@require_admin
def api_admin_trigger_full_scrape():
    """全体スクレイピングを非同期で実行（管理モード専用）"""
    global _background_process_running, _background_process_type, _background_process_start_time
    
    try:
        # 排他制御チェック
        with _background_process_lock:
            if _background_process_running:
                elapsed = ""
                if _background_process_start_time:
                    elapsed_sec = (datetime.now() - _background_process_start_time).total_seconds()
                    elapsed = f"（経過時間: {int(elapsed_sec)}秒）"
                return jsonify({
                    'success': False,
                    'error': f'別の処理が実行中です: {_background_process_type}{elapsed}'
                }), 409
            
            # ロックを取得
            _background_process_running = True
            _background_process_type = "全体スクレイピング"
            _background_process_start_time = datetime.now()
        
        scraping_url = os.environ.get('SCRAPING_BASE_URL')
        discord_webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
        
        if not scraping_url:
            # ロック解除
            with _background_process_lock:
                _background_process_running = False
                _background_process_type = None
                _background_process_start_time = None
            return jsonify({'success': False, 'error': 'SCRAPING_BASE_URLが設定されていません'}), 500
        
        # 開始通知を送信
        if discord_webhook_url:
            try:
                from scripts.utils.discord_notifier import DiscordNotifier
                notifier = DiscordNotifier(discord_webhook_url)
                notifier.send_info(
                    "全体スクレイピングを開始しました。\n処理はバックグラウンドで実行されます。",
                    details={"モード": "全体スクレイピング（手動実行）"}
                )
            except Exception as e:
                app.logger.warning(f"開始通知の送信に失敗しました: {e}")
        
        def run_scraping():
            """バックグラウンドでスクレイピングを実行"""
            global _background_process_running, _background_process_type, _background_process_start_time
            try:
                env = build_scraper_subprocess_env()
                cmd = [
                    sys.executable,
                    str(PROJECT_ROOT / 'scripts' / 'run_automated_update.py'),
                    '--url', scraping_url,
                    '--full-scrape',
                ]
                if discord_webhook_url:
                    cmd.extend(['--discord-webhook', discord_webhook_url])
                
                # サブプロセスでスクレイピングを実行
                result = subprocess.run(
                    cmd,
                    env=env,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    app.logger.error(f"Full scrape subprocess error: {result.stderr}")
                else:
                    app.logger.info(f"Full scrape completed successfully")
                
            except Exception as e:
                app.logger.error(f"Full scrape error: {e}")
            finally:
                # 処理完了後にロック解除
                with _background_process_lock:
                    _background_process_running = False
                    _background_process_type = None
                    _background_process_start_time = None
        
        # バックグラウンドスレッドで実行
        thread = threading.Thread(target=run_scraping)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': '全体スクレイピングを開始しました。処理はバックグラウンドで実行されます。'
        })
        
    except Exception as e:
        # エラー時もロック解除
        with _background_process_lock:
            _background_process_running = False
            _background_process_type = None
            _background_process_start_time = None
        app.logger.error(f"Trigger full scrape error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/trigger-partial-scrape', methods=['POST'])
@require_admin
def api_admin_trigger_partial_scrape():
    """指定IDのみスクレイピングを非同期で実行"""
    global _background_process_running, _background_process_type, _background_process_start_time
    
    data = request.json or {}
    try:
        ids = parse_id_list(data.get('ids'))
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    
    # 排他制御チェック
    with _background_process_lock:
        if _background_process_running:
            elapsed = ""
            if _background_process_start_time:
                elapsed_sec = (datetime.now() - _background_process_start_time).total_seconds()
                elapsed = f"（経過時間: {int(elapsed_sec)}秒）"
            return jsonify({
                'success': False,
                'error': f'別の処理が実行中です: {_background_process_type}{elapsed}'
            }), 409
        
        # ロックを取得
        _background_process_running = True
        _background_process_type = "部分スクレイピング"
        _background_process_start_time = datetime.now()
    
    scraping_url = os.environ.get('SCRAPING_BASE_URL')
    discord_webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
    if not scraping_url:
        # ロック解除
        with _background_process_lock:
            _background_process_running = False
            _background_process_type = None
            _background_process_start_time = None
        return jsonify({'success': False, 'error': 'SCRAPING_BASE_URLが設定されていません'}), 500
    
    ids_arg = ','.join(str(i) for i in ids)
    
    if discord_webhook_url:
        try:
            from scripts.utils.discord_notifier import DiscordNotifier
            notifier = DiscordNotifier(discord_webhook_url)
            notifier.send_info(
                "部分スクレイピングを開始しました。\n処理はバックグラウンドで実行されます。",
                details={
                    "モード": "部分スクレイピング（手動実行）",
                    "対象ID": ids_arg
                }
            )
        except Exception as e:
            app.logger.warning(f"開始通知の送信に失敗しました: {e}")
    
    def run_partial():
        global _background_process_running, _background_process_type, _background_process_start_time
        try:
            env = build_scraper_subprocess_env()
            cmd = [
                sys.executable,
                str(PROJECT_ROOT / 'scripts' / 'run_automated_update.py'),
                '--url', scraping_url,
                '--scrape-ids', ids_arg
            ]
            if discord_webhook_url:
                cmd.extend(['--discord-webhook', discord_webhook_url])
            
            # サブプロセスでスクレイピングを実行
            result = subprocess.run(
                cmd,
                env=env,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                app.logger.error(f"Partial scrape subprocess error: {result.stderr}")
            else:
                app.logger.info(f"Partial scrape completed successfully")

        except Exception as e:
            app.logger.error(f"Partial scrape error: {e}")
        finally:
            # 処理完了後にロック解除
            with _background_process_lock:
                _background_process_running = False
                _background_process_type = None
                _background_process_start_time = None
    
    thread = threading.Thread(target=run_partial, daemon=True)
    thread.start()
    
    return jsonify({
        'success': True,
        'message': '部分スクレイピングを開始しました。処理はバックグラウンドで実行されます。'
    })

@app.route('/api/bug-report', methods=['POST'])
def api_bug_report():
    """不具合報告をDiscordに送信（匿名）"""
    try:
        data = request.json
        report_text = data.get('text', '').strip()
        
        if not report_text:
            return jsonify({'success': False, 'error': '報告内容が空です'}), 400
        
        # Discord Webhook URLを環境変数から取得
        webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
        if not webhook_url:
            app.logger.error("Discord Webhook URLが設定されていません")
            return jsonify({'success': False, 'error': 'サーバー設定エラー'}), 500
        
        # DiscordNotifierを使用して送信
        from scripts.utils.discord_notifier import DiscordNotifier
        notifier = DiscordNotifier(webhook_url)
        
        # メッセージを送信（シンプルな形式）
        success = notifier.send_message(
            content=f"【不具合】\n\n{report_text}",
            timestamp=False  # タイムスタンプなし
        )
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': '送信に失敗しました'}), 500
            
    except Exception as e:
        app.logger.error(f"Bug report error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# 管理機能API: データ取得
# ============================================================================
@app.route('/api/admin/get-effects/<skill_text>')
@require_admin
def api_admin_get_effects(skill_text):
    """指定したskill_textの効果を取得"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        cur.execute("""
            SELECT skill_text, effect_name, effect_type, category, target, 
                   condition_target, has_requirement, requirement_details, 
                   requirement_count, requires_awakening
            FROM skill_text_verified_effects
            WHERE skill_text = %s
            ORDER BY effect_name
        """, (skill_text,))
        
        effects = [dict(row) for row in cur.fetchall()]
        
        # このskill_textを使用するエイリアンを取得
        cur.execute("""
            SELECT id, name
            FROM alien
            WHERE skill_text1 = %s OR skill_text2 = %s OR skill_text3 = %s OR "S_Skill_text" = %s
            ORDER BY id
        """, (skill_text, skill_text, skill_text, skill_text))
        
        aliens = [dict(row) for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'effects': effects,
            'aliens': aliens
        })
    except Exception as e:
        app.logger.error(f"API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/get-unregistered')
@require_admin
def api_admin_get_unregistered():
    """辞書にない効果を取得"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        # 全効果名を取得
        cur.execute("""
            SELECT DISTINCT effect_name
            FROM skill_text_verified_effects
            WHERE effect_name IS NOT NULL
            ORDER BY effect_name
        """)
        all_db_effects = [row['effect_name'] for row in cur.fetchall()]
        
        # 辞書に登録されている効果名を取得
        cur.execute("""
            SELECT DISTINCT correct_name
            FROM correct_effect_names
        """)
        registered_effects = set(row['correct_name'] for row in cur.fetchall())
        
        # 未登録効果を検出
        unregistered = [e for e in all_db_effects if e not in registered_effects]
        
        # 使用件数とskill_textを取得
        cur.execute("""
            SELECT effect_name, COUNT(*) as usage_count, 
                   ARRAY_AGG(DISTINCT skill_text) as skill_texts
            FROM skill_text_verified_effects
            WHERE effect_name = ANY(%s)
            GROUP BY effect_name
        """, (unregistered,))
        
        unregistered_with_stats = []
        for row in cur.fetchall():
            unregistered_with_stats.append({
                'effect_name': row['effect_name'],
                'usage_count': row['usage_count'],
                'skill_texts': row['skill_texts']
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'effects': unregistered_with_stats
        })
    except Exception as e:
        app.logger.error(f"API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# 管理機能API: 変更管理
# ============================================================================
def create_backup(skill_text=None):
    """バックアップを作成（追記形式）"""
    try:
        backup_dir = PROJECT_ROOT / 'backups'
        backup_dir.mkdir(exist_ok=True)
        
        # 固定ファイル名でバックアップ（追記形式）
        backup_path = backup_dir / 'skill_verified_effects_backup.jsonl'
        
        timestamp = datetime.now().strftime('%Y%m%dT%H%M%S')
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        if skill_text:
            cur.execute("""
                SELECT * FROM skill_text_verified_effects
                WHERE skill_text = %s
            """, (skill_text,))
        else:
            cur.execute("SELECT * FROM skill_text_verified_effects")
        
        # 追記モードで開く
        with open(backup_path, 'a', encoding='utf-8') as f:
            # タイムスタンプマーカーを追加
            f.write(json.dumps({'__backup_timestamp__': timestamp}, ensure_ascii=False) + '\n')
            # データを追記
            for row in cur.fetchall():
                f.write(json.dumps(dict(row), ensure_ascii=False, default=str) + '\n')
        
        cur.close()
        conn.close()
        
        app.logger.info(f"Backup appended: {backup_path} (timestamp: {timestamp})")
        return True
    except Exception as e:
        app.logger.error(f"Backup error: {e}")
        return False

@app.route('/api/admin/apply-changes', methods=['POST'])
@require_admin
def api_admin_apply_changes():
    """変更を一括でDBに適用"""
    try:
        data = request.json
        changes = data.get('changes', [])
        
        if not changes:
            return jsonify({'success': False, 'error': '変更がありません'}), 400
        
        # バックアップ作成（影響を受けるskill_textを収集）
        affected_skill_texts = set()
        for change in changes:
            if 'skill_text' in change:
                affected_skill_texts.add(change['skill_text'])
        
        # 全体バックアップを作成（一括適用のため）
        create_backup()
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        applied_count = 0
        change_details = []  # 変更内容の詳細を記録
        
        for change in changes:
            change_type = change.get('type')
            skill_text = change.get('skill_text')
            effect_name = change.get('effect_name')
            
            try:
                if change_type == 'add':
                    # 効果を追加
                    effect_data = change.get('data', {})
                    effect_name_to_add = effect_data.get('effect_name')
                    cur.execute("""
                        INSERT INTO skill_text_verified_effects 
                        (skill_text, effect_name, effect_type, category, target, 
                         condition_target, has_requirement, requirement_details, 
                         requirement_count, requires_awakening)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        skill_text,
                        effect_name_to_add,
                        effect_data.get('effect_type'),
                        effect_data.get('category'),
                        effect_data.get('target'),
                        effect_data.get('condition_target'),
                        effect_data.get('has_requirement', False),
                        effect_data.get('requirement_details'),
                        effect_data.get('requirement_count'),
                        effect_data.get('requires_awakening')
                    ))
                    change_details.append({
                        'type': 'add',
                        'skill_text': skill_text,
                        'effect_name': effect_name_to_add,
                        'success': True
                    })
                    applied_count += 1
                    
                elif change_type == 'update':
                    # 効果を更新
                    updates = change.get('data', {})
                    new_effect_name = change.get('new_effect_name') or effect_name
                    
                    if updates:
                        # 同じskill_textとeffect_nameのレコードをすべて削除してから新規にINSERT
                        # これにより、不正な値（例：「味方」）が残らないようにする
                        cur.execute("""
                            DELETE FROM skill_text_verified_effects
                            WHERE skill_text = %s AND effect_name = %s
                        """, (skill_text, effect_name))
                        
                        # 変更した内容を含めて新規にINSERT
                        cur.execute("""
                            INSERT INTO skill_text_verified_effects 
                            (skill_text, effect_name, effect_type, category, target, 
                             condition_target, has_requirement, requirement_details, 
                             requirement_count, requires_awakening)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            skill_text,
                            new_effect_name,
                            updates.get('effect_type'),
                            updates.get('category'),
                            updates.get('target'),
                            updates.get('condition_target'),
                            updates.get('has_requirement', False),
                            updates.get('requirement_details'),
                            updates.get('requirement_count'),
                            updates.get('requires_awakening')
                        ))
                        
                        change_details.append({
                            'type': 'update',
                            'skill_text': skill_text,
                            'old_effect_name': effect_name,
                            'new_effect_name': new_effect_name,
                            'success': True
                        })
                        applied_count += 1
                        
                elif change_type == 'delete':
                    # 効果を削除
                    cur.execute("""
                        DELETE FROM skill_text_verified_effects
                        WHERE skill_text = %s AND effect_name = %s
                    """, (skill_text, effect_name))
                    change_details.append({
                        'type': 'delete',
                        'skill_text': skill_text,
                        'effect_name': effect_name,
                        'success': True
                    })
                    applied_count += 1
                    
            except Exception as e:
                app.logger.error(f"Error applying change {change_type}: {e}")
                change_details.append({
                    'type': change_type,
                    'skill_text': skill_text,
                    'effect_name': effect_name,
                    'success': False,
                    'error': str(e)
                })
                conn.rollback()
                cur.close()
                conn.close()
                return jsonify({
                    'success': False, 
                    'error': f'変更の適用に失敗しました: {str(e)}',
                    'change_details': change_details
                }), 500
        
        conn.commit()
        cur.close()
        conn.close()
        
        # キャッシュクリア
        try:
            if hasattr(get_all_aliens, 'cache_clear'):
                get_all_aliens.cache_clear()
            if hasattr(get_all_skill_requirements_new, 'cache_clear'):
                get_all_skill_requirements_new.cache_clear()
            if hasattr(get_correct_effect_names, 'cache_clear'):
                get_correct_effect_names.cache_clear()
            if hasattr(get_s_skill_effect_names, 'cache_clear'):
                get_s_skill_effect_names.cache_clear()
            if hasattr(get_alien_effects, 'cache_clear'):
                get_alien_effects.cache_clear()
        except Exception as e:
            app.logger.warning(f"Cache clear warning: {e}")
        
        return jsonify({
            'success': True,
            'applied_count': applied_count,
            'change_details': change_details
        })
    except Exception as e:
        app.logger.error(f"Apply changes error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# 管理機能API: 辞書管理
# ============================================================================
@app.route('/api/admin/dictionary/add', methods=['POST'])
@require_admin
def api_admin_dictionary_add():
    """辞書に効果名を追加"""
    try:
        data = request.json
        effect_name = data.get('effect_name')
        effect_type = data.get('effect_type')
        category = data.get('category')
        
        if not effect_name:
            return jsonify({'success': False, 'error': '効果名が指定されていません。'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # correct_effect_namesテーブルに追加（複合主キー対応）
        target = data.get('target')
        condition_target = data.get('condition_target')
        show_target = data.get('show_target', True)
        show_condition_target = data.get('show_condition_target', True)
        
        cur.execute("""
            INSERT INTO correct_effect_names (correct_name, effect_type, category, target, condition_target, show_target, show_condition_target, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (correct_name, category) DO UPDATE
            SET effect_type = EXCLUDED.effect_type,
                target = EXCLUDED.target,
                condition_target = EXCLUDED.condition_target,
                show_target = EXCLUDED.show_target,
                show_condition_target = EXCLUDED.show_condition_target
        """, (effect_name, effect_type, category, target, condition_target, show_target, show_condition_target, datetime.now()))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # キャッシュクリア
        try:
            if hasattr(get_correct_effect_names, 'cache_clear'):
                get_correct_effect_names.cache_clear()
            if hasattr(get_s_skill_effect_names, 'cache_clear'):
                get_s_skill_effect_names.cache_clear()
        except Exception as e:
            app.logger.warning(f"Cache clear warning: {e}")
        
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Dictionary add error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/dictionary/update-show-flags', methods=['POST'])
@require_admin
def api_admin_dictionary_update_show_flags():
    """効果名のtarget/condition_target表示フラグを更新"""
    try:
        data = request.json
        effect_name = data.get('effect_name')
        category = data.get('category')
        show_target = data.get('show_target')
        show_condition_target = data.get('show_condition_target')
        
        if not effect_name or category is None:
            return jsonify({'success': False, 'error': '効果名とカテゴリが指定されていません。'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE correct_effect_names
            SET show_target = %s, show_condition_target = %s
            WHERE correct_name = %s AND category = %s
        """, (show_target, show_condition_target, effect_name, category))
        
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            conn.close()
            return jsonify({'success': False, 'error': '効果名が見つかりませんでした。'}), 404
        
        conn.commit()
        cur.close()
        conn.close()
        
        # キャッシュクリア
        try:
            if hasattr(get_correct_effect_names, 'cache_clear'):
                get_correct_effect_names.cache_clear()
            if hasattr(get_s_skill_effect_names, 'cache_clear'):
                get_s_skill_effect_names.cache_clear()
            if hasattr(get_alien_effects, 'cache_clear'):
                get_alien_effects.cache_clear()
        except Exception as e:
            app.logger.warning(f"Cache clear warning: {e}")
        
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Update show flags error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/dictionary/mass-update', methods=['POST'])
@require_admin
def api_admin_dictionary_mass_update():
    """未登録効果を一括で辞書登録された効果名に置き換え"""
    try:
        data = request.json
        old_effect_name = data.get('old_effect_name')
        new_effect_name = data.get('new_effect_name')
        skill_texts = data.get('skill_texts', [])
        
        if not old_effect_name or not new_effect_name:
            return jsonify({'success': False, 'error': '効果名が指定されていません。'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        # バックアップ作成（追記形式）
        timestamp = datetime.now().strftime('%Y%m%dT%H%M%S')
        backup_dir = PROJECT_ROOT / 'backups'
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / 'skill_verified_effects_backup.jsonl'
        
        # 更新対象のデータを取得してバックアップ
        cur.execute("""
            SELECT * FROM skill_text_verified_effects
            WHERE effect_name = %s AND skill_text = ANY(%s)
        """, (old_effect_name, skill_texts))
        
        with open(backup_path, 'a', encoding='utf-8') as f:
            # タイムスタンプマーカーを追加（一括置換の場合はメタ情報も含める）
            meta_info = {
                '__backup_timestamp__': timestamp,
                '__backup_type__': 'mass_update',
                '__old_effect_name__': old_effect_name,
                '__new_effect_name__': new_effect_name
            }
            f.write(json.dumps(meta_info, ensure_ascii=False) + '\n')
            # データを追記
            for row in cur.fetchall():
                f.write(json.dumps(dict(row), ensure_ascii=False, default=str) + '\n')
        
        # 一括更新
        cur.execute("""
            UPDATE skill_text_verified_effects
            SET effect_name = %s
            WHERE effect_name = %s AND skill_text = ANY(%s)
        """, (new_effect_name, old_effect_name, skill_texts))
        
        updated_count = cur.rowcount
        
        conn.commit()
        cur.close()
        conn.close()
        
        app.logger.info(f"Mass update backup created: {backup_path}")
        
        # キャッシュクリア
        try:
            if hasattr(get_all_aliens, 'cache_clear'):
                get_all_aliens.cache_clear()
            if hasattr(get_all_skill_requirements_new, 'cache_clear'):
                get_all_skill_requirements_new.cache_clear()
            if hasattr(get_correct_effect_names, 'cache_clear'):
                get_correct_effect_names.cache_clear()
            if hasattr(get_s_skill_effect_names, 'cache_clear'):
                get_s_skill_effect_names.cache_clear()
            if hasattr(get_alien_effects, 'cache_clear'):
                get_alien_effects.cache_clear()
        except Exception as e:
            app.logger.warning(f"Cache clear warning: {e}")
        
        return jsonify({'success': True, 'updated_count': updated_count})
    except Exception as e:
        app.logger.error(f"Mass update error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/validate-targets', methods=['POST'])
@require_admin
def api_admin_validate_targets():
    """target/condition_targetのバリデーション"""
    try:
        data = request.json
        targets = data.get('targets', [])  # [{'target': '...', 'condition_target': '...'}, ...]
        
        invalid_items = []
        
        # 有効なtarget値（文字列形式）
        valid_target_strings = {'自分', '味方全員', '敵全員', '敵単体'}
        
        # 有効なコード形式のパターン
        valid_code_patterns = {
            'a': ['1', '2', '3', '4'],  # 属性
            'c': ['1', '2'],  # 攻撃はんい
            'd': ['1', '2', '3'],  # 攻撃距離
            'boss': ['1'],  # ボスタイプ
        }
        
        for item in targets:
            target = item.get('target', '')
            condition_target = item.get('condition_target', '')
            
            invalid_targets = []
            invalid_conditions = []
            
            # targetの検証
            if target:
                if target not in valid_target_strings:
                    # コード形式をチェック
                    parts = [p.strip() for p in target.split(',')]
                    for part in parts:
                        if ':' not in part:
                            invalid_targets.append(part)
                        else:
                            cat, val = part.split(':', 1)
                            if cat not in valid_code_patterns or val not in valid_code_patterns[cat]:
                                invalid_targets.append(part)
            
            # condition_targetの検証（コード形式のみ）
            if condition_target:
                parts = [p.strip() for p in condition_target.split(',')]
                for part in parts:
                    if ':' not in part:
                        invalid_conditions.append(part)
                    else:
                        cat, val = part.split(':', 1)
                        if cat not in valid_code_patterns or val not in valid_code_patterns[cat]:
                            invalid_conditions.append(part)
            
            if invalid_targets or invalid_conditions:
                invalid_items.append({
                    'target': target,
                    'condition_target': condition_target,
                    'invalid_targets': invalid_targets,
                    'invalid_conditions': invalid_conditions
                })
        
        return jsonify({
            'success': True,
            'invalid_items': invalid_items,
            'is_valid': len(invalid_items) == 0
        })
    except Exception as e:
        app.logger.error(f"Validation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/get-effect-info/<effect_name>')
@require_admin
def api_admin_get_effect_info(effect_name):
    """効果名からeffect_typeとcategoryを取得"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        # 辞書から取得（個性用と特技用の両方を取得）
        cur.execute("""
            SELECT correct_name, effect_type, category
            FROM correct_effect_names
            WHERE correct_name = %s
            ORDER BY category
        """, (effect_name,))
        
        results = [dict(row) for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        if results:
            # 複数のカテゴリがある場合（個性用と特技用で異なる場合）
            return jsonify({
                'success': True,
                'effect_name': effect_name,
                'options': results  # [{effect_type, category}, ...]
            })
        else:
            # 辞書にない場合はNoneを返す
            return jsonify({
                'success': True,
                'effect_name': effect_name,
                'options': []
            })
    except Exception as e:
        app.logger.error(f"Get effect info error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/check-skill-type/<skill_text>')
@require_admin
def api_admin_check_skill_type(skill_text):
    """skill_textが特技か個性かを判定"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        # 特技として登録されているかチェック
        cur.execute("""
            SELECT COUNT(*) > 0 as is_special
            FROM alien
            WHERE "S_Skill_text" = %s 
              AND "S_Skill_text" IS NOT NULL 
              AND "S_Skill_text" != 'なし'
        """, (skill_text,))
        
        is_special = bool(cur.fetchone()[0])
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'is_special': is_special
        })
    except Exception as e:
        app.logger.error(f"Check skill type error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/admin/get-effect-usage')
@require_admin
def api_admin_get_effect_usage():
    """効果名ごとの使用数を取得"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=DictCursor)
        
        cur.execute("""
            SELECT effect_name, COUNT(*) as usage_count
            FROM skill_text_verified_effects
            GROUP BY effect_name
        """)
        
        usage_stats = {row['effect_name']: row['usage_count'] for row in cur.fetchall()}
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'usage_stats': usage_stats
        })
    except Exception as e:
        app.logger.error(f"Effect usage error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False)