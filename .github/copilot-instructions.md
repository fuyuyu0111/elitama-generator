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
│   └── index.html                       "HTML/CSS + Jinja2変数定義（約2,500行）"
├── static/
│   ├── images/                          "エイリアン画像（478体）【WebP形式】"
│   ├── icon/                            "UI用アイコン【WebP形式】"
│   ├── main_icon.png                    "アプリアイコン（favicon/apple-touch-icon用）【PNG形式】"
│   ├── main_icon.webp                   "アプリアイコン（PWA用）【WebP形式】"
│   ├── manifest.json                    "PWAマニフェスト"
│   └── js/
│       ├── main.js                      "メインJavaScript（約8,300行）"
│       └── service-worker.js            "Service Worker（PWA用キャッシュ制御）"
├── scripts/
│   ├── run_automated_update.py          "自動更新統合スクリプト"
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
    └── skill_verified_effects_backup.jsonl  "変更履歴のバックアップ"
```

### 現在の状態（2025年12月更新）

**メインアプリ**: ✅ **完成**
- エイリアン編成、個性発動条件判定、バフ/デバフ絞り込み、管理機能が統合
- JavaScriptは `static/js/main.js` に分離済み

**コード構成**:
- `index.html`: HTML構造、CSS、Jinja2変数定義のみ（約2,500行）
- `main.js`: 全JavaScriptロジック（約8,300行）
- Jinja2変数（`ALL_ALIENS`, `ALIEN_SKILL_DATA`等）はHTML側で定義し、main.jsから参照

**個性解析システム**: ✅ **解析完了（Gemini API削除済み）**
- 個性: 全849個解析完了、特技: 191件解析済み
- 新規個性・特技は管理モードから手動登録

**画像形式**: ✅ **WebP形式に統一**
- エイリアン画像、UIアイコン: **WebP形式**
- メインアイコン: **PNG（favicon/apple-touch-icon）+ WebP（PWA）の併用**

-----

## 2. 技術スタックと設計思想

### 技術

- **Backend**: Python 3.x, Flask, psycopg2, Pillow
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Database**: PostgreSQL
- **画像形式**: WebP（PNG比約12%削減）

### 設計思想

1. **シンプルさ最優先**: ライブラリ不使用、基本的なDOM操作のみ
2. **コード分離**: HTML/CSSとJavaScriptを分離（`index.html` + `main.js`）
3. **Jinja2変数の扱い**: HTML側で定義 → JSから参照（`.js`ファイル内では`{{ }}`使用不可）
4. **データ一括読み込み**: `@lru_cache`で初回に全データをメモリにキャッシュ
5. **Git自動反映**: スクレイピング後の資産は自動コミット＆プッシュ

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
| `S_Skill` | TEXT | 特技名（引用符必須） |
| `S_Skill_text` | TEXT | 特技テキスト（引用符必須） |
| `hp`, `power`, `motivation`, `size`, `speed` | INTEGER | ステータス値 |

### correct\_effect\_names テーブル (効果辞書)

| 列 | 型 | 説明 |
|------|-----|------|
| `correct_name` | TEXT | 効果名 |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS |
| `category` | TEXT | カテゴリ |
| **主キー** | **(correct_name, category)** | 複合主キー |

### skill\_text\_verified\_effects テーブル

| 列 | 型 | 説明 |
|------|-----|------|
| `id` | BIGSERIAL PK | 自動連番 |
| `skill_text` | TEXT | 個性説明文 |
| `effect_name` | TEXT FK | 効果名 |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS |

-----

## 4. フロントエンド構成

### ファイル構成
```
templates/index.html  ← HTML/CSS + Jinja2変数定義
static/js/main.js     ← 全JavaScriptロジック
```

### Jinja2変数（HTML側で定義）
```javascript
// index.html 内の <script> で定義
const ALL_ALIENS = {{ all_aliens | tojson | safe }};
const ALIEN_SKILL_DATA = {{ alien_skill_data | tojson | safe }};
const ALL_EFFECTS = {{ all_effects | tojson | safe }};
const S_SKILL_EFFECTS = {{ s_skill_effects | tojson | safe }};
const ALIEN_EFFECTS = {{ alien_effects | tojson | safe }};
```

### main.js の構造
- `DOMContentLoaded`でローディング処理を実行
- グローバル変数（上記Jinja2変数）を参照
- 約8,300行のロジック（パーティ編成、D&D、フィルター、管理モード等）

-----

## 5. 管理モード

### 認証
- **パスワード認証**: 環境変数`ADMIN_PASSWORD`で管理
- **セッション管理**: 30分間操作なしで自動ログアウト

### 機能
- **個性・特技管理**: 効果の追加・編集・削除
- **辞書管理**: 効果名の追加・編集
- **変更適応**: 変更を一括適用、バックアップ自動作成

### パフォーマンス最適化
- `updateAdminUI(skipRender)`: `skipRender=true`で全パーティ再描画をスキップ
- 効果追加/削除時は変更カウント更新のみ（画像再読み込みを防止）

-----

## 6. 運用・デプロイ仕様

### 自動スクレイピング（GitHub Actions）
- スケジュール: 毎日00:02（JST）
- 処理: alienテーブル更新 → 画像ダウンロード（WebP変換） → Discord通知

### PWA化
- `manifest.json`と`service-worker.js`でPWA対応
- アイコン: PNG（favicon/apple-touch-icon）、WebP（PWAマニフェスト）
- キャッシュ: `alien-egg-cache-v2`