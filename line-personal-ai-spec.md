# LINE専用パーソナルAI 実装仕様書 v0.4

作成日: 2026-08-26（v0.2 同日更新 / v0.3 2026-08-25 12章にフェーズ0・検証・フェーズ5〜6・運用プロンプトを追加、14章に出典追記 / v0.4 2026-08-25 モデルIDを claude-opus-5 に訂正、3.3の履歴の渡し方を訂正、11章に実装時の判断6件を追記、12.10にフェーズ2〜4実装後の是正プロンプトを追加）

**このファイルはリポジトリ直下に `line-personal-ai-spec.md` として置き、CLAUDE.md から `@line-personal-ai-spec.md` で参照する。**
対象: Mac mini上で動作する、Claude APIを使ったLINE公式アカウント連携チャット

## 0. 決定事項

| 項目 | 決定 |
|---|---|
| 暗号鍵の保管 | macOS Keychain（Mac miniのログインキーチェーン） |
| 画像生成 | 対象外。画像の受信・理解のみ対応 |
| 動画 | 対象外 |
| 使用モデル | `claude-fable-5`（既定）/ `claude-opus-5`（切替可）。v0.4訂正: 旧記載 `claude-opus-4-8` は公式モデル一覧の現行IDに無い（14章） |
| 実行環境 | Mac mini 1台。開発・デプロイはClaude Codeで実施 |
| LINE公式アカウント作成 | 手動。Webhook設定以降を自動化対象とする |
| 実装言語 | Python 3.12以上。SDKは `anthropic` と `line-bot-sdk`（v3）、暗号化は `cryptography` |

## 1. 問題定義

オーナーがLINEで話しかけると、オーナーの手元の資料を読み込んだ上で「本人らしく」返事をするAIが欲しい。既存のチャットサービスでは資料の永続的な取り込みと応答人格の固定を自分の管理下で行えない。

## 2. ゴール / 非ゴール

### ゴール
1. LINEでメッセージを送ると、Mac mini上のClaudeが返答する
2. 応答待ちの間、LINE画面にローディングアニメーションが出る
3. Claude APIのレート制限に当たっても、待って自動再送する
4. LINEから送ったファイルを暗号化して保存し、以降の会話で参照する
5. Mac mini上のコードはオーナーが読める。保存済みファイルは暗号化状態で置かれる

### 非ゴール
- 画像生成・動画生成（決定により除外）
- グループチャット対応（ローディングアニメーションが1対1限定のため、v1は1対1のみ）
- LINE公式アカウント自体の自動作成（API提供の有無を確認できず）
- オーナー（Mac mini管理者）に対する暗号学的な秘匿。Keychain方式ではroot権限を持つ管理者が復号できる。本仕様の暗号化は「ディスク上の平文放置を防ぐ」目的であり、管理者からの秘匿は運用ルールで担保する

## 3. システム構成

```
LINEアプリ
  │
  ▼
LINE Platform ──(Webhook / HTTPS)──▶ Cloudflare Tunnel ──▶ Mac mini
                                                            │
  ┌─────────────────────────────────────────────────────────┤
  │ gateway      : 署名検証 / 即時200応答 / Loading表示 / 返信送信
  │ orchestrator : ジョブキュー / 会話履歴 / 429待機 / モデル選択
  │ claude client: Messages API 呼び出し（streaming可）
  │ memory       : 暗号化ストア + 要約索引（ユーザー別）
  │ keychain     : 暗号鍵の取得（security CLI or keyring）
  │ claude-code  : 開発・デプロイ・自動化（オーナー操作）
  └─────────────────────────────────────────────────────────
```

### 3.1 gateway
- LINEからのWebhookを受け、`X-Line-Signature` をチャネルシークレットで検証する
- 検証後すぐに200を返し、処理はorchestratorのキューへ渡す
- 受信直後に Loading animation API（`POST /v2/bot/chat/loading/start`）を呼ぶ。`loadingSeconds` は最大60。60秒を超える処理では再度呼び直す
- 返信は原則 reply（replyToken使用）。replyTokenの期限内に返せない場合は push に切り替える

### 3.2 orchestrator
- ユーザーIDごとに直列キュー。同一ユーザーの並行処理はしない
- 会話履歴はSQLite（暗号化ストア内）に保存し、直近N往復＋要約をClaudeに渡す
- 429受信時: `retry-after` ヘッダーの秒数だけ待って再送。待機が20秒を超える見込みならユーザーへ「○秒後に返答します」とpushで通知する
- 529（overloaded）は指数バックオフで再試行。retry-afterのカウントとは分ける
- モデル切替: LINE上のコマンド（例 `/model opus`）でユーザー単位に保存

### 3.3 claude client
- 公式SDK `anthropic`（Python）を使用。SDKの自動再試行は `max_retries=0` で無効化し、再試行は orchestrator が一元管理する（v0.4訂正。SDKとorchestratorの二重再試行を避けるため。11章参照）
- system promptの構成: 人格定義（オーナーが編集）＋ ユーザー別の資料要約。直近会話は system ではなく `messages` 配列として渡す（v0.4訂正。Messages API の本来の形式であり、実装もそうなっている）
- 画像を受信した場合は base64 で `image` ブロックとして渡し、理解・説明・分析に使う

### 3.4 memory（暗号化ストア）
- 保存対象: LINEから受信したファイル本体、抽出テキスト、要約、会話履歴
- 暗号方式: AES-256-GCM。ファイルごとに乱数nonce
- 鍵: Keychainに保存したマスター鍵1本（32バイト乱数）。ユーザー別に HKDF で派生させ、ユーザー間で鍵を分ける
- 取り込みフロー:
  1. Webhookの `file` / `image` メッセージを受信
  2. `GET https://api-data.line.me/v2/bot/message/{messageId}/content` で本体を取得（メモリ上）
  3. テキスト抽出（PDF/docx/txt）→ 要約生成 → 全て暗号化して書き込み
  4. 平文は一時ディレクトリにも残さない
