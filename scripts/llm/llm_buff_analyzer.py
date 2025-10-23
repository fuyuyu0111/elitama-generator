"""LLM を用いて個性テキストからバフ・デバフ効果を抽出し skill_complete テーブルへ保存するスクリプト。"""

import os
import time
import json
import psycopg2
from psycopg2.extras import Json, execute_values
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
import argparse
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from scripts.db.backup_skill_complete import backup_skill_complete

# --- 定数と設定 ---
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ★★★ モデル優先順位と制限 ★★★
MODEL_PRIORITY = [
    {'name': 'gemini-2.5-flash-lite', 'rpd': 1000, 'rpm': 15},
    {'name': 'gemini-2.0-flash-lite', 'rpd': 200,  'rpm': 30},
    {'name': 'gemini-2.5-flash',      'rpd': 250,  'rpm': 10},
    {'name': 'gemini-2.0-flash',      'rpd': 200,  'rpm': 15},
    {'name': 'gemini-2.5-pro',        'rpd': 50,   'rpm': 2},
]

# サポートされているトリガータイミング
SUPPORTED_TRIGGER_TIMINGS = {
    "BATTLE_START",
    "BATTLE_START_CONDITIONAL",
    "ON_FIRST_ATTACK",
    "ON_ATTACK",
    "ON_DAMAGED",
    "ON_DAMAGE_TAKEN",
    "ON_DEFEATED",
    "ON_ALLY_DEFEATED",
    "ON_KILL",
    "ON_HP_BELOW",
    "ON_HP_ABOVE",
    "ON_TIMER",
    "ON_COUNT",
    "WHILE_ALIVE",
    "WHILE_CONDITION",
}

