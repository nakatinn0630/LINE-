# LINE Personal AI — Phase 1

LINEで送ったメッセージをClaudeに投げ、その返事をLINEに返すだけの最小構成。
Webhook受信 → 署名検証 → Claude呼び出し → reply、の往復ひとつが動く範囲まで。

> **前提についての注記**
> リポジトリに `line-personal-ai-spec.md` が見つからなかったため（`README.md` 以外の
> ファイルはコミットされておらず、他ブランチにもありません）、フェーズ1の範囲は
> 依頼文にあった「Webhook受信→Claude→replyの最小往復」の定義をそのまま採用しています。
> 仕様書が入ったら、その内容と突き合わせて差分を埋めてください。

## 構成

| ファイル | 役割 |
| --- | --- |
| `app/main.py` | FastAPIアプリ。`POST /webhook` と `GET /healthz` |
| `app/line_client.py` | LINE署名検証（`WebhookParser`）とreply送信 |
| `app/claude.py` | Anthropic Messages APIの薄いラッパ |
| `app/config.py` | 設定の読み込み |
| `app/keychain.py` | macOS Keychainからのシークレット読み出し |
| `tests/test_webhook.py` | 署名検証とreply呼び出しのテスト |

処理の流れ:

1. `POST /webhook` が `X-Line-Signature` を検証する。不正なら400を返し、何もしない。
2. テキストメッセージのイベントだけを拾い、**先に200を返す**。
   LINEはWebhookに素早い応答を期待するため、Claude呼び出しは
   `BackgroundTasks` に逃がしてレスポンス後に走らせる。
3. バックグラウンドでClaudeに投げ、返ってきたテキストをreply tokenで返信する。

フェーズ1は意図的にステートレスで、会話履歴は持たない。1メッセージが1会話。

## シークレットの置き場所

チャネルシークレット・チャネルアクセストークン・Anthropic APIキーは
**macOS Keychainにだけ**置く。`.env` などの平文ファイルには書かない
（`.gitignore` で `.env*` を弾いてある）。

登録は以下の3コマンド。`-w` に値を付けずに実行すると対話的に入力を求められるので、
シークレットがシェル履歴に残らない。

```bash
security add-generic-password -s line-personal-ai -a LINE_CHANNEL_SECRET -w
security add-generic-password -s line-personal-ai -a LINE_CHANNEL_ACCESS_TOKEN -w
security add-generic-password -s line-personal-ai -a ANTHROPIC_API_KEY -w
```

確認と更新:

```bash
# 登録済みか確認（-w を付けない限り値そのものは表示されない）
security find-generic-password -s line-personal-ai -a LINE_CHANNEL_SECRET

# 値を差し替える（-U で上書き）
security add-generic-password -U -s line-personal-ai -a ANTHROPIC_API_KEY -w

# 削除
security delete-generic-password -s line-personal-ai -a ANTHROPIC_API_KEY
```

値はプロセス起動時に一度だけ読まれる。差し替えたらアプリの再起動が要る。

同名の環境変数が設定されていれば、そちらがKeychainより優先される。これはCIと
非macOSホストのための逃げ道なので、開発マシンでは設定しないでおくこと
（設定するとKeychainが単一の情報源でなくなる）。

## ローカルでの起動