- 参照フロー: 会話ごとに要約索引から関連資料を選び、メモリ上で復号してsystem promptへ注入
- ディスク上の見え方: `data/<userId>/<uuid>.enc` のみ。ファイル名も暗号化して保存

### 3.5 keychain
- 初回セットアップ: `security add-generic-password -a line-ai -s master-key -w <hex>` でマスター鍵を登録
- 起動時: `security find-generic-password -a line-ai -s master-key -w` を `subprocess` で呼ぶか、`keyring` ライブラリ（macOS Keychainバックエンド）で取得し、プロセスメモリにのみ保持
- LINEチャネルアクセストークン、チャネルシークレット、Anthropic APIキーも同じ方式でKeychainに置く。`.env` に平文で置かない
- 制約: Mac miniにログインできる管理者は同じコマンドで鍵を取り出せる（非ゴール参照）

### 3.6 claude-code
- リポジトリ構成、依存導入、launchd登録、Cloudflare Tunnel設定をClaude Codeで実施
- Claude Codeは `data/` ディレクトリを読まない（v0.3訂正: `.claudeignore` は公式に存在しない。`.claude/settings.json` の `permissions.deny` と PreToolUse フックで除外。12章フェーズ0参照）
- オーナーはコードを読み、リリース操作（launchd再起動、Tunnel再設定）を手動でも実行できる

## 4. データフロー（1メッセージ）

1. ユーザーがLINEで送信
2. LINE Platform → Tunnel → gateway。署名検証、200応答
3. gateway → Loading animation 開始（20秒）
4. orchestrator がキューから取り出し、memory から履歴＋関連要約を復号
5. claude client が Messages API を呼ぶ。429なら retry-after 待機
6. 応答をLINEへ reply（期限切れなら push）。Loadingは自動消滅
7. 往復を暗号化して履歴に追記

## 5. 要件と受入基準

### P0（必須）
- [ ] 不正署名のWebhookを400で拒否する
- [ ] テキスト受信から3秒以内にLoading animationが表示される
- [ ] 429を受けたとき、retry-after秒後に再送して応答が返る
- [ ] ファイル送信後、`data/` 配下に平文ファイルが存在しない（`strings` で内容が読めない）
- [ ] Mac mini再起動後、launchdで自動起動し応答できる
- [ ] Keychainにマスター鍵が無い状態では起動せず、エラーを記録して停止する

### P1
- [ ] `/model` コマンドでモデル切替
- [ ] 待機が長い場合の「○秒後に返答します」通知
- [ ] 資料要約の再生成コマンド

### P2（設計上考慮のみ）
- 複数ユーザー対応（鍵派生は既にユーザー別）
- 鍵をユーザー由来パスフレーズへ移行する選択肢を残す（暗号層をインターフェース化）

## 6. 運用上の注意

- LINEの push メッセージは月間の無料通数に上限がある。reply は上限対象外のため、可能な限り reply で返す（具体的な通数はLINE公式の料金ページで確認）
- Claude APIの利用上限はorganization単位。Claude Codeを同じAPIキーで動かすとレート枠を共有する
- Mac miniのスリープ無効化、Tunnelの常駐化が必要

## 7. 未解決事項

| 質問 | 担当 | ブロッキング |
|---|---|---|
| replyTokenの有効期限の正確な値 | LINE公式リファレンス確認 | 非 |
| push無料枠の通数（現行プラン） | LINE公式料金ページ確認 | 非 |
| Fable 5 と Opus 5 の料金・レート上限 | Anthropic料金ページ確認 | 非 |

## 7.1 Python構成（確定）

| 役割 | ライブラリ |
|---|---|
| Webサーバー | FastAPI + uvicorn |
| LINE | line-bot-sdk（v3） |
| Claude | anthropic |
| 暗号化 | cryptography（AESGCM、HKDF） |
| Keychain | subprocess経由の `security` CLI または keyring |
| DB | sqlite3（標準）。履歴は暗号化して格納 |
| テキスト抽出 | pypdf、python-docx |
| 常駐 | launchd（plist）。仮想環境は `uv` または `venv` |

## 8. フェーズ分け

| フェーズ | 内容 | 完了条件 |
|---|---|---|
| 1 | Webhook受信→Claude→reply の最小往復 | テキストで会話できる |
| 2 | Loading表示、429待機、launchd常駐 | P0を全て満たす |
| 3 | 暗号化ストア、ファイル取り込み、要約参照 | ファイル送信後の会話に反映される |
| 4 | Claude Codeによるデプロイ自動化 | オーナーが1コマンドで再デプロイできる |

## 9. 出典

- LINE Loading animation: https://developers.line.biz/en/docs/messaging-api/use-loading-indicator/ （機能公開 2024-04-17。5〜60秒、1対1のみ）
- LINE Messaging API リファレンス（レート上限、Loading 100 req/s）: https://developers.line.biz/en/reference/messaging-api/
- Claude API レート制限（429 と retry-after）: https://platform.claude.com/docs/en/api/rate-limits （2026-07-25 更新）
- Claude API エラー仕様（SDK自動再試行、529）: https://platform.claude.com/docs/en/api/errors （2026-06-23 更新）
- Claudeの画像出力非対応: Anthropic公式ドキュメントに画像出力機能の記載なし。第三者解説 https://claudeai.guide/faq/can-claude-ai-generate-images/ （2026-07-12）
- AES-GCM / HKDF: 標準的な暗号方式。実装は各言語の標準ライブラリまたは `cryptography`（Python）を想定

## 10. 最終ゴール（確定版）

> Mac mini上でPythonで動作し、LINE公式アカウント経由でClaude API（Fable 5 / Opus 5）と会話できる自分専用AI。ローディング表示とレート制限待機に対応し、LINEから送ったファイルをKeychain管理の鍵で暗号化保存して会話に反映する。画像・動画の生成は行わない。

