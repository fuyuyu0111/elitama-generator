# エリたま編成ジェネレーター 開発ガイド

## 1. プロジェクト概要

### 目的

スマホゲーム「エイリアンのたまご」の非公式編成支援ツール。直感的な操作でパーティを組み、個性（スキル）の発動条件や効果をリアルタイムで確認できるWebアプリ。

### リポジトリ構成

```
alien_egg/
├── .github/
│   └── copilot-instructions.md          "このファイル（開発ガイド）"
├── app.py                               "メインアプリのバックエンド（Flask）"
├── requirements.txt                     "Pythonパッケージ一覧"
├── templates/
│   └── index.html                       "メインアプリのフロントエンド（管理機能統合済み、約9400行）"
├── static/
│   ├── images/                          "エイリアン画像（460体）"
│   └── icon/                            "UI用アイコン（属性・はんい・距離・所属・タイプ・ロール）"
├── analysis/                            "個性・特技解析システム"
│   ├── run_stage1.py                    "本番解析スクリプト（LLM解析）"
│   └── prompts/                         "LLM用プロンプト"
│       ├── stage1_effect_names.md       "個性用プロンプト"
│       ├── s_skill_effect_names.md      "特技用プロンプト"
│       └── s_skill_effect_names.json    "特技用効果辞書"
├── scripts/
│   ├── scraping/
│   │   ├── full_scraper.py              "データ収集スクリプト（ステータス値取得対応済み）"
│   │   └── combined_scraper.py          "スクレイピングと画像取得を統合したスクリプト（逆順スクレイピング機能付き）"
│   ├── utils/
│   │   └── discord_notifier.py          "Discord通知機能"
│   └── run_automated_update.py          "自動更新統合スクリプト（変更検知＋解析専用モード対応）"
└── backups/
    ├── skill_list_fixed.jsonl           "修正版個性解析データ"
    ├── special_skill_analysis.jsonl     "特技解析データ"
    └── skill_verified_effects_backup.jsonl  "変更履歴のバックアップ（追記形式、管理機能で自動生成）"
```

### 現在の状態（2025年12月更新）

**メインアプリ（編成ジェネレーター）**: ✅ **完成**

- エイリアン編成、個性発動条件判定、バフ/デバフ絞り込み、管理機能が統合された単一アプリとして運用中
- 管理機能により、個性・特技の効果データを直接編集可能

**個性解析システム**: ✅ **解析完了**

- 個性: 全849個の個性テキストを解析完了（2123件の効果データ、98種のユニーク効果名）
- 特技: 191件解析済み、29件は「ダメージのみ」
- 手動チェック: 全850件のユニーク個性テキストを手動で精査完了
- データベース適用: 修正済みデータを`correct_effect_names`と`skill_text_verified_effects`テーブルに適用完了

**スクレイピングシステム**: ✅ **実装完了**

- データ収集: `full_scraper.py`で全データを取得（ステータス値: hp, power, motivation, size, speed を含む）
- スクレイピングモード:
  - デフォルト（逆順スクレイピング）: 最新のエイリアンから逆順にチェックし、新キャラのみスクレイピング（公式Wikiへの負荷を大幅に削減）
  - 全体スクレイピング: 管理画面の「全体」ボタンまたは `--full-scrape` フラグで実行（上方修正などのステータス変更検出用）
  - 部分スクレイピング: `--scrape-ids <ID列>` で指定IDのエイリアンのみスクレイピング（上方修正などのステータス変更検出用）
  - 解析専用モード: `--skip-scraping --analysis-ids <ID列>` でスクレイピングを行わず指定IDの個性・特技のみ再解析
- 解析スキーム: **スクレイピング完了後、DBのalienテーブルからスクレイピング対象IDを参照し、そのエイリアンに未解析個性・特技が含まれる場合に解析＆DB登録**
- 自動解析: 未解析個性・特技テキストのみをLLMで解析（Gemini 2.5 Flash使用）
- 自動実行: GitHub Actionsで毎日00:02（JST）に自動実行（デフォルトで逆順スクレイピング）

