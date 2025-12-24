# エリたま編成ジェネレーター 開発ガイド

## 1. プロジェクト概要

### 目的

スマホゲーム「エイリアンのたまご」の非公式編成支援ツール。直感的な操作でパーティを組み、個性（スキル）の発動条件や効果をリアルタイムで確認できるWebアプリ。

### リポジトリ構成

```
alien_egg/
├── .github/
│   ├── copilot-instructions.md          "このファイル（開発ガイド）"
│   └── workflows/
│       └── daily_scraping.yml           "日次スクレイピング自動実行"
├── app.py                               "メインアプリのバックエンド（Flask）"
├── requirements.txt                     "Pythonパッケージ一覧"
├── templates/
│   └── index.html                       "メインアプリのフロントエンド（約10700行）"
├── static/
│   ├── images/                          "エイリアン画像（478体）【WebP形式】"
│   ├── icon/                            "UI用アイコン【WebP形式】"
│   ├── main_icon.png                    "アプリアイコン（favicon/apple-touch-icon用）【PNG形式・互換性重視】"
│   ├── main_icon.webp                   "アプリアイコン（PWA用）【WebP形式・軽量】"
│   ├── manifest.json                    "PWAマニフェスト"
│   └── js/
│       └── service-worker.js            "Service Worker（PWA用キャッシュ制御）"
├── scripts/
│   ├── run_automated_update.py          "自動更新統合スクリプト（スクレイピングのみ）"
│   ├── scraping/
│   │   ├── full_scraper.py              "データ収集スクリプト"
│   │   └── combined_scraper.py          "スクレイピング+画像取得（WebP変換対応）"
│   └── utils/
│       ├── __init__.py                  "パッケージ初期化"
│       ├── db_helpers.py                "データベースヘルパー関数"
│       └── discord_notifier.py          "Discord通知機能"
└── backups/
    ├── skill_list_fixed.jsonl           "修正版個性解析データ"
    ├── special_skill_analysis.jsonl     "特技解析データ"
    └── skill_verified_effects_backup.jsonl  "変更履歴のバックアップ（追記形式）"
```

### 現在の状態（2024年12月更新）

**メインアプリ（編成ジェネレーター）**: ✅ **完成**

- エイリアン編成、個性発動条件判定、バフ/デバフ絞り込み、管理機能が統合
- 管理機能により、個性・特技の効果データを直接編集可能

**個性解析システム**: ✅ **解析完了（Gemini API削除済み）**

- 個性: 全849個の個性テキストを解析完了（2123件の効果データ）
- 特技: 191件解析済み、29件は「ダメージのみ」
- **注意**: Gemini APIを使用した自動解析機能は削除済み。新規個性・特技は管理モードから手動で登録

**スクレイピングシステム**: ✅ **実装完了**

- データ収集: `full_scraper.py`で全データを取得
- 画像形式: **WebP形式**で保存（PNG比約12%削減）
- スクレイピングモード:
  - デフォルト（逆順スクレイピング）: 最新のエイリアンから逆順にチェック
  - 全体スクレイピング: `--full-scrape` フラグで実行
  - 部分スクレイピング: `--scrape-ids <ID列>` で指定IDのみ
- 自動実行: GitHub Actionsで毎日00:02（JST）に自動実行

**画像形式**: ✅ **WebP形式に統一**

- エイリアン画像、UIアイコン: **WebP形式**
- メインアイコン: **PNG（favicon/apple-touch-icon）+ WebP（PWA）の併用**

**データベース**: PostgreSQL

- `alien`: エイリアン基本情報（478体）
- `correct_effect_names`: 効果名の辞書
- `skill_text_verified_effects`: 個性・特技テキストごとの効果

-----

## 2. 技術スタックと設計思想

### 技術

- **Backend**: Python 3.x, Flask, psycopg2, Pillow
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Database**: PostgreSQL
- **画像形式**: WebP（PNG比約12%削減）

### 設計思想

1. **シンプルさ最優先**: ライブラリ不使用、基本的なDOM操作のみ
2. **コード量の最小化**: 可能な限り1ファイル完結
3. **操作の簡潔さ**: 画面遷移なし、1画面に情報集約
4. **データ一括読み込み**: `@lru_cache`で初回に全データをメモリにキャッシュ
5. **モバイル対応**: アドレスバーを考慮したスクロール対応
6. **Git自動反映**: スクレイピング後の資産は自動コミット＆プッシュ

-----

## 3. データベーススキーマ

### alien テーブル