## 11. 決定の経緯

| 日付 | 決定 | 選択肢と理由 |
|---|---|---|
| 2026-08-26 | 暗号鍵はKeychain | ユーザー由来パスフレーズ方式はオーナーから秘匿できるが、忘却時に復旧不可・毎回入力が必要。運用の軽さを優先 |
| 2026-08-26 | 画像生成なし | Claude APIは画像出力非対応。外部API接続は別料金・別キーが必要になるため除外 |
| 2026-08-26 | Python | TypeScriptはClaude Codeとランタイム共用・標準cryptoで暗号化完結の利点があったが、資料解析ライブラリの充実とオーナーの可読性を優先 |
| 2026-08-25 | 切替先モデルは `claude-opus-5` | v0.2の `claude-opus-4-8` は公式モデル一覧の現行IDに無く、実装（フェーズ2〜4）は `claude-opus-5` を採用済み。仕様書側を実装に合わせた |
| 2026-08-25 | SDK自動再試行を無効化（`max_retries=0`） | 有効のままだと SDK と orchestrator が二重に再試行し、待ち時間が retry-after どおりにならない。orchestrator 一元管理を選択 |
| 2026-08-25 | 直近履歴は `messages` で渡す | system prompt に履歴を埋める案は Messages API の形式に反し、キャッシュ効率も悪い。system は人格＋資料要約のみ |
| 2026-08-25 | 画像は vision で要約して索引化 | 画像はテキスト抽出経路が無い。Claude の画像理解で要約を作り、資料と同じ索引に載せる |
| 2026-08-25 | 資料検索は文字 bigram の重なりで採点 | 日本語は空白で単語が切れず、形態素解析を入れると依存が増える。要約索引は暗号化されており、当たったものだけメモリ上で復号する |
| 2026-08-25 | マスター鍵が無いときは exit 0 で停止 | launchd の `KeepAlive.SuccessfulExit=false` は「異常終了時のみ再起動」。鍵が無い状態で再起動ループさせないために正常終了させる。**鍵登録後は `launchctl kickstart -k` で手動再起動が必要** |
| 2026-08-25 | 設定値（モデル選択など）のみ非暗号化 | 本文・ファイル・要約・履歴は暗号化。設定値は秘匿価値が低く、起動時に鍵なしでも読める必要がある |
| 2026-08-25 | Loading は15秒ごとに打ち直す | 仕様の「60秒超で呼び直す」より安全側。キュー投入前に呼ぶことで、前のメッセージ処理中でも3秒以内表示を守る |
| 2026-08-25 | `data/<userId>/` のディレクトリ名は平文のまま | 仕様どおり。隠すなら HKDF でディレクトリ名を派生させる（メタデータ秘匿↑、一覧・削除に逆引き表が必要↓）。v1は1対1運用のため見送り |
| 2026-08-26 | フックは `data` を**パスの一区切り**として判定する | 12.10 の字面どおり「`data/` を含む」で照合すると、(a) `cd data && cat *` がスラッシュ無しで素通りし、(b) `metadata/` `bigdata/` `data_export/` を誤爆する。区切り照合なら両方解決し、仕様の要求の厳密な上位互換になる |
| 2026-08-26 | 鍵欠落時の終了コードは **0** を採用（12.5 との食い違いを解消） | 12.5 フェーズ2検証4は「exit code 1」と書くが、11章（2026-08-25）は exit 0 を決めている。exit 1 にすると `KeepAlive.SuccessfulExit=false` が「異常終了」とみなして**鍵が無いまま再起動ループする**ため、11章を採る。12.5 の期待する `master-key not found` はログに出すことで満たした |
| 2026-08-26 | `/health` を `/healthz` の別名として追加 | 12.5 フェーズ4検証と 12.6 は `/health` を参照し、実装は `/healthz` だった。中身を増やすのはフェーズ5の仕事なので、いまは同じ最小応答を返す別名だけ置く |
| 2026-08-26 | ruff の対象ルールを明示指定し、RUF001〜003 を無効化 | 既定任せだとruffの版で結果が変わる。全角の `？` `（）` を「紛らわしい文字」として警告する規則は、日本語を書く以上ただの雑音 |
| 2026-08-26 | 既定モデルは **`claude-fable-5`**（0章に実装を合わせた） | 実装は `claude-opus-5` を既定にしていて0章と食い違っていた。単価が2倍違う（Fable 5 は $10/$50、Opus 5 は $5/$25）ため勝手に寄せず、オーナーの判断を仰いだ結果「0章どおり Fable 5」と決定。`app/config.py` の `DEFAULT_MODEL` と `SELECTABLE_MODELS` の並び順を変更し、`/model opus-5` で切り替え可能な点は従来どおり |

## 12. Claude Code 投入プロンプト（フェーズ別）

各フェーズは前のフェーズの動作確認後に投入する。

### 投入時の共通ルール

1. **秘密情報をプロンプトに貼らない。** チャネルシークレット、アクセストークン、Anthropic APIキー、マスター鍵は、フェーズ0で作る `scripts/setup_keychain.sh` を**オーナー自身がターミナルで実行**して登録する。Claude Codeには「Keychainから読む」ことだけを指示する
2. 各プロンプトの先頭に `line-personal-ai-spec.md を読み、` を付けているのは、Claude Codeが仕様書を毎回参照するため。CLAUDE.md（フェーズ0で作成）にも仕様書への参照を書くので、2回目以降は省略してもよい
3. 1フェーズ1セッションを原則とする。フェーズをまたぐと文脈が長くなり、前フェーズの決定が崩れやすい
4. 各フェーズの直後に「検証プロンプト」（12.5）を投入し、P0/P1の受入基準を機械的に確認してから次へ進む
5. `.claudeignore` という仕組みは Claude Code 公式ドキュメントに存在しない（2026-08-25 確認、出典は14章）。`data/` の除外は `.claude/settings.json` の `permissions.deny` と PreToolUse フック で行う。フェーズ4の「.claudeignore等」はこの方法に読み替える