# プロンプトテンプレート
PROMPT_TEMPLATE = """
あなたはゲーム「エイリアンのたまご」の個性テキストを構造化データに変換する専門家です。
以下の JSON スキーマを厳密に満たす出力のみを返してください。追加の文章や説明は禁止です。

出力フォーマット:
{
  "results": [
    {
      "alien_id": <int>,
      "skill_number": <1|2|3>,
      "requirements": [
        {
          "type": "a|b|c|d|e|f",
          "value": "<string>",
          "count": <int>
        }
      ],
      "effects": [
        {
          "group_id": <int>,
          "name": "<string>",
          "target": "SELF|ALL_ALLIES|ALL_ENEMIES|RANDOM_ALLY|RANDOM_ENEMY|SLOT_ALLY|SLOT_ENEMY|OTHER",
          "value": <number|null>,
          "unit": "PERCENT|FLAT|SECONDS|COUNT|NONE|STACK|MULTIPLIER",
          "duration": <int>,
          "probability": <int>,
          "occupies_slot": <true|false|null>,
          "is_debuff": <true|false>,
          "awakening_required": <true|false>,
          "trigger_timing": "BATTLE_START|BATTLE_START_CONDITIONAL|ON_FIRST_ATTACK|ON_ATTACK|ON_DAMAGED|ON_DAMAGE_TAKEN|ON_DEFEATED|ON_ALLY_DEFEATED|ON_KILL|ON_HP_BELOW|ON_HP_ABOVE|ON_TIMER|ON_COUNT|WHILE_ALIVE|WHILE_CONDITION|OTHER",
          "trigger_condition": <object|null>,
          "applies_to_requirements_only": <true|false>
        }
      ]
    }
  ]
}

# ルール
1. JSON 以外のテキストを出力しない。
2. requirements は味方パーティ構成が発動条件の場合のみ追加。条件が無ければ空配列。
3. requirement の type: 属性=a, 所属=b, 攻撃範囲=c, 攻撃距離=d, タイプ=e, ロール=f。
   - 攻撃範囲 (c) の value: たんたい=1, はんい=2。
   - 攻撃距離 (d) の value: ちかい=1, ふつう=2, とおい=3。
4. effect.group_id はスキル内で 1 から連番。複数効果が同時発動するブロックは同じ group_id。
5. target, unit は指定された語から選択。該当が無ければ target="OTHER", unit="NONE"。
6. probability, duration は整数 (無ければ 0, 100 とする)。
7. occupies_slot が判断できない、または枠を使わない場合は null を許可する。
8. trigger_condition は追加の発動条件を JSON オブジェクトで表現 (例: {"hp_threshold": 50})。無ければ null。
9. applies_to_requirements_only は、要求条件を満たした時だけ発動する効果なら true、それ以外は false。
10. 値が不明な場合は null ではなく適切な既定値を入れる (value 不明時のみ null 可)。
11. requirement.value は以下のマッピングに従い英数字のみを使用し、大文字を保持すること。
    - 属性 (a): 動物=1, 昆虫=2, 機械=3, ナゾ=4。
    - 所属 (b): 宇宙連合=1, 星間帝国=2, 恒星連邦=3, unknown=4, 銀河同盟=5。
    - タイプ (e): 海=A, 夜=B, 氷=C, ラブ=D, 空=E, 音=F, 魔術=G, 熱=H, 大和=I, エレメント=J, 新星=K, チケット=L, 突然変異=M, 競技=N, 盗賊=O, 祈祷=P, 闇=Q, パックマンシリーズまたはカタログIP=AA。
12. 「◯◯以外」の条件は requirement.value の末尾に感嘆符を付ける (例: 動物以外 → "1!", 熱タイプ以外 → "H!")。
13. requirements 配列は最大でも発動条件に直接必要な項目のみを含め、役割など別条件が同時にある場合はそれぞれ個別の要素とする。

# 例
- 「味方に機械属性以外が3体いると」 → {"type": "a", "value": "3!", "count": 3}
- 「味方に攻撃距離：ふつうが2体」 → {"type": "d", "value": "2", "count": 2}
- 「味方に熱タイプが1体以上」 → {"type": "e", "value": "H", "count": 1}
- 「距離：とおい以外がいると」 → {"type": "d", "value": "3!", "count": 1}
- 「味方に機械属性が2体いると」 → {"type": "a", "value": "3", "count": 2}
- 「味方に銀河同盟がいると」 → {"type": "b", "value": "5", "count": 1}

# Few-shot 参考例
[{
    "alien_id": 99901,
    "skill_number": 1,
    "requirements": [
        {"type": "d", "value": "3", "count": 1}
    ],
    "effects": [
        {
            "group_id": 1,
            "name": "つよさアップ",
            "target": "ALL_ALLIES",
            "value": 30,
            "unit": "PERCENT",
            "duration": 0,
            "probability": 100,
            "occupies_slot": true,
            "is_debuff": false,
            "awakening_required": false,
            "trigger_timing": "WHILE_ALIVE",
            "trigger_condition": null,
            "applies_to_requirements_only": true
        }
    ]
},
{
    "alien_id": 99902,
    "skill_number": 1,
    "requirements": [],
    "effects": [
        {
            "group_id": 1,
            "name": "特技ダメアップ",
            "target": "SELF",
            "value": 100,
            "unit": "PERCENT",
            "duration": 0,
            "probability": 100,
            "occupies_slot": true,
            "is_debuff": false,
            "awakening_required": false,
            "trigger_timing": "WHILE_ALIVE",
            "trigger_condition": null,
            "applies_to_requirements_only": false
        }
    ]
}]

# 処理対象:
<<PAYLOAD>>
"""


def resolve_api_key() -> str:
    """環境変数からAPIキーを取得"""
    env_candidates = [
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY",
    ]
    for env_name in env_candidates:
        value = os.getenv(env_name)
        if value:
            print(f"APIキーを環境変数 {env_name} から読み込みました。")
            return value
    raise RuntimeError("APIキーが環境変数に設定されていません。")