**データベース**: PostgreSQL

- `alien`: エイリアン基本情報（460体、ステータス値含む）
- `correct_effect_names`: 効果名の辞書（個性用: 約98種、特技用: 約39種、合計約137レコード）
- `skill_text_verified_effects`: 個性・特技テキストごとの効果・発動条件（個性: 2123件、特技: 292件）

**注意: 過去の作業用スクリプトについて**

以下のスクリプトは過去のデータ修正作業で使用されたもので、現在の自動実行フローでは使用されていません。ローカル環境でのみ保持し、GitHubには上げないように設定されています（`.gitignore`に記載）:

- `scripts/apply_fixed_data.py`: 修正した効果データと辞書をデータベースに適用するスクリプト
- `scripts/fix_correct_effect_names.py`: `correct_effect_names`テーブルのスキーマ変更とデータ復元スクリプト
- `scripts/insert_personality_from_backup.py`: バックアップファイルから個性の解析結果を読み込んでDBに挿入するスクリプト

これらのスクリプトは過去の作業記録として保持していますが、現在のシステムでは不要です。

-----

## 2. 技術スタックと設計思想

### 技術

- **Backend**: Python 3.x, Flask, psycopg2
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Database**: PostgreSQL
- **LLM**: Google Gemini（解析用）

### 設計思想

1. **シンプルさ最優先**: ライブラリ不使用、基本的なDOM操作のみ
2. **コード量の最小化**: 可能な限り1ファイル完結・関数の粒度を揃え、複雑な抽象化や汎用フレームワークを避ける（テンプレートも極力HTML/JS素のまま）
2. **操作の簡潔さ**: 画面遷移なし、1画面に情報集約
3. **データ一括読み込み**: `@lru_cache`で初回に全データをメモリにキャッシュ
4. **型安全**: `parseInt()`でID比較を統一
5. **解析の効率化**: キャラごと（1380個性）ではなく、個性テキストごと（約870種）に解析し、不整合を防止する
6. **モバイル対応**: モバイルブラウザでも快適に使用できるよう、アドレスバーを考慮したスクロール対応を実装
7. **Git自動反映**: スクレイピング後の資産（`static/images`, `backups/skill_list_fixed.jsonl`）は成功時に自動コミット＆プッシュし、デプロイ環境とGitHubを即時同期させる

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
| `S_Skill` | TEXT | **特技名（データベースでは大文字、引用符必須）** |
| `S_Skill_text` | TEXT | **特技テキスト（データベースでは大文字、引用符必須）** |
| `hp`, `power`, `motivation`, `size`, `speed` | INTEGER | ステータス値 |

**重要**: 
- データベースでは`"S_Skill"`, `"S_Skill_text"`として保存される（PostgreSQLの大文字小文字区別のため引用符が必要）
- Python/JavaScriptコード内では`normalize_alien_row()`関数（`scripts/utils/db_helpers.py`）で`s_skill`, `s_skill_text`に正規化して使用
- SQLクエリでは必ず`"S_Skill"`, `"S_Skill_text"`として参照すること

### correct\_effect\_names テーブル (効果辞書)

| 列 | 型 | 説明 |
|------|-----|------|
| `correct_name` | TEXT | 効果名（例: つよさアップ） |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS |
| `category` | TEXT | BUFF\_BOOST, DEBUFF\_REDUCE, S_SKILL_HEAL, S_SKILL_BUFF, S_SKILL_DEBUFF... |
| `target` | TEXT | バフ/デバフ対象（例: "自分", "味方全員", "a:1"など） |
| `condition_target` | TEXT | 効果対象（例: "a:1", "boss:1"など） |
| `show_target` | BOOLEAN | バフ/デバフ対象の表示制御（TRUE: 表示、FALSE: 非表示） |
| `show_condition_target` | BOOLEAN | 効果対象の表示制御（TRUE: 表示、FALSE: 非表示） |
| `created_at` | TIMESTAMP | 作成日時 |
| **主キー** | **(correct_name, category)** | **複合主キー。同じ効果名でも個性用と特技用で異なるcategoryとして区別される** |