### フェーズ0：初期セットアップ（フェーズ1の前に1回だけ）

目的: リポジトリの骨格、Claude Codeの設定（`data/` 読み取り禁止・CLAUDE.md）、Keychain登録スクリプトを作る。**この段階ではLINEにもClaude APIにも接続しない。**

```
line-personal-ai-spec.md を読み、フェーズ0として次を作って。まだLINEやClaude APIには接続しない。

1. リポジトリ骨格
   - uv init（Python 3.12）。パッケージ名は line_personal_ai
   - 依存: fastapi, uvicorn, line-bot-sdk（v3）, anthropic, cryptography, pypdf, python-docx, keyring
   - 開発依存: pytest, pytest-asyncio, httpx, respx, ruff
   - ディレクトリ: src/line_personal_ai/{gateway,orchestrator,claude_client,memory,keychain}/ 、tests/ 、scripts/ 、launchd/ 、data/（.gitkeepのみ）
   - .gitignore に data/ 、*.enc 、*.db 、.env 、.cloudflared/ 、.claude/settings.local.json を入れる

2. Claude Code の設定
   - .claude/settings.json を作り、permissions.deny に
     "Read(./data/**)" "Edit(./data/**)" "Write(./data/**)"
     "Bash(cat data/*)" "Bash(strings data/*)" "Bash(cp data/*)" "Bash(mv data/*)" "Bash(rm -rf *)"
     を入れる
   - PreToolUse フック（matcher: "Bash"）として scripts/hooks/block_data_access.sh を作る。
     stdin の JSON から tool_input.command を読み、"data/" を含む場合は exit 2 で拒否する。
     ただし "ls data/" と "ls -la data/" と "du -sh data/" は許可する（ファイル名は暗号化されているので一覧は安全）
   - CLAUDE.md をリポジトリ直下に作る。内容:
     - プロジェクトの一行説明と @line-personal-ai-spec.md の参照
     - 絶対ルール: data/ を読まない。秘密情報を .env・ログ・テストコードに書かない。
       秘密情報は Keychain（service名 line-ai）からのみ読む
     - 使うコマンド: uv run pytest / uv run ruff check / make deploy（フェーズ4で追加）
     - モデルIDは claude-fable-5 と claude-opus-5 の2つだけ。他を勝手に使わない
     - 日本語でコメントとログを書く

3. Keychain 登録スクリプト scripts/setup_keychain.sh
   - オーナーがターミナルで対話実行する前提。引数や環境変数で秘密を受け取らない
   - read -s で次の4つを入力させ、security add-generic-password -U -a line-ai -s <name> -w "<値>"
     で登録する。<name> は line-channel-secret / line-channel-access-token / anthropic-api-key / master-key
   - master-key は入力させず、python3 -c "import secrets; print(secrets.token_hex(32))" で
     32バイト（hex 64文字）を生成して登録する。既に存在する場合は上書きせず、警告して終了する
   - -T /usr/bin/security と -T "$(which python3)" を付け、launchd 起動時にダイアログが出ないようにする
   - 最後に security find-generic-password -a line-ai -s master-key -w の結果を「長さのみ」表示して
     登録確認する（値は表示しない）

4. keychain モジュール src/line_personal_ai/keychain/__init__.py
   - get_secret(name: str) -> str 。subprocess で security find-generic-password -a line-ai -s <name> -w
     を呼ぶ。見つからない場合は KeychainMissingError を送出する
   - 取得した値はモジュール内でキャッシュしない。呼び出し側がプロセス起動時に1回だけ取得して保持する
   - macOS 以外では ImportError ではなく RuntimeError("macOS専用") を送出する

5. テスト
   - tests/test_keychain.py: subprocess をモックし、存在しない場合に KeychainMissingError になること
   - tests/test_hook.py: block_data_access.sh に "cat data/x.enc" を渡すと exit 2、"ls data/" は exit 0

6. README.md に「初回セットアップ手順」を書く:
   brew install uv → uv sync → bash scripts/setup_keychain.sh → uv run pytest
   ここまでで pytest が全て通ることを完了条件とする
```

**フェーズ0のオーナー作業（Claude Codeにやらせない）**

| 手順 | コマンド | 備考 |
|---|---|---|
| Keychain登録 | `bash scripts/setup_keychain.sh` | 4つの秘密を対話入力。master-key は自動生成 |
| 登録確認 | `security find-generic-password -a line-ai -s master-key -w \| wc -c` | 65（64文字+改行）になれば正常 |
| バックアップ | `security find-generic-password -a line-ai -s master-key -w` の出力を紙またはパスワードマネージャに保管 | **この鍵を失うと data/ は復号不能** |

### フェーズ1：最小往復
```
line-personal-ai-spec.md を読み、フェーズ1（Webhook受信→Claude→reply の最小往復）を実装して。
Python 3.12、uv、FastAPI、line-bot-sdk v3、anthropic を使う。
LINEのチャネルシークレット・アクセストークン・Anthropic APIキーは
macOS Keychain（security find-generic-password）から読む。.envに平文で置かない。
data/ ディレクトリは読まない。完了後、ローカルでの起動手順とCloudflare Tunnelの設定手順を README に書いて。
```

### フェーズ2：Loading表示・429待機・常駐
```
仕様書の3.1と3.2を実装して。
1. Webhook受信直後に POST /v2/bot/chat/loading/start を呼ぶ（loadingSeconds=20、上限60。
   Claude応答が60秒を超えそうなら再度呼ぶ）。
2. Claude APIが429を返したらretry-afterヘッダーの秒数だけ待って再送。
   待機が20秒を超える場合は「◯秒後に返答します」をpushで送る。
   529は指数バックオフで再試行し、retry-afterとは分ける。
3. ユーザーIDごとに直列キュー。
4. launchdのplistを作成し、ログイン時に自動起動・異常終了時に再起動させる。
   Keychainにマスター鍵が無い場合は起動せずログを残して停止する。
完了後、P0の「3秒以内にLoading表示」「429後に応答が返る」「再起動後に自動起動」を確認する手順を書いて。
```