def get_db_connection():
    """データベース接続を取得"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("環境変数 'DATABASE_URL' が設定されていません。")
    return psycopg2.connect(db_url, sslmode='require')


def fetch_aliens_from_db(conn, limit: Optional[int] = None, offset: int = 0):
    """alien テーブルから個性データを取得"""
    query = "SELECT id, skill_text1, skill_text2, skill_text3 FROM alien ORDER BY id"
    params = []
    
    if limit is not None:
        query += " LIMIT %s"
        params.append(int(limit))
    if offset:
        query += " OFFSET %s"
        params.append(int(offset))
    
    with conn.cursor() as cur:
        cur.execute(query, params)
        aliens = []
        for row in cur.fetchall():
            aliens.append({
                'id': row[0],
                'skill_text1': row[1],
                'skill_text2': row[2],
                'skill_text3': row[3],
            })
    return aliens


def build_batch_payload(aliens, batch_size: int = 10):
    """複数エイリアンの個性を1つのプロンプトにまとめる"""
    payload_items = []
    for alien in aliens[:batch_size]:
        for skill_num in range(1, 4):
            skill_text = alien.get(f'skill_text{skill_num}', '').strip()
            if skill_text:
                payload_items.append({
                    "alien_id": alien['id'],
                    "skill_number": skill_num,
                    "skill_text": skill_text
                })
    return json.dumps(payload_items, ensure_ascii=False, indent=2)


def call_gemini(model, prompt: str) -> str:
    """Gemini APIを呼び出す"""
    import google.generativeai as genai
    response = model.generate_content(prompt)
    if not response:
        raise RuntimeError("Gemini API応答がNoneです（API側の問題またはレート制限）")
    
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError(f"Gemini API応答にtextが含まれていません。response: {response}")
    
    return text


def parse_llm_response(raw_text: str) -> Dict[str, Any]:
    """LLM応答からJSONをパース"""
    # None または空文字列チェック
    if raw_text is None or not raw_text:
        raise ValueError("LLM応答が空です（None または空文字列）")
    
    cleaned = raw_text.strip().replace('```json', '').replace('```', '').strip()
    # 最後の } または ] を見つけて、それ以降を切り捨てる
    last_brace = max(cleaned.rfind('}'), cleaned.rfind(']'))
    if last_brace != -1:
        cleaned = cleaned[:last_brace + 1]
    
    try:
        parsed = json.loads(cleaned)
        
        # 配列が直接返された場合、{"results": [...]} 形式に変換
        if isinstance(parsed, list):
            return {"results": parsed}
        
        return parsed
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 応答の JSON パースに失敗しました: {exc}\nRAW: {raw_text}")


def estimate_occupies_slot(effect: Dict[str, Any]) -> Optional[bool]:
    """バフ枠使用の簡易推定"""
    name = (effect.get("name") or "").lower()
    target = (effect.get("target") or "").upper()
    if not name:
        return None
    
    keywords_true = [
        "無効", "抵抗", "アップ", "回復", "軽減", "吸収", "徐々に回復", "じどう", "奮迅", "ガード",
    ]
    keywords_false = ["被ダメ軽減", "被ダメージ軽減", "ダメージ軽減", "反射", "カウンター"]
    
    if any(kw in name for kw in keywords_true) and target in {"ALL_ALLIES", "SELF"}:
        return True
    if any(kw in name for kw in keywords_false):
        return False
    if target == "ALL_ALLIES" and "アップ" in name:
        return True
    return None


def normalize_trigger_timing(value: str) -> str:
    """トリガータイミングの正規化"""
    normalized = (value or "").upper().strip()
    if not normalized:
        return "OTHER"
    if normalized in SUPPORTED_TRIGGER_TIMINGS:
        return normalized
    return "OTHER"


def decimal_or_none(value: Any) -> Optional[Decimal]:
    """Decimal変換"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def int_or_default(value: Any, default: int) -> int:
    """int変換"""
    try:
        return int(value)
    except Exception:
        return default