**重要**: 
- このテーブルは複合主キー`(correct_name, category)`を持つ。同じ効果名（例: "いどうアップ"）でも、個性用（`category = "BUFF_BOOST"`）と特技用（`category = "S_SKILL_BUFF"`）で別レコードとして保存される。これにより、個性と特技で同じ効果名が存在する場合でも、それぞれが独立して管理される。
- **表示制御機能**: `show_target`と`show_condition_target`により、バフ/デバフ画面でのターゲット表示を制御。自爆などの対象が明らかな効果では`FALSE`に設定することで、無駄な表示を非表示にできる。
- 管理モードの辞書管理画面で効果名をタップすると、これらのチェックボックスが表示され、更新可能
- API: `/api/admin/dictionary/update-show-flags` (POST) で更新

### skill\_text\_verified\_effects テーブル (1行=1効果)

| 列 | 型 | 説明 |
|------|-----|------|
| `id` | BIGSERIAL PK | 自動連番 |
| `skill_text` | TEXT | 個性説明文（`alien`テーブルとこれで紐付け） |
| `effect_name` | TEXT FK | 効果名（`correct_effect_names`を参照） |
| `effect_type` | TEXT | BUFF, DEBUFF, STATUS（自動補完） |
| `category` | TEXT | BUFF_BOOST, DEBUFF_REDUCE...（自動補完） |
| `condition_target` | TEXT | **条件対象**（バフのみ）。例: "a:2"（昆虫属性）, "d:3"（とおい）, "boss:1"（レジェンド） |
| `requires_awakening` | BOOLEAN | 個性覚醒の要否（2段階目で解析予定） |
| `target` | TEXT | **効果対象**。例: "自分", "味方全員", "敵全員", "敵単体", "a:1"（動物属性の味方）, "a:2,a:4"（昆虫/ナゾ属性の敵） |
| `has_requirement` | BOOLEAN | **味方編成要求の有無**（`skill`テーブルの代替） |
| `requirement_details` | TEXT | 要求内容（例: "a:1", "e:A\!"） |
| `requirement_count` | INTEGER | 要求数（例: 3） |
| `created_at` | TIMESTAMP | 作成日時 |
| `updated_at` | TIMESTAMP | 更新日時 |

**設計ポイント**: 解析単位を`skill_text`（個性文）にすることで、解析数を削減し、同一の個性を持つキャラ間で解析結果が異なる不整合を根本的に防ぐ。

**`target`と`condition_target`の使い分け**:
- `target`: バフ・デバフが付与される対象（効果を受ける対象）
  - バフ: "自分", "味方全員", "a:1"（動物属性の味方）など
  - デバフ: "敵単体", "敵全員", "a:2,a:4"（昆虫/ナゾ属性の敵）など
- `condition_target`: 効果が作用する条件対象（バフのみ使用）
  - 例: "昆虫属性に対する与ダメージアップ" → `target: "自分"`, `condition_target: "a:2"`
  - 例: "距離:とおいの敵を狙いやすく" → `target: "味方全員"`, `condition_target: "d:3"`

**個性と特技の区別**:
- **個性データ**: `skill_text`が`alien`テーブルの`skill_text1-3`に一致、かつ`category`が`S_SKILL_%`で始まらない
- **特技データ**: `skill_text`が`alien`テーブルの`S_Skill_text`と一致、かつ`category`が`S_SKILL_%`で始まる
- 判定には`scripts/utils/db_helpers.py`の`is_special_skill()`関数を使用

-----

## 4. メインアプリの仕様と機能

### 4.1 基本機能

#### パーティ編成機能

- **5つのパーティスロット**: パーティ1〜5を切り替え可能
- **ドラッグ&ドロップ**: エイリアン一覧からスロットへ、スロット間で移動可能
- **個性発動条件判定**: リアルタイムで個性の要求条件をチェックし、◯/✗アイコンで表示
  - 開いた時用（`.skill-name-display`内）と閉じた時用（`.skill-item`内）の両方のアイコンを更新