### フェーズ3：暗号化ストア・ファイル取り込み
```
仕様書の3.4と3.5を実装して。
1. Keychainの32バイトマスター鍵からHKDFでユーザー別鍵を派生し、AES-256-GCMで暗号化。
   ファイルごとに乱数nonce。保存先は data/<userId>/<uuid>.enc、ファイル名も暗号化。
2. Webhookのfile/imageメッセージを受けたら
   GET https://api-data.line.me/v2/bot/message/{messageId}/content でメモリ上に取得。
   pypdf/python-docxでテキスト抽出→Claudeで要約→本体・抽出テキスト・要約を全て暗号化保存。
   平文を一時ディレクトリに書かない。
3. 会話履歴もsqlite3に暗号化して保存。直近N往復＋要約をsystem promptに渡す。
4. 会話時は要約索引から関連資料を選び、メモリ上で復号してsystem promptに注入する。
完了後、data/配下でstringsコマンドを実行しても内容が読めないことを確認する手順を書いて。
```

### フェーズ4：デプロイ自動化
```
仕様書の3.6を実装して。
1. make deploy（またはスクリプト1本）で、依存更新→テスト→launchd再起動→
   Cloudflare Tunnelの疎通確認まで行う。
2. フェーズ0で作った .claude/settings.json の permissions.deny と PreToolUse フックが
   data/ を除外できているか再確認する（.claudeignore は使わない。公式に存在しない）。
3. /model コマンドで claude-fable-5 と claude-opus-5 をユーザー単位で切替できるようにする（P1）。
4. READMEに、オーナーが手動でリリース・ロールバックする手順を書く。
```

### 12.5 検証プロンプト（各フェーズ直後に投入）

各フェーズの実装セッションとは**別セッション**で投入する。同じセッションだと「自分の実装は正しい」前提で確認が甘くなるため。

#### フェーズ0 検証
```
line-personal-ai-spec.md の 3.5 と 3.6 に照らして、フェーズ0の成果物をレビューして。
1. .claude/settings.json の permissions.deny を読み、data/ に対する Read / Edit / Write が拒否されているか
2. scripts/hooks/block_data_access.sh に次の入力を実際に流して結果を表にして:
   cat data/a.enc / strings data/a.enc / ls data/ / python -c "open('data/a.enc')" / cp data/a.enc /tmp/
   最後の2つが拒否されないなら、フックの判定を「data/ を含む全コマンド。ただし ls / du のみ許可」に直して
3. scripts/setup_keychain.sh に秘密を引数や環境変数で受ける経路が無いか
4. リポジトリ全体を grep して、sk-ant- / channel secret らしき文字列 / .env 参照が無いか
5. 問題があれば修正し、uv run pytest が通ることを確認して。修正点を箇条書きで報告して
```

#### フェーズ1 検証
```
フェーズ1の最小往復を、LINE実機なしで検証するテストを書いて。
1. tests/test_gateway.py:
   - 正しい X-Line-Signature（チャネルシークレットで HMAC-SHA256 → base64）なら 200
   - 署名が違う / ヘッダーが無い場合は 400（P0）
   - 署名検証に使うシークレットはテスト用の固定値をモンキーパッチで注入し、Keychain を呼ばない
2. tests/test_claude_client.py:
   - respx で api.anthropic.com をモックし、messages.create の呼び出し引数に model=claude-fable-5 が入ること
3. 実機確認の手順を README に追記:
   - cloudflared tunnel --url http://localhost:8000 で一時URL（trycloudflare.com）を取得
   - LINE Developers コンソールの Webhook URL に <一時URL>/webhook を設定し「検証」ボタンで 200 を確認
   - LINEから「こんにちは」を送って返答が来ること
   - 一時URLは開発用（SLAなし・URLは起動ごとに変わる）で、本番はフェーズ2で名前付きトンネルにすることを明記
```

#### フェーズ2 検証
```
P0 の「3秒以内にLoading表示」「429後に応答が返る」「再起動後に自動起動」「マスター鍵が無いと起動しない」を検証して。
1. tests/test_loading.py: Webhook受信から loading/start 呼び出しまでの経過時間を計測し 1秒未満であること
   （Claude API 呼び出しをモックして待たせても Loading が先に呼ばれること）
2. tests/test_retry.py: respx で 1回目 429（retry-after: 3）→ 2回目 200 を返し、
   合計待機が 3秒以上・4秒未満で成功すること。529 は retry-after が無くても指数バックオフで再試行されること。
   待機20秒超のときに push が1回だけ送られること
3. tests/test_queue.py: 同一ユーザーの2メッセージが直列、別ユーザーは並行になること
4. tests/test_startup.py: keychain.get_secret をモックして KeychainMissingError を出すと、
   プロセスが exit code 1 で終了し、ログに "master-key not found" が残ること
5. launchd の確認手順を README に:
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.line-ai.agent.plist
   launchctl kickstart -k gui/$(id -u)/com.line-ai.agent
   launchctl print gui/$(id -u)/com.line-ai.agent | grep state
   再起動 → 上記 print で running を確認 → LINE から送信して返答
6. plist は LaunchAgent（~/Library/LaunchAgents）であること。LaunchDaemon にするとログインキーチェーンが
   使えず Keychain 取得に失敗するので、その旨をコメントで plist と README に残して
```