| 列 | 型 | 説明 |
|------|-----|------|
| `id` | INTEGER PK | 図鑑No |
| `name` | TEXT | エイリアン名 |
| `attribute` | INTEGER | 属性 |
| `affiliation` | INTEGER | 所属 |
| `attack_range` | INTEGER | 攻撃範囲 |
| `attack_area` | INTEGER | 攻撃エリア |
| `role` | INTEGER | 役割 |
| `type_1`〜`type_4` | INTEGER | タイプ1-4 |
| `skill_no1`〜`skill_no3` | TEXT | 個性名1-3 |
| `skill_text1`〜`skill_text3` | TEXT | 個性テキスト1-3 |
| `S_Skill` | TEXT | **特技名（引用符必須）** |
| `S_Skill_text` | TEXT | **特技テキスト（引用符必須）** |
| `hp`, `power`, `motivation`, `size`, `speed` | INTEGER | ステータス値 |

### correct\_effect\_names テーブル (効果辞書)

| 列 | 型 | 説明 |
|------|-----|------|
| `correct_name` | TEXT | 効果名 |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS |
| `category` | TEXT | BUFF_BOOST, S_SKILL_HEAL... |
| `target` | TEXT | バフ/デバフ対象 |
| `condition_target` | TEXT | 効果対象（バフのみ） |
| **主キー** | **(correct_name, category)** | 複合主キー |

### skill\_text\_verified\_effects テーブル

| 列 | 型 | 説明 |
|------|-----|------|
| `id` | BIGSERIAL PK | 自動連番 |
| `skill_text` | TEXT | 個性説明文 |
| `effect_name` | TEXT FK | 効果名 |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS |
| `category` | TEXT | カテゴリ |
| `target` | TEXT | 効果対象 |
| `has_requirement` | BOOLEAN | 味方編成要求の有無 |
| `requirement_details` | TEXT | 要求内容 |
| `requirement_count` | INTEGER | 要求数 |

-----

## 4. メインアプリの仕様

### 基本機能

- **5つのパーティスロット**: パーティ1〜5を切り替え可能
- **ドラッグ&ドロップ**: エイリアン一覧からスロットへ移動
- **個性発動条件判定**: リアルタイムで◯/✗アイコン表示
- **絞り込み機能**: 属性、所属、攻撃範囲、ロール、タイプ、効果名
- **ソート機能**: ID、たいりょく、こうげき、やるき、おおきさ、いどう

### 状態管理

```javascript
const parties = {'1': [null,null,null,null,null], ..., '5': [...]};
const ALL_ALIENS = {{ all_aliens | tojson | safe }}; 
const ALL_EFFECTS = {{ all_effects | tojson | safe }};
const ALIEN_EFFECTS = {{ alien_effects | tojson | safe }};
const ALIEN_SKILL_DATA = {{ alien_skill_data | tojson | safe }};
```

-----

## 5. 管理モード

### 認証

- **パスワード認証**: 環境変数`ADMIN_PASSWORD`で管理
- **セッション管理**: 30分間操作なしで自動ログアウト

### 機能

- **個性・特技管理**: 効果の追加・編集・削除
- **辞書管理**: 効果名の追加・編集
- **変更適応**: 変更を一括適用、バックアップ自動作成

### 主要APIエンドポイント

- `/api/admin/login`: ログイン
- `/api/admin/apply-changes`: 変更一括適用
- `/api/admin/dictionary/add`: 辞書追加

-----

## 6. よくある質問

**Q: Gemini APIはまだ使用している？**
→ **削除済み**。新規個性・特技は管理モードから手動で登録。

**Q: 画像形式は？**
→ **エイリアン画像・UIアイコン: WebP形式**。メインアイコン: PNG（互換性）+ WebP（PWA）併用。

-----

## 7. 運用・デプロイ仕様

### 自動スクレイピング（GitHub Actions）

- スケジュール: 毎日00:02（JST）
- 処理フロー: alienテーブル更新 → 画像ダウンロード（WebP変換） → Discord通知

### 管理モードでの手動スクレイピング

1. ヘッダー「管」→ パスワード入力
2. 「実行」メニューから選択
   - **全体スクレイピング**: 全件スクレイピング
   - **部分スクレイピング**: 指定IDのみ

### Git自動プッシュ

- 成功終了時に自動コミット→プッシュ
- 対象: `static/images`, `backups/skill_list_fixed.jsonl`

### PWA化

- `manifest.json`と`service-worker.js`でPWA対応
- アイコン: PNG（favicon/apple-touch-icon）、WebP（PWAマニフェスト）
- キャッシュ: `alien-egg-cache-v2`

### Service Worker

- ファイル: `static/js/service-worker.js`
- キャッシュ対象: `/`, `/static/manifest.json`, `/static/main_icon.webp`