- **編成解除**: 各スロットの「編成解除」ボタンで削除可能（管理モード時は「管理」ボタンに変更）

#### エイリアン一覧機能

- **グリッド表示**: 460体のエイリアンをグリッド形式で表示
- **絞り込み機能**:
  - 属性、所属、攻撃範囲、攻撃エリア、ロール、タイプによる絞り込み
  - 効果名による絞り込み（バフ・デバフ画面）
  - 名前検索（通常モード: 名前のみ、管理モード: 名前 + 個性・特技テキストの全文検索）
- **ソート機能**: ID、たいりょく、こうげき、やるき、おおきさ、いどうでソート可能
- **エイリアン詳細表示**: クリックで詳細モーダルを表示（基本情報、個性・特技テキスト）

#### バフ・デバフ絞り込み機能

- **効果名選択**: バフ・デバフ・状態異常のカテゴリから効果名を選択
- **個性/特技タブ**: 個性と特技を分けて表示・選択
- **詳細条件指定**:
  - 右クリック/長押しで詳細メニューを表示
  - バフ/デバフ対象（`target`）の選択
  - 効果対象（`condition_target`）の選択（バフのみ）
  - AND/OR検索モードの切り替え
- **効果使用数表示**: 効果ボタンの横に`(X)`形式で使用数を表示（管理モード時のみ）

### 4.2 状態管理

```javascript
const parties = {'1': [null,null,null,null,null], ..., '5': [...]};
let currentPartyId = '1';
// (★重要★) app.pyから渡される「全カラム」入りのエイリアン辞書
const ALL_ALIENS = {{ all_aliens | tojson | safe }}; 
// (新) 効果辞書（絞り込みUI用）
const ALL_EFFECTS = {{ all_effects | tojson | safe }};
// (新) エイリアンごとの効果リスト（個性別、絞り込み用）
const ALIEN_EFFECTS = {{ alien_effects | tojson | safe }};
// (新) 全エイリアンの全個性テキストと要求（絞り込み＆条件判定用）
const ALIEN_SKILL_DATA = {{ alien_skill_data | tojson | safe }};
```

### 4.3 主要関数

#### パーティ管理

- `renderPartySlots()`: パーティ表示の完全再構築（HTML生成）
- `checkPartyRealtime()`: 個性条件判定（◯/✗）。開いた時用/閉じた時用の**両方**のアイコンDOMを更新する。
- `addToParty()` / `removeFromParty()`: 編成追加・削除（`renderPartySlots` を呼び出す）
- `checkCondition()`: `checkPartyRealtime` が使用するヘルパー関数（判定ロジック本体）
- `createRequirementIcon()`: `renderPartySlots` が使用するヘルパー関数（HTML生成）

#### エイリアン一覧

- `updateAlienGrid()`: エイリアン一覧の絞り込みとソート
- `normalizeString()`: 文字列を正規化（ひらがな→カタカナ、全角→半角、大文字→小文字）
- `createFilterButtons()`: フィルターボタンの動的生成（効果名フィルター含む）
- `updateFilterUIState()`: フィルターUIの状態を更新（絞り込みボタンの色など）

#### バフ・デバフ絞り込み

- `createFilterButtons()`: 効果名フィルターボタンの動的生成
- `updateAlienGrid()`: 選択された効果名でエイリアンを絞り込み

### 4.4 データ処理の流れ

1. **初期化**:
   - `app.py`が起動時に全データをDBから取得
   - `@lru_cache`でキャッシュ（初回のみDBアクセス）
   - `templates/index.html`に全データを埋め込み（`ALL_ALIENS`, `ALL_EFFECTS`, `ALIEN_EFFECTS`, `ALIEN_SKILL_DATA`）

2. **パーティ編成**:
   - ユーザーがエイリアンをドラッグ&ドロップ
   - `addToParty()`が呼ばれ、`parties[currentPartyId]`を更新
   - `renderPartySlots()`でパーティ表示を再構築
   - `checkPartyRealtime()`で個性発動条件を判定し、◯/✗アイコンを更新