#### フェーズ3 検証
```
P0 の「data/ 配下に平文が無い」を検証して。
1. tests/test_crypto.py:
   - 同じ平文でも nonce が毎回異なり、暗号文が異なること
   - ユーザーAの派生鍵でユーザーBのファイルを復号すると InvalidTag になること
   - ファイル名が暗号化され、data/<userId>/ 配下のファイル名から元の名前が推測できないこと
2. tests/test_ingest.py: 1ページのPDF（既知の日本語文）を取り込み、
   data/ 配下の全ファイルに対して strings 相当の走査（連続4文字以上の印字可能文字列抽出）をして、
   元の文が1文字も出てこないこと。/tmp と $TMPDIR にも平文が残っていないこと
3. 実機手順を README に:
   LINE からPDFを送る → find data -type f | xargs strings | grep <文中の単語> が空
   → 「さっき送った資料の要点は？」と聞いて内容が返ること
```

#### フェーズ4 検証
```
make deploy を壊した状態で実行し、失敗時に旧バージョンが動き続けることを確認して。
1. 意図的に構文エラーを入れて make deploy → テスト失敗で launchd 再起動まで進まないこと
2. 正常時: make deploy → launchctl print で起動時刻が更新 → curl https://<hostname>/health が 200
3. ロールバック: git checkout <前のタグ> && make deploy で戻ること
4. /model opus → 次の返答が claude-opus-5 で生成されたことをログで確認。/model fable で戻ること
5. 上記を README の「リリース確認チェックリスト」として追記して
```

### 12.6 フェーズ5：運用コマンドと観測（P1）

目的: LINEから日常運用できるようにし、コストを見える化する。

```
line-personal-ai-spec.md の P1 と 6章を踏まえて、フェーズ5として次を実装して。
1. LINE コマンド（先頭が / のテキスト。Claude には渡さない）
   /model fable | opus  … 既存（フェーズ4）
   /model            … 現在のモデルを表示
   /reset            … 会話履歴を削除（資料は残す）。「本当に削除しますか？ /reset yes」の2段階
   /files            … 取り込み済み資料の一覧（要約の先頭30文字と取り込み日時。復号はメモリ上のみ）
   /forget <番号>    … /files の番号で資料を削除。data/ の .enc も削除
   /resummarize <番号> … 資料要約の再生成（P1）
   /usage            … 今月の入力/出力トークン数と、料金表に基づく概算額（料金は config/pricing.toml に
                       オーナーが手で書く。値が無ければ「料金未設定」と返す。勝手に数字を入れない）
   /help             … コマンド一覧
2. 観測
   - 全 Claude API 呼び出しの usage（input_tokens / output_tokens / model / 所要秒 / 再試行回数）を
     SQLite の usage テーブルに記録（このテーブルは暗号化不要。本文は含めない）
   - GET /health を追加（Keychain取得済み・DB接続可・直近5分のエラー数を返す。認証不要だが本文情報は出さない）
   - ログは logs/ 配下に日次ローテーション、14日で削除。ログにメッセージ本文・ファイル名・トークンを書かない
3. Loading の再呼び出し（3.1）: 応答生成が 50 秒を超えたら loading/start をもう一度呼ぶ（上限60秒のため）
4. tests を追加し、README にコマンド一覧を追記して
```

### 12.7 フェーズ6：バックアップと鍵の管理（P2の準備）

目的: Mac mini 故障時に復旧できること。鍵の差し替えができること。

```
line-personal-ai-spec.md の 3.4・3.5・P2 を踏まえて、フェーズ6として次を実装して。
1. scripts/backup.sh
   - data/ と SQLite を tar.gz にして外部ディスクまたはオーナー指定パスへコピー。暗号化済みなのでそのまま置く
   - マスター鍵は含めない（別経路で保管。フェーズ0のオーナー作業参照）
   - launchd で毎日 03:00 に実行する plist（com.line-ai.backup）を追加
2. scripts/restore.sh
   - 新しい Mac に uv sync → setup_keychain.sh（master-key は生成せず、保管していた値を入力するモードを追加）
     → tar.gz 展開 → 起動、の手順。README に「復旧手順」として書く
3. 鍵ローテーション scripts/rotate_key.py
   - 新マスター鍵を生成 → 全 .enc を旧鍵で復号・新鍵で暗号化（メモリ上で1ファイルずつ、平文をディスクに書かない）
     → Keychain の master-key を更新 → 旧鍵を master-key-prev として30日間保持
   - 途中失敗時に混在しないよう、書き込みは一時ファイル（.enc.new）に行い最後に rename
   - サービス停止中にのみ実行できるようにロックファイルで排他
4. 暗号層のインターフェース化（P2）
   - KeyProvider を抽象クラスにし、KeychainKeyProvider を現行実装とする。
     将来のパスフレーズ方式（PassphraseKeyProvider）はクラスだけ用意して NotImplementedError にする
5. tests: ローテーション後に全ファイルが新鍵で復号でき、旧鍵では InvalidTag になること
```

### 12.8 Claude Code 側の仕組み（スキル・ヘッドレス実行）

フェーズ4の `make deploy` を、Claude Code から `/deploy` として呼べるようにする。

```
.claude/skills/deploy/SKILL.md を作って。
frontmatter:
  name: deploy
  description: テスト→launchd再起動→Tunnel疎通確認までを実行して結果を報告する
  disable-model-invocation: true
  allowed-tools: Bash(make deploy) Bash(launchctl print *) Bash(curl -s https://*/health) Bash(git log *)
本文: make deploy を実行し、失敗したらどの段階で止まったかを報告して止まる。成功したら
launchctl print の state と /health の応答と git log -1 を報告する。data/ には触らない。
```

**ヘッドレス実行（オーナーが cron や別スクリプトから使う場合）**

```bash
# 例: 毎週の依存更新チェックを非対話で実行し、結果だけ JSON で受け取る
claude -p "uv lock --upgrade の差分を確認し、破壊的変更がある依存を列挙して。変更は加えない" \
  --allowedTools "Bash(uv lock *),Bash(git diff *),Read" \
  --output-format json | jq -r '.result'
```