必要なもの: Python 3.12、[uv](https://docs.astral.sh/uv/)。

```bash
# 依存の取得（.venv を作って同期する）
uv sync --extra dev

# テスト
uv run pytest

# 起動（開発中は --reload 付きが楽）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

疎通確認:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

起動時のログに `Ready. model=... effort=...` が出れば、3つのシークレットは
読めている。読めていない場合は `SecretNotFoundError` が出て、登録用の
`security` コマンドがそのままメッセージに書かれる。

### 調整できる項目

シークレット以外は普通の環境変数。他ツールが定義する汎用的な `CLAUDE_*` と
ぶつからないよう、すべて `LINE_AI_` を前置してある。

| 変数 | 既定値 | 内容 |
| --- | --- | --- |
| `LINE_AI_MODEL` | `claude-opus-5` | 使うモデル |
| `LINE_AI_EFFORT` | `low` | 思考の深さ (`low`/`medium`/`high`/`xhigh`/`max`) |
| `LINE_AI_MAX_TOKENS` | `2000` | 応答の上限トークン |
| `LINE_AI_SYSTEM_PROMPT` | `app/config.py` 参照 | システムプロンプト |
| `LINE_AI_REFUSAL_FALLBACK` | `1` | 拒否時のサーバサイドfallback。`0` で無効 |
| `LINE_AI_KEYCHAIN_SERVICE` | `line-personal-ai` | Keychainのサービス名 |
| `LINE_AI_LOG_LEVEL` | `INFO` | ログレベル |

LINEのreply tokenは短命なので、既定は `effort=low` で応答速度側に振ってある。
返答の質を上げたいときは `high` に上げる代わりに、返信までの待ち時間が伸びる。

`LINE_AI_REFUSAL_FALLBACK` は既定で有効。安全性の判定でモデルが応答を断った場合に、
API側で別モデルに引き継いで返答を作る。アカウントでこのbetaが使えない場合は
1回目のリクエストで自動的に無効化され、以降は通常の呼び出しにフォールバックする
（ログに1行残る）。

## Cloudflare Tunnel の設定

LINEのWebhookはHTTPSの公開URLしか受け付けない。`cloudflared` でローカルの
8000番を外に出す。

### 準備

```bash
brew install cloudflared
```

### A. お試し用（Quick Tunnel）

アカウント登録もDNSも要らない。URLは起動ごとに変わる。

```bash
# 別ターミナルでアプリを起動しておく
uv run uvicorn app.main:app --port 8000

cloudflared tunnel --url http://localhost:8000
```

`https://<ランダムな文字列>.trycloudflare.com` が表示されるので、
これに `/webhook` を付けたものをLINEのWebhook URLに設定する。
再起動するたびにURLが変わるので、そのたびに設定し直す必要がある。

### B. 固定URL（Named Tunnel）

Cloudflareアカウントと、そこに登録済みのドメインが要る。URLが固定されるので
LINE側の設定を毎回書き換えずに済む。

```bash
# 1. Cloudflareにログイン（ブラウザが開く。証明書が ~/.cloudflared/cert.pem に入る）
cloudflared tunnel login

# 2. トンネルを作る（認証情報が ~/.cloudflared/<UUID>.json に入る）
cloudflared tunnel create line-personal-ai

# 3. DNSレコードを作る（例: line-ai.example.com）
cloudflared tunnel route dns line-personal-ai line-ai.example.com
```

`~/.cloudflared/config.yml` を作る:

```yaml
tunnel: line-personal-ai
credentials-file: /Users/<ユーザー名>/.cloudflared/<UUID>.json

ingress:
  - hostname: line-ai.example.com
    service: http://localhost:8000
  - service: http_status:404
```

`<UUID>` は `cloudflared tunnel list` で確認できる。起動:

```bash
cloudflared tunnel run line-personal-ai
```

常駐させたい場合は `sudo cloudflared service install` でlaunchdに登録する。

トンネルの認証情報（`cert.pem` と `<UUID>.json`）は `~/.cloudflared/` に置かれる。
リポジトリには入れないこと。

### LINE側の設定

[LINE Developersコンソール](https://developers.line.biz/console/) の
対象チャネル → Messaging API設定 で:

1. **Webhook URL** に `https://line-ai.example.com/webhook` を設定する
   （Quick Tunnelなら `https://<ランダム>.trycloudflare.com/webhook`）。
2. **検証** ボタンを押して成功することを確認する。
   アプリとトンネルの両方が起動している必要がある。
3. **Webhookの利用** をオンにする。
4. **応答メッセージ** をオフにする。オンのままだと定型の自動応答が混ざる。
5. **あいさつメッセージ** は好みで。

チャネルアクセストークン（長期）は同じ画面の下部で発行できる。
チャネルシークレットは「チャネル基本設定」タブにある。

設定できたら、そのチャネルを友だち追加して何か送る。
`Handling text message` → `Replied` の2行がログに出れば往復できている。

## 制約と、この先

- 会話履歴を持たない。前のメッセージを踏まえた返事はできない。
- テキストメッセージだけを扱う。スタンプ・画像・音声は無視する（ログには残る）。
- 応答はLINEの上限に合わせて4900文字で切る。
- reply tokenは1回きりで期限があるため、返信の送信に失敗しても再送はしない。
- `data/` ディレクトリはこのフェーズの対象外。コードからは触れておらず、
  `.gitignore` にも入れてある。