3. **絞り込み**:
   - ユーザーがフィルターを選択
   - `updateAlienGrid()`がフィルター条件をチェック
   - 条件に合致するエイリアンのみを表示

4. **バフ・デバフ検索**:
   - ユーザーが効果名を選択
   - `ALIEN_EFFECTS`から該当するエイリアンを検索
   - `updateAlienGrid()`で絞り込み表示

### 4.5 モバイルブラウザ対応

- **スクロール対応**: モバイルブラウザ（画面幅768px以下）では、`body`に`overflow-y: auto`を設定してスクロールを許可
- **アドレスバー対応**: ブラウザのアドレスバーが表示されている場合でも、ユーザーが下にスクロールすることで見切れている部分を確認可能
- **UIの高さ**: `100vh`を維持し、余分な高さは追加しない（画面の縦幅に合わせる）
- **実装**: CSSのメディアクエリ（`@media (max-width: 768px)`）でモバイル専用のスタイルを適用

### 4.6 UI挙動メモ（感覚的仕様の共有）

- `updateFilterUIState()`は「属性/所属などの通常フィルター」と「名前検索欄」の状態だけで「絞り込み」ボタンの赤ハイライトを制御し、バフデバフ絞り込みのハイライトは`effectFilterButton`側で独立して判定する（赤が消えないよう分離）
- 編成スロットの◯/✗判定は`checkPartyRealtime()`→`checkCondition()`が`ALIEN_SKILL_DATA`と現在の`parties`配列を突き合わせて行い、管理モード中も同じ判定結果を使ってUI表示を統一
- 画像は`createAlienImageElement()`で既存カードの`img`を探してクローンし、都度の再ロードを避けてメモリ上の同一要素を使い回す

-----

## 5. 管理モード

### 5.1 認証システム

- **管理ボタン**: ヘッダー左端に「管」ボタンが表示
- **パスワード認証**: カスタムモーダルでパスワード入力（環境変数`ADMIN_PASSWORD`で管理、デフォルト: 'admin'）
- **セッション管理**: 
  - 30分間操作なしで自動ログアウト
  - ページリロード時は自動的にログアウト（セキュリティ対策）
  - 「管理を続ける」を選択した場合は、`sessionStorage`で管理モードを維持

### 5.2 UI変更

- **ネオンカラー変更**: 管理モード時はすべてのネオンカラー（`--accent-green`）がオレンジ（`#ff8800`）に変更
- **ボタン表示**:
  - ヘッダーに「辞書」ボタン（「辞」ボタン）が表示される
  - 編成スロットの「編成解除」ボタンが「管理」ボタンに変わる
  - バフデバフ画面に「不整合」ボタンが表示される
- **不整合データ表示**: 不整合データを持つエイリアンの名前が赤く表示される

### 5.3 個性・特技管理機能

#### 管理モーダル

- **開き方**: 編成スロットの「管理」ボタンをクリック
- **タブ構成**: エイリアンアイコン・名前・個性1-3・特技のタブ表示
- **選択中のタブ**: 縁取りで表示
- **個性説明文**: 選択中のタブの説明文を表示

#### 効果管理

- **効果表示**: 
  - 効果名、分類（effect_type）、カテゴリ、バフ/デバフ対象（target）、効果対象（condition_target）、要求が表示される
  - 不整合データがある場合、該当フィールドが赤色で表示される
  - 効果使用数が効果名の横に`(X)`形式で表示される
  - 新規・変更された効果は点線ボーダーでハイライトされる（「新規」「変更」ラベル付き）
  - 削除予定の効果は破線ボーダーで表示され、「削除」ラベルと「✕」ボタンが表示される

- **効果の追加・編集・削除**:
  - **追加**: 「効果を追加」ボタンで新規効果を追加
  - **編集**: 「編集」ボタンで既存効果を編集（削除予定の効果は編集不可）
  - **削除**: 「削除」ボタンで効果を削除予定にマーク（実際の削除は変更適応時）
  - **削除取消**: 「✕」ボタンで削除予定を取消