- `-p` は非対話モード、`--allowedTools` は許可ツールを絞る、`--output-format json` は `result` / `session_id` / `usage` / `cost_usd` を含むJSONを返す（出典は14章）
- `.claude/settings.json` の `permissions.deny` はヘッドレス実行でも効く（deny は allow より常に優先）

### 12.9 投入順序とセッションの区切り（まとめ）

| 順 | セッション | 投入するもの | 次へ進む条件 |
|---|---|---|---|
| 1 | 新規 | フェーズ0 | `uv run pytest` 全通過、Keychain登録済み |
| 2 | 新規 | フェーズ0 検証 | 指摘の修正完了 |
| 3 | 新規 | フェーズ1 | LINEで往復できる |
| 4 | 新規 | フェーズ1 検証 | テスト通過、README追記 |
| 5 | 新規 | フェーズ2 | Loading表示・再起動後の自動起動 |
| 6 | 新規 | フェーズ2 検証 | P0の4項目通過 |
| 7 | 新規 | フェーズ3 | ファイル送信が会話に反映 |
| 8 | 新規 | フェーズ3 検証 | strings で読めない |
| 9 | 新規 | フェーズ4 + 12.8 スキル | `make deploy` 1コマンド |
| 10 | 新規 | フェーズ4 検証 | ロールバック確認 |
| 11 | 新規 | フェーズ5 | /usage が返る |
| 12 | 新規 | フェーズ6 | 復旧手順で別環境起動 |

### 12.10 是正プロンプト（フェーズ2〜4実装後・2026-08-25時点の状態向け）

フェーズ2〜4（3.1/3.2/3.4/3.5/3.6）の実装報告（155テスト通過）を受けて、次に投入するもの。**新しいセッション**で投入する。内容は別ファイル `AI社員への指示_v0.4.md` と同一。

```
まずリポジトリ直下の line-personal-ai-spec.md を読んで。無ければ何もせず
「line-personal-ai-spec.md が無い。オーナーが置くまで作業しない」と報告して止まって。

読めたら、次を順番に実施して。各項目の完了時に1行で報告して。

1. CLAUDE.md
   - 先頭に「@line-personal-ai-spec.md」の1行を追加（Claude Code の import 構文）
   - 「モデルIDは claude-fable-5 と claude-opus-5 の2つだけ。他を勝手に使わない」を追記

2. data/ の除外を公式の方法に置き換える
   - .claudeignore を削除する（Claude Code 公式に存在しない仕組み）
   - .claude/settings.json の permissions.deny に次があるか確認し、無ければ追加:
     "Read(./data/**)" "Edit(./data/**)" "Write(./data/**)"
   - .claude/settings.json の hooks.PreToolUse に matcher "Bash" で
     scripts/hooks/block_data_access.sh を登録する。スクリプトは stdin の JSON から
     tool_input.command を読み、"data/" を含めば exit 2（拒否）。ただし
     "ls data/" "ls -la data/" "du -sh data/" は exit 0 で許可する
   - 次の5コマンドを実際にフックへ流し、結果を表で報告して:
     cat data/x.enc / strings data/x.enc / ls data/ / cp data/x.enc /tmp/ / python -c "open('data/x.enc')"
   - tests/test_hook.py を追加

3. モデルID
   - 実装内の claude-opus-5 / claude-fable-5 の使用箇所を列挙して。
     仕様書 0章も claude-opus-5 に訂正済みなので実装は変えない。
     もし claude-opus-4-8 が残っていれば claude-opus-5 に直して

4. マスター鍵なし停止の運用
   - README に「Keychain 登録後は launchctl kickstart -k gui/$(id -u)/<plistのLabel> で
     手動再起動が必要（exit 0 停止のため自動再起動しない）」が書いてあるか確認。無ければ追記

5. 仕様書 12.5 のフェーズ2・3・4 検証との突き合わせ
   - 12.5 の各項目について「既存テストでカバー済み / 未カバー」を表にして
   - 未カバーのものはテストを追加。特に:
     a. 同一ユーザーの2メッセージが直列、別ユーザーが並行
     b. ユーザーAの派生鍵でユーザーBの .enc を復号すると失敗（AAD の検証も含む）
     c. make deploy が構文エラー時に launchd 再起動まで進まない
     d. Loading の再呼び出しが 15 秒間隔で行われ、応答完了後は止まる
   - 12.5 で書かれている「実機手順」が README に全て載っているか確認し、無いものは追記

6. 仕様書 11章「決定の経緯」
   - README の「設計上の判断と、仕様からの逸脱」の各項目が 11章の表に載っているか照合。
     載っていない判断があれば、日付 2026-08-25 で仕様書側に追記して

7. 仕上げ
   - uv run ruff check と uv run pytest を通す
   - 「fix: Claude Code設定を公式方式に統一、仕様書v0.4と整合」でコミットしてプッシュ。PRは作らない
   - 最後に、実機（Mac mini）でしか確認できない項目を README から抜き出して一覧で報告して
```

**このプロンプトを投入する前にオーナーがやること**

| 手順 | 内容 |
|---|---|
| 1 | この v0.4 ファイルをリポジトリ直下に `line-personal-ai-spec.md` という名前でコピーする（`AI社員への指示_v0.4.md` はリポジトリに入れなくてよい） |
| 2 | `git add line-personal-ai-spec.md && git commit -m "docs: 仕様書v0.4を追加"` |
| 3 | Claude Code を**新しいセッション**で起動し、上のプロンプトを貼る |

## 13. 未確認事項（本仕様書作成時点）

- replyTokenの有効期限の正確な値
- LINE pushメッセージの無料通数（現行プラン）
- Fable 5 / Opus 5 の料金・レート上限の具体値
- `cryptography`・`line-bot-sdk`・`anthropic` の必要バージョン
- LINE公式アカウントをAPIで作成できるか（自動化の可否）

### 13.1 v0.3 追加分の未確認事項