def prepare_effects(parsed: Dict[str, Any], model_name: str) -> List[tuple]:
    """パース結果をDB挿入用のタプルリストに変換"""
    results = parsed.get("results", [])
    rows = []
    analyzed_at = datetime.now(timezone.utc)
    
    for entry in results:
        alien_id = entry.get("alien_id")
        skill_number = entry.get("skill_number")
        requirements = entry.get("requirements") or []
        
        # 要求の取得（最初の1件のみ）
        has_requirement = False
        requirement_type = None
        requirement_value = None
        requirement_count = None
        
        if requirements:
            primary = requirements[0]
            has_requirement = True
            type_value = primary.get("type")
            requirement_type = str(type_value).lower() if type_value is not None else None
            raw_value = primary.get("value")
            requirement_value = str(raw_value).upper() if raw_value is not None else None
            requirement_count = int_or_default(primary.get("count"), 1)
        
        for effect in entry.get("effects") or []:
            group_id = int_or_default(effect.get("group_id"), 0)
            name = str(effect.get("name") or "").strip()
            target = str(effect.get("target") or "OTHER").upper()
            unit = str(effect.get("unit") or "NONE").upper()
            duration = int_or_default(effect.get("duration"), 0)
            probability = int_or_default(effect.get("probability"), 100)
            
            occupies_slot = effect.get("occupies_slot")
            if occupies_slot is None:
                occupies_slot = estimate_occupies_slot(effect)
            
            is_debuff = bool(effect.get("is_debuff", False))
            awakening_required = bool(effect.get("awakening_required", False))
            trigger_timing = normalize_trigger_timing(effect.get("trigger_timing"))
            trigger_condition = effect.get("trigger_condition")
            applies_to_req_only = bool(effect.get("applies_to_requirements_only", False))
            
            rows.append((
                alien_id,
                skill_number,
                group_id,
                name,
                target,
                decimal_or_none(effect.get("value")),
                unit,
                duration,
                probability,
                occupies_slot,
                is_debuff,
                awakening_required,
                trigger_timing,
                Json(trigger_condition) if trigger_condition else None,
                has_requirement,
                requirement_type,
                requirement_value,
                requirement_count,
                "unverified",
                None,
                model_name,
                analyzed_at,
                Json(effect),
                None,
                None,
            ))
    
    return rows


async def process_aliens_parallel(aliens: List[dict], batch_size: int, concurrency: int, model_name: str, model: str, rpm: int) -> tuple:
    """
    Gemini用の準並列処理（RPM制限を厳守、送信タイミングを制御）
    
    リクエストを4.5秒間隔で送信し、回答は並行して待つ。
    None応答のバッチは記録して、最後にまとめて再解析する。
    
    Args:
        aliens: 処理対象のエイリアンリスト
        batch_size: 1リクエストあたりのエイリアン数
        concurrency: 未使用（互換性のため残す）
        model_name: モデル名（ログ用）
        model: モデル識別子（Gemini API呼び出し用）
        rpm: Requests Per Minute（1分あたりのリクエスト数制限）
    
    Returns:
        (全バッチから抽出された効果のリスト, None応答だったバッチのリスト)
    """
    interval = 60.0 / rpm + 0.5  # リクエスト間隔（安全マージン+0.5秒 = 4.5秒）
    
    # バッチリストを作成
    total_batches = (len(aliens) + batch_size - 1) // batch_size
    batches = []
    for i in range(0, len(aliens), batch_size):
        batch = aliens[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        batches.append((batch, batch_num))
    
    # 各バッチの処理タスクを格納
    tasks = []
    
    # リクエストを時間差で送信
    for idx, (batch, batch_num) in enumerate(batches):
        # 初回以外は待機
        if idx > 0:
            await asyncio.sleep(interval)
        
        # リクエスト送信（回答は待たない）
        print(f"[バッチ {batch_num}/{total_batches}] 図鑑No.{batch[0]['id']} 〜 No.{batch[-1]['id']} を送信...")
        task = asyncio.create_task(process_one_batch_gemini(batch, batch_num, batch_size, model_name, model))
        tasks.append((batch_num, batch, task))
    
    print(f"\n全 {len(tasks)} バッチの送信完了。回答を待機中...\n")
    
    # 全タスクの完了を待ち、結果を収集
    all_rows = []
    failed_batches = []  # None応答のバッチを記録
    
    for batch_num, batch, task in tasks:
        try:
            rows = await task
            if rows:
                all_rows.extend(rows)
                print(f"[バッチ {batch_num}] 完了: 効果 {len(rows)} 件を抽出（累計: {len(all_rows)}件）")
            else:
                print(f"[バッチ {batch_num}] 完了: 効果なし")
        except RuntimeError as e:
            # None応答エラーの場合は再解析対象として記録
            if "None" in str(e) or "空" in str(e):
                print(f"[バッチ {batch_num}] None応答: 再解析対象として記録")
                failed_batches.append((batch, batch_num))
            else:
                print(f"[バッチ {batch_num}] エラー: {e}")
                print(f"  このバッチをスキップします。")
        except Exception as e:
            print(f"[バッチ {batch_num}] エラー: {e}")
            print(f"  このバッチをスキップします。")
    
    return all_rows, failed_batches


async def process_one_batch_gemini(batch: List[dict], batch_num: int, batch_size: int, model_name: str, model: str) -> List[tuple]:
    """
    Gemini用の単一バッチ処理（同期API呼び出しをスレッドプールで実行）
    
    Args:
        batch: エイリアンのバッチ
        batch_num: バッチ番号
        batch_size: バッチサイズ
        model_name: モデル名（ログ用）
        model: モデル識別子
    
    Returns:
        抽出された効果のリスト
    """
    try:
        # プロンプト作成
        payload = build_batch_payload(batch, batch_size)
        prompt = PROMPT_TEMPLATE.replace("<<PAYLOAD>>", payload)
        
        # 同期API呼び出しをスレッドプールで実行（非同期化）
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, call_gemini, model, prompt)
        
        # JSONパース
        parsed = parse_llm_response(raw)
        rows = prepare_effects(parsed, model_name)
        
        return rows
        
    except Exception as e:
        raise  # エラーを上位に伝播させる