- **選択式UI**:
  - 効果名は辞書からカテゴリ別に選択（個性用/特技用で分離）
  - `target`/`condition_target`/`requirement`はアイコンから選択（複数選択可能、コード形式と文字列形式に対応）
  - 所属(b:1-5)も選択可能
  - `effect_type`と`category`は効果名から自動取得して表示のみ（編集不可）
  - 要求選択はアイコンから選択（「以外」も含む、単一選択のみ）
  - 「以外」チェックボックスは決定ボタンの横に1つだけ配置

- **不正な値の自動削除**: 有効な値を選択した際に不正な値（:を含まない値）を自動削除

- **変更判定**:
  - 最初の状態（DBの状態）と比較して、変更が元に戻った場合はpendingChangesから削除
  - 新規追加の効果は削除マークの対象外（同じ効果名でも別スロットとして認識）

### 5.4 辞書管理機能

- **開き方**: ヘッダーの「辞書」ボタンをクリック
- **タブ構成**: 「個性 | 特技 | 未登録」のタブで切り替え
- **効果名の追加・編集**: 個性用・特技用で`effect_type`/`category`の選択肢が動的に変更される
- **表示制御**: 効果名をタップすると、`target`/`condition_target`の表示/非表示を制御できるモーダルが開く
- **未登録効果の置換**: 未登録効果を辞書の効果名に一括・個別置換できる

### 5.5 変更管理システム

#### 変更履歴（pendingChanges）

- **保持場所**: フロントエンドの`pendingChanges`配列
- **変更タイプ**: 
  - `add`: 新規追加
  - `update`: 既存効果の更新
  - `delete`: 既存効果の削除
  - `dictionary_update`: 辞書の表示フラグ更新
- **重複排除**: 同じ`skill_text`と`effect_name`の変更がある場合は上書き（複数回編集しても最初と最後の差分だけ保持）

#### 変更適応ボタン

- **表示**: 変更がある場合、ヘッダーの「エリジェネ」が「変更適応(X)」（Xは変更数）に変わり、クリック可能になる
- **確認ダイアログ**: クリックすると確認ダイアログが表示され、変更内容を確認できる
- **バックアップ**: 適用前に自動的にバックアップが作成される（`backups/skill_verified_effects_backup.jsonl`に追記）
- **適用処理**:
  - `/api/admin/apply-changes`にPOSTリクエスト
  - バックエンドで`add`/`update`/`delete`を処理
  - `update`はDELETE→INSERT方式で、不正な値が残らないようにする
  - `dictionary_update`は先に処理され、その後スキル変更が処理される
- **成功通知**: オーバーレイ形式で詳細を表示
  - 「管理を続ける」ボタン: リロード後も管理モードを維持（`sessionStorage`使用）
  - 「管理を終了する」ボタン: 通常モードに戻る

### 5.6 データ検証機能

- **不整合チェック**: `target`/`condition_target`の形式を検証し、不正な値を持つエイリアンを検出
- **不整合表示**: 
  - 不整合エイリアンは名前が赤く表示される
  - 「不整合」ボタンで絞り込み可能
  - 個性・特技管理モーダルで不整合フィールドが赤色で表示される

### 5.7 APIエンドポイント

- `/api/admin/login`: ログイン（POST、パスワード検証）
- `/api/admin/logout`: ログアウト（POST）
- `/api/admin/check-auth`: 認証状態確認（GET）
- `/api/admin/get-effects/<skill_text>`: 効果取得（管理モード専用、効果とエイリアン情報を返す）
- `/api/admin/get-unregistered`: 未登録効果取得（管理モード専用、使用件数とskill_text情報を含む）
- `/api/admin/apply-changes`: 変更一括適用（POST、`add`/`update`/`delete`の変更を一括適用、バックアップ作成）
- `/api/admin/dictionary/add`: 辞書追加（POST、`target`/`condition_target`/`show_target`/`show_condition_target`を含む）
- `/api/admin/dictionary/update-show-flags`: 表示フラグ更新（POST、`show_target`/`show_condition_target`を更新）
- `/api/admin/dictionary/mass-update`: 一括置換（POST、未登録効果名を辞書の効果名に一括置換）
- `/api/admin/validate-targets`: バリデーション（POST、`target`/`condition_target`の形式を検証）
- `/api/admin/get-effect-info/<effect_name>`: 効果情報取得（GET、`effect_type`/`category`/`target`/`condition_target`/表示フラグを返す）
- `/api/admin/check-skill-type/<skill_text>`: skill_textが特技か個性かを判定（GET）
- `/api/admin/get-effect-usage`: 効果使用数取得（GET、効果名ごとの使用数を返す）