- **LaunchAgent から `security find-generic-password` がダイアログ無しで動くか。** 参考記事（14章）は「対話なしのスクリプト（launchd/cron）には向かない」と述べている。`-T /usr/bin/security` を付けて登録し、ログイン済みセッションの LaunchAgent（LaunchDaemon ではない）で動かす想定だが、**Mac mini 実機でフェーズ2検証時に必ず確認する。** 動かない場合の代替: 初回のみ手動で「常に許可」を押す / `keyring` ライブラリに切り替える
- `launchctl bootstrap` / `kickstart` / `print` の引数形式は Mac mini 上で `man launchctl` を確認する（本仕様書では公式ページを参照できていない）
- `respx` による `anthropic` SDK のモック可否（SDK が httpx を使う前提。バージョンにより異なる可能性）
- Claude Code の `permissions.deny` の Bash パターンは**前方一致的なパターン照合**であり、`python -c "open('data/..')"` のような迂回は防げない。そのため PreToolUse フックを併用しているが、フックも文字列照合なので完全ではない。**data/ の秘匿の最終防衛線は暗号化であり、Claude Code 側の除外は「うっかり読まない」ための補助**と位置づける
- 12.6 の `/usage` 概算額に使う料金は未確認のまま（13章冒頭の項目と同じ）。`config/pricing.toml` にオーナーが公式料金ページの値を転記する

### 13.2 v0.4 時点の未確認事項

- `claude-opus-4-8` が現在も API から呼べるか（deprecation ページ未確認）。本仕様では使わないので影響なし
- Anthropic API の 529 応答に `retry-after` が付くか。実装は 529 で retry-after を無視して指数バックオフしているが、付く場合は尊重した方がよい可能性がある
- 実機（Mac mini）未検証: LaunchAgent 経由の Keychain 初回アクセス許可（「常に許可」が必要）、`launchctl bootstrap/kickstart` の引数、Cloudflare Tunnel 疎通、LINE 実 API での 3 秒以内 Loading。いずれも Linux コンテナではスタブ化されている

### 13.3 v0.4 適用時に見つかった不整合（2026-08-26）

- ~~既定モデルが 0章 と実装で食い違っている~~ → **解決済み（2026-08-26）。** オーナーの判断で 0章どおり `claude-fable-5` を既定とし、実装の `DEFAULT_MODEL` を変更した。12.5 フェーズ1検証の「`model=claude-fable-5` が渡ること」も `tests/test_webhook.py::test_the_default_model_is_fable_5` として追加済み
- 12.5 フェーズ2検証4の「exit code 1」は 11章の「exit 0」と矛盾する。11章を採用した（11章の該当行を参照）
- **Fable 5 は組織のデータ保持設定が30日であることを要求する。** ゼロデータ保持（ZDR）の組織からのリクエストは `400 invalid_request_error` になる。既定を Fable 5 にした以上、実機で最初に確認すべき項目。弾かれる場合は `LINE_AI_MODEL=claude-opus-5` で既定を落とすか、組織設定を見直す
- Fable 5 は思考が常時オンで、難しい依頼では1回の応答が数分に達しうる。`LINE_AI_EFFORT` を既定の `low` から上げるときは、ローディング表示の打ち直し（15秒間隔）と reply token の期限に注意する

## 14. v0.3・v0.4 追加分の出典（2026-08-25 確認）

| 内容 | 出典 | 日付 |
|---|---|---|
| `.claudeignore` は公式ドキュメントに記載なし。`permissions.deny` の `Read(./path/**)` / `Edit(./path/**)` / `Bash(cmd *)` 構文。deny は allow より常に優先 | https://code.claude.com/docs/en/permissions.md | ページ内に更新日の記載なし |
| settings の優先順位（managed → CLI → settings.local.json → settings.json → ~/.claude/settings.json） | https://code.claude.com/docs/en/settings-reference.md | 同上 |
| CLAUDE.md の配置場所と `@file` インポート（最大4段） | https://code.claude.com/docs/en/memory.md | 同上 |
| スキル（`.claude/skills/<name>/SKILL.md`）の frontmatter: `disable-model-invocation`、`allowed-tools` 等。旧 `.claude/commands/` も同等に動作 | https://code.claude.com/docs/en/skills.md | 同上 |
| フック: `PreToolUse` の JSON 構造、stdin に `tool_input.command`、exit code 2 で拒否 | https://code.claude.com/docs/en/hooks.md | 同上 |
| ヘッドレス: `claude -p`、`--allowedTools`、`--output-format json`（result / session_id / usage / cost_usd） | https://code.claude.com/docs/en/headless.md | 同上 |
| Cloudflare Tunnel（名前付き）: `brew install cloudflare/cloudflare/cloudflared` → `tunnel login` → `tunnel create` → `~/.cloudflared/config.yml` → `tunnel route dns` → `tunnel run` | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/ | 同上 |
| Cloudflare Tunnel を macOS サービス化: `cloudflared service install`（ログイン時起動、`~/.cloudflared/` を使用）、ログは `/Library/Logs/com.cloudflare.cloudflared.{out,err}.log` | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/as-a-service/macos/ | 同上 |
| Quick Tunnel（開発用）: `cloudflared tunnel --url http://localhost:8080`。SLAなし、ランダムな trycloudflare.com サブドメイン、同時200リクエスト上限、SSE非対応 | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/ | 同上 |
| `security add-generic-password -s <service> -a <account> -w <pw>` / `find-generic-password -w`、`-T` で信頼アプリ指定、非対話実行の注意 | https://scriptingosx.com/2021/04/get-password-from-keychain-in-shell-scripts/ | 2021-04 |
| 現行モデルID一覧: `claude-fable-5` / `claude-opus-5` / `claude-sonnet-5` / `claude-haiku-4-5-20251001`。`claude-opus-4-8` は「Opus 4.8 以前をお使いの場合」の移行案内にのみ登場（v0.4追加） | https://platform.claude.com/docs/en/models/overview | ページ内に更新日の記載なし |