def insert_effects(conn, rows: List[tuple], truncate: bool = False):
    """skill_complete テーブルにバッチ挿入"""
    if not rows:
        print("挿入対象の効果がありませんでした。")
        return
    
    try:
        with conn.cursor() as cur:
            if truncate:
                print("skill_complete を TRUNCATE します...")
                cur.execute("TRUNCATE skill_complete RESTART IDENTITY")
            
            insert_sql = (
                "INSERT INTO skill_complete ("
                "alien_id, skill_number, group_id, name, target, value, unit, duration, probability, "
                "occupies_slot, is_debuff, awakening_required, trigger_timing, trigger_condition, "
                "has_requirement, requirement_type, requirement_value, requirement_count, "
                "verification_status, verified_at, llm_model, llm_analyzed_at, original_llm_values, corrections, notes"
                ") VALUES %s"
            )
            execute_values(cur, insert_sql, rows)
            print(f"skill_complete に {len(rows)} 件の効果を挿入しました。")
    except Exception as e:
        print(f"挿入エラー: {e}")
        raise


def parse_args() -> argparse.Namespace:
    """コマンドライン引数のパース"""
    parser = argparse.ArgumentParser(description="LLMで個性テキストを解析して skill_complete に保存")
    parser.add_argument("--model", default="gemini-2.5-flash-lite", help="使用するモデル名（gemini-2.5-flash-lite, gemini-2.5-pro等）")
    parser.add_argument("--batch-size", type=int, default=1, help="1リクエストで処理するエイリアン数")
    parser.add_argument("--limit", type=int, help="処理する個性数の上限")
    parser.add_argument("--offset", type=int, default=0, help="スキップする個性数")
    parser.add_argument("--truncate", action="store_true", help="skill_complete を削除してから挿入")
    parser.add_argument("--dry-run", action="store_true", help="DB へ書き込まず結果を表示")
    parser.add_argument("--wait-start", action="store_true", help="開始前に60秒待機（RPMカウンターリセット）")
    parser.add_argument("--concurrency", type=int, default=10, help="並列リクエスト数")
    return parser.parse_args()