### 5.8 バックアップ処理

- **バックアップファイル**: `backups/skill_verified_effects_backup.jsonl`（固定ファイル名）
- **追記形式**: 毎回新しいファイルを作成せず、1つのファイルに追記
- **バックアップタイミング**: 変更適応（`/api/admin/apply-changes`）実行時
- **バックアップ内容**: 
  - タイムスタンプマーカー（`{"__backup_timestamp__": "YYYYMMDDTHHMMSS"}`）
  - 変更前の全データ（`skill_text_verified_effects`テーブルの全レコード）
- **実装**: `app.py`の`create_backup()`関数で処理

-----

## 6. よくある質問

**Q: なぜ解析単位を「キャラごと」から「個性テキストごと」に変更した？**
→ **効率化**と**不整合防止**のため。

1. **効率化**: 解析対象が1380個性から、ユニークな約870個性に減少する。
2. **不整合防止**: 同一個性を持つ別キャラがLLM解析で別々の結果になる不整合がなくなる。

**Q: バフ・デバフ絞り込みの仕組みは？**
→ `correct_effect_names`テーブルから効果名を取得し、`skill_text_verified_effects`テーブルで各エイリアンの個性ごとの効果を判定。フィルターでは「個性1〜3」の選択と「効果名」の選択を組み合わせて絞り込みを行う。デフォルトでは全個性が選択され、効果名は未選択。

**Q: correct_effect_namesテーブルの主キー構造は？**
→ `(correct_name, category)`の複合主キー。同じ効果名（例: "いどうアップ"）でも、個性用（`category = "BUFF_BOOST"`）と特技用（`category = "S_SKILL_BUFF"`）で別レコードとして保存される。これにより、個性と特技で同じ効果名が存在する場合でも、それぞれが独立して管理され、上書きされることがない。

**Q: `target`と`condition_target`の違いは？**
→ `target`はバフ・デバフが付与される対象（効果を受ける対象）。`condition_target`は効果が作用する条件対象で、バフのみ使用。「昆虫属性に対する与ダメージアップ」の場合、`target: "自分"`（効果を受けるのは自分）、`condition_target: "a:2"`（昆虫属性に対する効果）。デバフの場合は`condition_target`は使用せず、`target`のみで対象を指定（例: "昆虫属性の敵" → `target: "a:2"`）。

**Q: `S_Skill`と`S_Skill_text`の扱いは？**
→ データベースでは大文字の`"S_Skill"`, `"S_Skill_text"`として保存される（PostgreSQLの大文字小文字区別のため引用符が必要）。Python/JavaScriptコード内では`normalize_alien_row()`関数（`scripts/utils/db_helpers.py`）で`s_skill`, `s_skill_text`に正規化して使用する。SQLクエリでは必ず`"S_Skill"`, `"S_Skill_text"`として参照すること。

-----

## 7. 運用・デプロイ仕様（すべて実装済み）

### 7.1 自動スクレイピング（GitHub Actions）

- スケジュール: 毎日00:02（JST）に `/.github/workflows/daily_scraping.yml` が起動
- モード: 逆順（デフォルト）、`--full-scrape`、`--scrape-ids`、`--skip-scraping --analysis-ids`
- 処理フロー: alienテーブル更新 → 画像ダウンロード → 未解析テキスト抽出 → 必要時のみLLM解析 → Discord通知
- 解析対象ID: 逆順/全体=`new_alien_ids`、部分=`scrape_ids`（解析スキップ）、解析専用=`analysis_ids`
- 差分解析: `get_unanalyzed_skill_texts_for_alien_ids()`で既存解析との差分だけをLLMに投げる
- GitHub Actionsでは `AUTO_GIT_PUSH=1` 等を渡しており、処理完了時に `origin/main` へ自動プッシュする