def main():
    """メイン処理"""
    args = parse_args()
    
    # .envファイルを読み込み
    load_dotenv()
    
    # APIキー取得
    api_key = resolve_api_key()
    
    # Gemini設定
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    
    # モデル設定を取得
    model_config = None
    for cfg in MODEL_PRIORITY:
        if args.model.startswith(cfg['name']) or cfg['name'].startswith(args.model):
            model_config = cfg
            break
    
    if not model_config:
        print(f"警告: モデル '{args.model}' が優先順位リストにありません。デフォルト設定を使用します。")
        model_config = {'name': args.model, 'rpd': 50, 'rpm': 2}
    
    print(f"使用モデル: {model_config['name']} (RPD: {model_config['rpd']}, RPM: {model_config['rpm']})")
    model = genai.GenerativeModel(model_config['name'])
    
    # 開始前待機
    if args.wait_start:
        print("RPMカウンターリセットのため60秒待機します...")
        time.sleep(60)
        print("待機完了。解析を開始します。")
    
    # データベース接続
    conn = None
    try:
        conn = get_db_connection()
        print("データベースからエイリアンデータを読み込んでいます...")
        
        # limit は個性数ではなくエイリアン数に変換（1体=3個性）
        alien_limit = None
        if args.limit is not None:
            alien_limit = (args.limit + 2) // 3  # 切り上げ
        
        alien_offset = args.offset // 3
        
        aliens = fetch_aliens_from_db(conn, limit=alien_limit, offset=alien_offset)
        print(f"{len(aliens)}体のエイリアンデータを読み込みました（約{len(aliens) * 3}個性）。")
        
        # Gemini準並列処理（RPM制限対応）
        concurrency = args.concurrency
        rpm = model_config.get('rpm', 15)
        interval = 60.0 / rpm + 0.5  # 安全マージン（4.5秒）
        
        # 送信時間の計算（全リクエスト送信完了までの時間）
        send_time = (len(aliens) - 1) * interval  # 初回は即座、以降は4.5秒間隔
        
        print(f"\nLLMによる解析を開始します（準並列処理 + RPM制限）")
        print(f"  - バッチサイズ: {args.batch_size}体/リクエスト")
        print(f"  - レート制限: {rpm}req/分（{interval:.1f}秒間隔で送信）")
        print(f"  - 総バッチ数: {len(aliens)}バッチ")
        print(f"  - 送信完了時間: 約{send_time // 60:.0f}分{int(send_time % 60)}秒後")
        print(f"  - ※ 回答待ち時間は並行処理のため追加時間なし")
        print()
        
        all_rows, failed_batches = asyncio.run(process_aliens_parallel(aliens, args.batch_size, concurrency, model_config['name'], model, rpm))
        
        print(f"\n初回解析完了: 合計 {len(all_rows)} 件の効果を抽出しました。")
        
        # None応答のバッチを再解析
        if failed_batches:
            print(f"\n⚠️ None応答のバッチが {len(failed_batches)} 件見つかりました。再解析を開始します...")
            
            retry_aliens = [batch for batch, _ in failed_batches]
            retry_rows, retry_failed = asyncio.run(process_aliens_parallel(retry_aliens, args.batch_size, concurrency, model_config['name'], model, rpm))
            
            if retry_rows:
                all_rows.extend(retry_rows)
                print(f"✅ 再解析成功: {len(retry_rows)} 件の効果を追加抽出（累計: {len(all_rows)}件）")
            
            if retry_failed:
                print(f"⚠️ 再解析後もNone応答: {len(retry_failed)} バッチが失敗しました。")
                for batch, batch_num in retry_failed:
                    print(f"  - バッチ {batch_num}: 図鑑No.{batch[0]['id']}")
        
        print(f"\n全解析完了: 合計 {len(all_rows)} 件の効果を抽出しました。")
        
        # 解析用の接続を閉じる（長時間経過したため）
        conn.close()
        print("解析用のデータベース接続をクローズしました。")
        
        # ドライラン時はプレビューのみ
        if args.dry_run:
            print("\n--- ドライラン: プレビュー（最大5件） ---")
            for row in all_rows[:5]:
                print(f"  alien_id={row[0]}, skill={row[1]}, name={row[3]}, target={row[4]}")
            return
        
        # バックアップ作成
        db_url = os.getenv("DATABASE_URL")
        backup_path = backup_skill_complete(db_url)
        print(f"バックアップを作成しました: {backup_path}")
        
        # 挿入用に新しい接続を作成
        print("\n挿入用の新しいデータベース接続を作成します...")
        conn = psycopg2.connect(db_url)
        
        # DB挿入
        insert_effects(conn, all_rows, truncate=args.truncate)
        conn.commit()
        print("\nデータベースへの変更をコミットしました。")
        
    except Exception as error:
        print(f"\nエラーが発生しました: {error}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
                print("データベースへの変更をロールバックしました。")
            except:
                pass
    finally:
        if conn:
            try:
                conn.close()
                print("データベース接続をクローズしました。")
            except:
                pass
    
    print("\n--- 全ての処理が完了しました ---")


if __name__ == "__main__":
    main()