### 7.2 管理モードでの手動スクレイピング・解析

1. ヘッダー左端「管」→ パスワード入力で管理モードへ
2. 「実行」メニューからモードを選択
   - **全体スクレイピング**: 全件スクレイピング＋解析（Wiki負荷が高いため必要時のみ）
   - **部分スクレイピング**: `parse_id_list()`で正規化した `scrape_ids` だけをスクレイピング（`--skip-analysis`固定）
   - **指定解析**: スクレイピングせず、DB上の既存データのみを `analysis_ids` で再解析
3. いずれもバックグラウンドで `run_automated_update.py` を起動し、UIとDiscordに進捗を返す

補足:
- `scrape_ids` は画像/ステータス同期用途で解析は必ずスキップ
- 解析専用モードはDBに存在しないIDを自動除外
- ◯/✗や名前の赤字表示は `ALIEN_SKILL_DATA` と `checkInvalidData()` を共通利用し、管理モード専用分岐を増やさない

### 7.3 Git自動プッシュと資産同期

- `scripts/run_automated_update.py` は成功終了時に `AUTO_GIT_TARGETS`（既定: `static/images`, `backups/skill_list_fixed.jsonl`）を強制ステージ→コミット→ `AUTO_GIT_REMOTE/AUTO_GIT_BRANCH`（既定: `origin/main`）へプッシュ
- 主な環境変数: `AUTO_GIT_PUSH`, `AUTO_GIT_TARGETS`, `AUTO_GIT_COMMIT_MESSAGE`, `AUTO_GIT_USER_NAME`, `AUTO_GIT_USER_EMAIL`, `AUTO_GIT_REMOTE`, `AUTO_GIT_BRANCH`, `ADMIN_AUTO_GIT_PUSH`
- 管理モードから起動するサブプロセスは `ADMIN_AUTO_GIT_PUSH` が未設定または真値なら `AUTO_GIT_PUSH=1` を強制付与し、`ADMIN_AUTO_GIT_PUSH=0` の場合は常に push 無効で起動する（`build_scraper_subprocess_env()` で制御）
- GitHub Actionsでは `GITHUB_TOKEN` と上記環境変数をセットした状態で `actions/checkout@v4（fetch-depth: 0）` を実行
- Render 等の常設環境では Start Command を以下の1行にして `.git` 初期化と `origin` 設定を自動化（`GIT_PAT` は環境変数で管理）
  bash -lc 'set -e; cd /opt/render/project/src; git config --global --add safe.directory /opt/render/project/src; if [ ! -d .git ]; then git init; git remote add origin https://${GIT_PAT}@github.com/fuyuyu0111/elitama-generator.git; git fetch origin main; git checkout -f main; elif git remote | grep -q origin; then git remote set-url origin https://${GIT_PAT}@github.com/fuyuyu0111/elitama-generator.git; else git remote add origin https://${GIT_PAT}@github.com/fuyuyu0111/elitama-generator.git; fi; gunicorn app:app'

- スクレイピングや解析フェーズで例外が出ず正常終了したケースに限り `auto_push_updated_assets_if_needed()` が呼ばれ、問題が発生した際はコミット／プッシュをスキップしてログに失敗理由のみ出力
- 自動プッシュに失敗した場合は `[auto-push] プッシュに失敗しました: ...` とログを残すだけで処理は継続し、後から手動で同期できる
- ローカル実行時は `AUTO_GIT_PUSH` を設定しない限り push されない

### 7.4 PWA化（Progressive Web App）

- `manifest.json` と PWA 用 meta タグを用意し、「ホーム画面に追加」で standalone 表示に切り替え
- 192x192 / 512x512 アイコンを同梱し、モバイルでもアプリ感覚で操作できる