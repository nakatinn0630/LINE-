# LINE Personal AI

LINEで送ったメッセージとファイルをClaudeに渡し、返事をLINEに返す個人用アシスタント。
資料と会話履歴はすべてローカルに暗号化して保存する。

仕様は `line-personal-ai-spec.md`（v0.4）が正。判断の経緯はその11章に日付付きで残してある。

> **未決の項目がひとつある: 既定モデル。**
> 仕様書0章は `claude-fable-5` を既定としているが、実装は `claude-opus-5` を既定に
> している（`app/config.py` の `DEFAULT_MODEL`）。`/model` でどちらにも切り替えられる
> ので機能上は困らないが、**単価が2倍違う**（Fable 5 は $10/$50、Opus 5 は $5/$25）。
> どちらを既定にするか決めて、0章か `DEFAULT_MODEL` のどちらかを直すこと。
> 詳細は仕様書 13.3。

## 目次

- [構成](#構成)
- [シークレットの置き場所](#シークレットの置き場所)
- [ローカルでの起動](#ローカルでの起動)
- [launchd での常駐](#launchd-での常駐)
- [Cloudflare Tunnel の設定](#cloudflare-tunnel-の設定)
- [リリースとロールバック](#リリースとロールバック)
- [動作確認の手順](#動作確認の手順)
- [チャットで使えるコマンド](#チャットで使えるコマンド)
- [設計上の判断と、仕様からの逸脱](#設計上の判断と仕様からの逸脱)
- [制約](#制約)

## 構成

| ファイル | 役割 |
| --- | --- |
| `app/main.py` | FastAPI。`POST /webhook` と `GET /healthz` |
| `app/serve.py` | プロセス入口。起動前チェック（プリフライト）つき |
| `app/assistant.py` | メッセージ処理の本体。文脈組み立て → Claude → 暗号化保存 |
| `app/claude.py` | Claude呼び出し（応答・要約・画像説明・履歴圧縮） |
| `app/retry.py` | 429 / 529 のリトライ方針 |
| `app/loading.py` | ローディング表示とその延長 |
| `app/queues.py` | ユーザーIDごとの直列キュー |
| `app/crypto.py` | マスター鍵 → HKDF → AES-256-GCM |
| `app/vault.py` | `data/<userId>/<uuid>.enc` の読み書き |
| `app/store.py` | 会話履歴・資料索引・設定（暗号化 sqlite3） |
| `app/context.py` | 資料の関連度判定とsystem prompt組み立て |
| `app/extract.py` | pypdf / python-docx によるテキスト抽出 |
| `app/commands.py` | `/model` などのチャットコマンド |
| `app/keychain.py` | Keychainからのシークレット読み出し |
| `app/tools.py` | `genkey` / `check` |
| `scripts/fake_anthropic.py` | 429/529 の動作確認用スタブサーバ |
| `scripts/hooks/block_data_access.sh` | Claude Code の Bash ツールを `data/` から締め出す PreToolUse フック |

### メッセージが処理される流れ

1. `POST /webhook` が `X-Line-Signature` を検証する。不正なら400を返し、何もしない。
2. **ローディング表示を先に出す**（`POST /v2/bot/chat/loading/start`、`loadingSeconds=20`）。
   キューに積む前に呼ぶので、直前のメッセージを処理中でもすぐ表示される。
3. ジョブを **ユーザーIDごとの直列キュー** に積み、200を返す。
   同じ人からの2通が並行に走ることはなく、届いた順に処理される。
4. キューのワーカーが処理する。処理中はローディング表示を15秒ごとに打ち直すので、
   APIの上限である60秒を超える応答でも表示が途切れない。
5. テキストなら文脈（要約＋直近履歴＋関連資料）を組んでClaudeに投げ、
   ファイル・画像ならメモリ上に取得して要約し、いずれも暗号化して保存してから返信する。

## シークレットの置き場所

4つのシークレットを **macOS Keychainにだけ** 置く。`.env` などの平文ファイルには
書かない（`.gitignore` で `.env*` を弾いてある）。

| Keychainのアカウント名 | 中身 |
| --- | --- |
| `LINE_CHANNEL_SECRET` | チャネルシークレット（署名検証用） |
| `LINE_CHANNEL_ACCESS_TOKEN` | チャネルアクセストークン（長期） |
| `ANTHROPIC_API_KEY` | Anthropic APIキー |
| `MASTER_KEY` | 32バイトのマスター鍵（base64）。`data/` 配下の暗号化に使う |

まずマスター鍵を作る:

```bash
make genkey
# 例: 8Jx2... （32バイトのbase64。これを次のコマンドで貼り付ける）
```

4つとも登録する。`-w` に値を付けずに実行すると対話的に入力を求められるので、
シークレットがシェル履歴に残らない。

```bash
security add-generic-password -s line-personal-ai -a LINE_CHANNEL_SECRET -w
security add-generic-password -s line-personal-ai -a LINE_CHANNEL_ACCESS_TOKEN -w
security add-generic-password -s line-personal-ai -a ANTHROPIC_API_KEY -w
security add-generic-password -s line-personal-ai -a MASTER_KEY -w
```

登録できたか確認する（値そのものは表示されない）:

```bash
make check
# OK: all secrets resolved from Keychain service 'line-personal-ai'
```

更新と削除:

```bash
security add-generic-password -U -s line-personal-ai -a ANTHROPIC_API_KEY -w   # 上書き
security delete-generic-password -s line-personal-ai -a ANTHROPIC_API_KEY      # 削除
```

値はプロセス起動時に一度だけ読まれる。差し替えたら `make restart` が要る。

> **マスター鍵を失うと `data/` は復号できない。** 鍵の派生元はこれひとつで、
> どこにもバックアップされない。Keychainごと失う可能性を考えるなら、
> `make genkey` で作った値を別途安全な場所（パスワードマネージャなど）に控えておくこと。
> 鍵を作り直すと、既存の `data/` は読めないまま残る。

> **launchd から初回起動したとき、Keychainがアクセス許可を求めることがある。**
> ダイアログが出たら「常に許可」を選ぶ。選ばないと、以後の自動起動で
> シークレットを読めずに停止する（そのときの挙動は下の「プリフライト」を参照）。

同名の環境変数が設定されていれば、そちらがKeychainより優先される。これはCIと
非macOSホストのための逃げ道なので、開発マシンでは設定しないでおくこと。

## ローカルでの起動

必要なもの: Python 3.12、[uv](https://docs.astral.sh/uv/)。

```bash
make sync        # 依存の取得（uv sync --extra dev）
make test        # テスト
make lint        # ruff
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

疎通確認:

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

起動時のログに `Ready. model=... effort=... data=...` が出れば、4つのシークレットは
読めている。

### 調整できる項目

シークレット以外は普通の環境変数。他ツールが定義する汎用的な `CLAUDE_*` と
ぶつからないよう、すべて `LINE_AI_` を前置してある。

| 変数 | 既定値 | 内容 |
| --- | --- | --- |
| `LINE_AI_MODEL` | `claude-opus-5` | 既定のモデル |
| `LINE_AI_EFFORT` | `low` | 思考の深さ (`low`/`medium`/`high`/`xhigh`/`max`) |
| `LINE_AI_MAX_TOKENS` | `2000` | 応答の上限トークン |
| `LINE_AI_SYSTEM_PROMPT` | `app/config.py` 参照 | 土台となるsystem prompt |
| `LINE_AI_REFUSAL_FALLBACK` | `1` | 拒否時のサーバサイドfallback。`0` で無効 |
| `LINE_AI_DATA_DIR` | `data` | 暗号化データの保存先 |
| `LINE_AI_HISTORY_EXCHANGES` | `8` | system promptに渡す直近の往復数 |
| `LINE_AI_COMPACT_AFTER` | `40` | 履歴がこの件数を超えたら古い分を要約に畳む |
| `LINE_AI_DOCUMENT_LIMIT` | `3` | 1回の応答に差し込む資料の最大数 |
| `LINE_AI_DOCUMENT_THRESHOLD` | `0.08` | 資料を関連ありとみなす下限スコア |
| `LINE_AI_DOCUMENT_CHARS` | `12000` | 資料1件あたりの差し込み上限文字数 |
| `LINE_AI_MAX_DOWNLOAD_BYTES` | `20971520` | 受け取るファイルの上限（20MB） |
| `LINE_AI_LOADING_SECONDS` | `20` | ローディング表示の1回あたりの秒数（5〜60） |
| `LINE_AI_NOTIFY_WAIT_THRESHOLD` | `20` | この秒数を超える待ちはpushで知らせる |
| `LINE_AI_QUEUE_MAX_PENDING` | `8` | 1ユーザーあたりの待ち行列の長さ |
| `LINE_AI_HOST` / `LINE_AI_PORT` | `127.0.0.1` / `8000` | 待ち受け先 |
| `LINE_AI_LOG_LEVEL` | `INFO` | ログレベル |

LINEのreply tokenは短命なので、既定は `effort=low` で応答速度側に振ってある。
返答の質を上げたいときは `high` に上げる代わりに、返信までの待ち時間が伸びる。

## launchd での常駐

ログイン時に自動起動し、異常終了したら再起動する。1台につき1度だけ:

```bash
make install-agent
```

`~/Library/LaunchAgents/com.line-personal-ai.agent.plist` を生成して読み込む。
ログは `~/Library/Logs/line-personal-ai/agent.log`。

`make install-agent` が中でやっているのは次の3つ。手で打つならこの順:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.line-personal-ai.agent.plist
launchctl kickstart -k gui/$(id -u)/com.line-personal-ai.agent
launchctl print gui/$(id -u)/com.line-personal-ai.agent | grep state
```

> **必ず LaunchAgent（`~/Library/LaunchAgents`）にすること。**
> LaunchDaemon（`/Library/LaunchDaemons`）はログインセッションの外で動くため
> **ログインキーチェーンを開けない**。`security find-generic-password` が失敗し、
> 4つのシークレットを一切読めなくなる。plist の先頭にも同じ注意を書いてある。

```bash
make status    # 状態を見る
make logs      # ログを追う
make restart   # 再起動
make stop      # 停止（ログインしても起動しなくなる）
```

### プリフライトと、再起動しない停止

`app/serve.py` はサーバを立てる前にシークレットを確認する。**マスター鍵がKeychainに
無ければ、起動せずログを残して停止する。** そのとき終了コードは **0** を返す。

plistの `KeepAlive` は `SuccessfulExit = false`、つまり
**「正常終了でないときだけ再起動する」**。だから終了コード0は launchd にとって
「もう放っておいてよい」の合図になり、再起動ループに入らない。逆に本当に落ちた
ときは非ゼロで終わるので、ちゃんと再起動される。

鍵が無いときのログはこうなる:

```
ERROR app.serve: Cannot start: Secret 'MASTER_KEY' not found. Add it to the login Keychain with:
  security add-generic-password -s line-personal-ai -a MASTER_KEY -w
ERROR app.serve: Not starting, and not asking launchd to retry - the Keychain entry has to be added by hand first.
```

> **鍵を登録しても、自動では起動しない。手動での再起動が要る。**
> launchd は「もう放っておいてよい」と判断して監視をやめているので、鍵を入れた
> だけでは何も起きない。次のコマンドで明示的に起動する:
>
> ```bash
> launchctl kickstart -k gui/$(id -u)/com.line-personal-ai.agent
> ```
>
> `make restart` は同じコマンドの別名。起動したかどうかは
> `curl http://127.0.0.1:8000/healthz` と `make logs` で確かめる。

## Cloudflare Tunnel の設定

LINEのWebhookはHTTPSの公開URLしか受け付けない。`cloudflared` でローカルの
8000番を外に出す。

```bash
brew install cloudflared
```

### A. お試し用（Quick Tunnel）

アカウント登録もDNSも要らない。URLは起動ごとに変わる。

```bash
cloudflared tunnel --url http://localhost:8000
```

表示された `https://<ランダム>.trycloudflare.com` に `/webhook` を付けて
LINEのWebhook URLに設定する。再起動のたびに設定し直しになる。

### B. 固定URL（Named Tunnel）

Cloudflareアカウントと登録済みのドメインが要る。`make deploy` の疎通確認は
固定URLを前提にしているので、常用するならこちら。

```bash
cloudflared tunnel login                                       # ~/.cloudflared/cert.pem
cloudflared tunnel create line-personal-ai                     # ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns line-personal-ai line-ai.example.com
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: line-personal-ai
credentials-file: /Users/<ユーザー名>/.cloudflared/<UUID>.json

ingress:
  - hostname: line-ai.example.com
    service: http://localhost:8000
  - service: http_status:404
```

`<UUID>` は `cloudflared tunnel list` で確認できる。

```bash
make tunnel                        # 手元で動かす
sudo cloudflared service install   # launchdに登録して常駐させる
```

トンネルの認証情報（`cert.pem` と `<UUID>.json`）は `~/.cloudflared/` に置かれる。
リポジトリには入れないこと。

### 公開URLをdeployに教える

`deploy/deploy.env` を作る（gitignore済み。秘密は入らない）:

```bash
LINE_AI_PUBLIC_URL=https://line-ai.example.com
```

### LINE側の設定

[LINE Developersコンソール](https://developers.line.biz/console/) の
対象チャネル → Messaging API設定 で:

1. **Webhook URL** に `https://line-ai.example.com/webhook` を設定する。
2. **検証** を押して成功することを確認する（アプリとトンネルの両方が起動していること）。
3. **Webhookの利用** をオンにする。
4. **応答メッセージ** をオフにする。オンのままだと定型の自動応答が混ざる。

チャネルアクセストークン（長期）は同じ画面の下部で発行できる。
チャネルシークレットは「チャネル基本設定」タブにある。

## リリースとロールバック

### リリース

```bash
make deploy
```

1本のスクリプト（`scripts/deploy.sh`）が4段階を順に実行し、途中で失敗したらそこで止まる:

1. **依存更新** — `uv sync --extra dev`
2. **テスト** — `uv run pytest -q`。落ちたらここで終わり、再起動はしない
3. **launchd再起動** — `launchctl kickstart -k`、そのあとローカルの `/healthz` を最大30秒待つ
4. **トンネル疎通確認** — `$LINE_AI_PUBLIC_URL/healthz` を外側から叩き、
   `{"status":"ok"}` が返ることを確認する

最後にデプロイしたコミット、公開URL、ログの場所を表示する。

手順を踏むリリース:

```bash
git pull                      # 変更を取り込む
make deploy                   # 上の4段階
git log --oneline -1          # いま動いているコミットを控えておく
```

LINEアプリから1通送って、実際に返事が来ることを確かめる。

### ロールバック

```bash
make rollback REF=<戻したいコミット>
```

`scripts/rollback.sh` が指定のコミットを detached HEAD でチェックアウトし、
そのまま `deploy.sh` を実行する（依存更新 → テスト → 再起動 → 疎通確認）。
戻し先の候補は `git log --oneline -10` で確認できる。引数なしで実行すると
直近10件を表示して止まる。

```bash
# 例: ひとつ前に戻す
make rollback REF=$(git rev-parse --short HEAD~1)

# 確認したら最新に戻る
git checkout claude/line-personal-ai-phase1-po9c6a
make deploy
```

**ロールバックしても `data/` は巻き戻らない。** 暗号化データはgit管理外なので、
コードだけが戻る。保存形式を変える変更をロールバックする場合は、
その前後で `data/` の互換性があるかを確認すること。

### リリース確認チェックリスト

`make deploy` のあと、上から順に確認する（仕様書 12.5 フェーズ4検証）。

- [ ] **壊れたコードが止まること** — わざと構文エラーを入れて `make deploy` を実行し、
      テスト失敗の時点で止まって `launchctl` まで進まないこと。旧バージョンが動き続ける
      ```bash
      echo "def broken(:" >> app/config.py
      make deploy                      # 「2/4 Running tests」で失敗するはず
      launchctl print gui/$(id -u)/com.line-personal-ai.agent | grep -E 'state|pid'
      git checkout app/config.py       # 後始末
      ```
- [ ] **正常時に起動時刻が更新されること**
      ```bash
      make deploy
      launchctl print gui/$(id -u)/com.line-personal-ai.agent | grep -E 'state|pid'
      curl -s https://line-ai.example.com/health     # {"status":"ok"}
      ```
- [ ] **ロールバックできること**
      ```bash
      make rollback REF=<前のタグまたはコミット>
      curl -s https://line-ai.example.com/health
      ```
- [ ] **`/model` が効くこと** — LINEで `/model opus` を送り、次の返答のログを見る
      ```bash
      make logs | grep "Claude応答を生成しました"
      # -> 要求モデル=claude-opus-5 応答モデル=claude-opus-5
      ```
      `/model fable` を送って `要求モデル=claude-fable-5` に戻ることも確認する
- [ ] **LINEから1通送って返事が来ること**

### 手が使えないときの最小手順

`make` が使えない状況（Makefileごと壊れているなど）でも、これだけで復旧できる:

```bash
cd ~/path/to/LINE-
git checkout <戻したいコミット>
uv sync
launchctl kickstart -k gui/$(id -u)/com.line-personal-ai.agent
curl https://line-ai.example.com/healthz
```

## 動作確認の手順

### 自動でまとめて確認する

```bash
make test
```

以下はすべてテストとして自動化してある:

- 署名検証（正・不正・ヘッダ欠落）
- ローディング表示がwebhook受信直後に呼ばれること。Claudeを待たせても**1秒未満**で出ること
- ローディングの打ち直しが**15秒間隔**で、応答完了後に止まること
- 429で `retry-after` の秒数だけ待って再送すること（`retry-after: 3` で**実測3〜4秒**）
- 待機20秒超のとき push が**1回だけ**送られること
- 529が `retry-after` 無しでも指数バックオフすること
- 同一ユーザーの直列化と、別ユーザーの並行実行
- ユーザーAの派生鍵でBのファイルを開くと `InvalidTag` になること（AADの検証を含む）
- PDFを取り込んでも `data/` 配下に平文が残らないこと（`strings` 実行と `$TMPDIR` 検査を含む）
- マスター鍵が無いときプリフライトが終了コード0で止まり、ログに `master-key not found` が残ること
- plistが `RunAtLoad` と `KeepAlive.SuccessfulExit=false` を持つこと
- `make deploy` がテスト失敗時に `launchctl` へ進まないこと
- フックが `data/` へのコマンドを拒否し、許可リストの3つだけ通すこと
- `/health` と `/healthz` が200を返すこと

以下は実機でしか確かめられないので、手順を分けて書く。

---

### P0-1. 3秒以内にLoading表示

**確認すること**: LINEでメッセージを送ってから3秒以内に「入力中」の表示が出る。

```bash
# 1. ログを追う端末を用意する
make logs
```

2. LINEアプリから何か1通送る。**送信した瞬間から、トーク画面に「…」が出るまでを数える。**
3. ログにこの順で出ていれば、経路は正しい:

```
INFO app.main: ...                     # webhook受信
INFO app.assistant: Handling ...       # キューのワーカーが処理を開始
```

4. 応答が20秒を超える場合、`LINE_AI_LOG_LEVEL=DEBUG` にすると延長も見える:

```
DEBUG app.loading: Refreshing the loading animation for U....
```

**わざと遅くして延長を見る**（60秒を超えても表示が消えないことの確認）:

```bash
LINE_AI_EFFORT=max LINE_AI_MAX_TOKENS=8000 make restart
```

長い調べものを投げると1分以上かかる。その間ずっと「入力中」が出続け、
15秒ごとに `Refreshing the loading animation` がログに出る。確認できたら元に戻す。

**表示が出ないときに見るところ**:

- ログに `Could not start the loading animation` が出ていないか
  （出ていればアクセストークンかチャネル設定の問題）
- LINE Developersコンソールで「Webhookの利用」がオンか
- そのユーザーが公式アカウントを友だち追加しているか（未追加だとローディングは出せない）

---

### P0-2. 429後に応答が返る

本番のAPIで429をわざと起こすのは難しいので、スタブサーバに差し替えて確認する。

```bash
# 端末1: スタブを起動する（1回だけ429、retry-after は25秒）
uv run python scripts/fake_anthropic.py --rate-limit 1 --retry-after 25
```

```bash
# 端末2: アプリをスタブに向けて起動する（launchdは止めておく）
make stop
ANTHROPIC_BASE_URL=http://127.0.0.1:8931 uv run python -m app.serve
```

LINEから1通送って、以下を順に確認する:

1. **「入力中」がすぐ出る**
2. **「25秒後に返答します。」というpushが届く**
   （25秒は閾値の20秒を超えているため。`--retry-after 10` にすると届かないことも確認できる）
3. **約25秒後にスタブからの応答が届く** — 「スタブからの応答です（2回目のリクエスト）。」
4. アプリのログ:

```
WARNING app.retry: Rate limited (attempt 1/3). Waiting 25s per retry-after.
```

5. スタブのログ:

```
fake-anthropic: request 1 -> 429 (retry-after: 25)
fake-anthropic: request 2 -> 200
```

**529も同じ要領で確認できる**（`retry-after` を無視して指数バックオフすること）:

```bash
uv run python scripts/fake_anthropic.py --overload 2
```

スタブは529に `retry-after: 999` を付けて返す。ログの待ち時間が999秒ではなく
数秒であれば、429と529が別の経路になっていることの確認になる。
pushも飛ばない（待ち時間の見積もりを伝えられるのは429だけ）。

確認が終わったら:

```bash
make start   # 本来のAPIに戻して常駐に戻す
```

---

### P0-3. 再起動後に自動起動

```bash
# 1. いま登録されていることを確認する
make status
# state = running, および LINE_AI_PORT などが見えること

# 2. Macを再起動して、ログインする
sudo shutdown -r now
```

ログイン後、**アプリには触らずに**:

```bash
# 3. プロセスが上がっているか
launchctl print gui/$(id -u)/com.line-personal-ai.agent | grep -E 'state|pid'

# 4. 応答するか
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}

# 5. 起動ログに再起動後の時刻が入っているか
tail -20 ~/Library/Logs/line-personal-ai/agent.log
```

6. LINEから1通送って返事が来れば、トンネル込みで通っている
   （`cloudflared` も `sudo cloudflared service install` で常駐させてあること）。

**異常終了からの復帰も確認する**:

```bash
PID=$(launchctl print gui/$(id -u)/com.line-personal-ai.agent | awk '/pid =/{print $3}')
kill -9 "$PID"
sleep 15                                   # ThrottleInterval は10秒
curl http://127.0.0.1:8000/healthz         # 別PIDで復帰しているはず
```

**鍵が無いときに再起動ループしないことも確認する**:

```bash
security delete-generic-password -s line-personal-ai -a MASTER_KEY
make restart
sleep 30
tail -5 ~/Library/Logs/line-personal-ai/agent.log
#   -> "Cannot start: Secret 'MASTER_KEY' not found..." が1〜2回だけ出て止まる
#      （何十回も繰り返されていたら、plistの KeepAlive を確認する）

# 鍵を戻す。鍵を入れただけでは起動しないので、明示的に立ち上げ直す
security add-generic-password -s line-personal-ai -a MASTER_KEY -w
launchctl kickstart -k gui/$(id -u)/com.line-personal-ai.agent
curl http://127.0.0.1:8000/healthz
```

---

### `data/` の中身が読めないことの確認

保存されているのは、資料の本体・抽出テキスト・要約・会話履歴のすべて。
どれも平文では残らない。

```bash
# 1. 何か特徴のある内容を送る
#    LINEから「私の口座番号は1234567です」と送信し、
#    テキストファイルやPDFも1つ送っておく

# 2. 何が書かれたか見る（ファイル名はUUIDのみ）
ls -la data/*/
#   Uxxxxxxxx/3f9c1e2a-....enc
#   Uxxxxxxxx/8b7d0f11-....enc
#   Uxxxxxxxx/store.db

# 3. strings で全部を舐める
strings -a data/*/*.enc data/*/store.db | less

# 4. 送った内容が出てこないことを確認する
strings -a data/*/*.enc data/*/store.db | grep -i "1234567"
strings -a data/*/*.enc data/*/store.db | grep -i "口座"
#   -> どちらも何も出ない（grepの終了コードは1）
```

まとめて確認するなら:

```bash
for word in 1234567 口座 $(送ったファイル名); do
  if strings -a data/*/* 2>/dev/null | grep -qi "$word"; then
    echo "NG: $word が読めてしまう"
  else
    echo "OK: $word は出てこない"
  fi
done
```

日本語は `strings` の既定ではUTF-8として拾われないことがあるので、
生バイト列でも確認しておくと確実:

```bash
grep -r --binary-files=text "口座" data/ ; echo "exit=$?"   # exit=1 なら見つかっていない
```

### PDFを使った実機手順（仕様書 12.5 フェーズ3検証）

1. LINEから、中身の分かっているPDFを送る（例: 本文に「四半期の売上」と書かれたもの）
2. 取り込みが終わったら、`data/` 配下を丸ごと走査する:

   ```bash
   find data -type f | xargs strings -a | grep "四半期"
   echo "exit=$?"        # exit=1（何も出ない）が正常
   ```

3. LINEで「さっき送った資料の要点は？」と聞き、**PDFの内容に沿った返事が来ること**を確認する

2で何も出ないのに3で内容が返る、という組み合わせが取れて初めて
「ディスク上は暗号化されているが、会話では使えている」ことの確認になる。

**出てくるのが正常なもの**（内容ではないので問題ない）:

- `store.db` の中の sqlite のスキーマ文字列 — `CREATE TABLE messages`, `role`, `content` など
- 会話の役割を表す `user` / `assistant` の文字列
- ディレクトリ名になっているLINEのユーザーID（`U` で始まる33文字）
- タイムスタンプとUUID

つまり **「いつ、何件やり取りしたか」は分かるが、「何を話したか」は分からない**。
メタデータまで隠したい場合は、ディレクトリ名をユーザーIDのハッシュに変えるなどの
追加対応が要る（現状は仕様どおり `data/<userId>/` にしてある）。

**鍵が無ければ読めないことの確認**:

```bash
uv run python - <<'EOF'
from app.crypto import Crypto, decode_master_key, generate_master_key
from app.vault import Vault
from pathlib import Path
import sys

user = sys.argv[1] if len(sys.argv) > 1 else input("userId: ").strip()
wrong = Vault(Path("data"), Crypto(decode_master_key(generate_master_key())))
for record_id in wrong.record_ids(user):
    try:
        wrong.read(user, record_id)
        print("NG: 別の鍵で読めてしまった", record_id)
    except Exception as exc:
        print("OK:", record_id, "->", type(exc).__name__)
EOF
```

### Claude Code から `data/` を読ませない設定

2層で塞いである。どちらも `.claude/settings.json` に入っている。

**1. permissions.deny（ツール単位）**

```
Read(./data/**)   Edit(./data/**)   Write(./data/**)
Bash(cat ./data/**)  Bash(strings ./data/**)  Bash(sqlite3 ./data/**)  Bash(cp ./data/**)
```

**2. PreToolUse フック（シェルコマンド単位）**

`scripts/hooks/block_data_access.sh` が、Bashツールに渡る**コマンド文字列そのもの**を
見て判定する。deny 規則は前方一致なので `cd data && cat *` のような書き方を
表現できないが、フックは実際の文字列を読むので塞げる。

`data` をパスの一区切りとして含むコマンドは、次の2つを除いてすべて終了コード2で拒否する:

```bash
ls data/
du -sh data/
```

`metadata/`・`bigdata/`・`data_export/` のような紛らわしい名前は誤爆しない。
拒否されたときは、理由と許可されている2コマンドがClaudeに返る。

| コマンド | 結果 |
| --- | --- |
| `ls data/` | 許可 |
| `du -sh data/` | 許可 |
| `cat data/x.enc` | 拒否 |
| `strings data/x.enc` | 拒否 |
| `cp data/x.enc /tmp/` | 拒否 |
| `cd data && cat *` | 拒否 |
| `tar cf - data/ \| nc host 1234` | 拒否 |
| `ls -la data/` | 拒否（許可リストにない書き方） |

> **このフックが効くのは Claude Code の Bash ツールだけ。**
> 上の「`data/` の中身が読めないことの確認」に書いた `strings` や `ls -la` は、
> あなた自身がターミナルで打つぶんには何の制限も受けない。フックは
> 「Claudeに読ませない」ためのもので、「あなたが読めなくする」ためのものではない。

なお `.claudeignore` は使っていない。Claude Code にその仕組みは無く、
置いても読まれないため削除した。除外は上の2層で行う。

## チャットで使えるコマンド

LINEのトークにそのまま打つ。Claudeには渡らず、会話履歴にも残らない。

| コマンド | 動作 |
| --- | --- |
| `/model` | いま使っているモデルを表示する |
| `/model opus-5` | 応答の速いモデルに切り替える |
| `/model fable-5` | じっくり考えるモデルに切り替える |
| `/help` | ヘルプを表示する |

切り替えは **ユーザー単位** で、`data/<userId>/store.db` に保存される。
`opus` / `fable` / `claude-opus-5` のような書き方も受け付ける。
一覧にないモデル名を指定すると拒否され、既定のままになる。

## 設計上の判断と、仕様からの逸脱

いずれも実装しながら判断が必要だった点。**すべて仕様書11章「決定の経緯」に記録済み**
（この6点は v0.4 の時点で反映されている）。以降に足した判断も同じ表に日付付きで並ぶので、
経緯を追うなら11章を先に読むとよい。

**1. 直近の履歴は `messages` で渡している（system promptではなく）**
仕様は「直近N往復＋要約をsystem promptに渡す」だが、直近の往復は Messages API の
`messages` 配列に本来の役割つきで渡し、**要約と参考資料をsystem promptに入れて**いる。
モデルに渡る情報は同じで、会話の順序と話者がAPIの想定どおりに伝わるぶん結果は良くなる。
要約と資料は仕様どおりsystem promptに入っている。

**2. 画像は Claude のvisionで要約している**
仕様の抽出経路（pypdf / python-docx）は画像に使えない。要約が無いと索引に載らず
「関連資料を選ぶ」対象にもならないので、画像はvisionで説明文を作って索引にしている。
本体は他と同じく暗号化して保存する。

**3. 資料の選定はモデルに問い合わせず、文字bigramの重なりで採点している**
「要約索引から関連資料を選ぶ」ために毎回Claudeを1往復させると、返信までの時間が
その分伸びる。日本語は空白で単語が切れないので、単語ではなく文字bigramの重なりで
採点している（日本語・英語のどちらでも動く）。閾値と件数は
`LINE_AI_DOCUMENT_THRESHOLD` / `LINE_AI_DOCUMENT_LIMIT` で変えられる。

**4. マスター鍵が無いとき「終了コード0」で止める**
launchd の `KeepAlive.SuccessfulExit=false` は「正常終了でないときだけ再起動」する。
つまり **再起動させないためには正常終了しなければならない**。直感に反するので
`app/serve.py` にコメントで書いてある。同じ理由から、他のシークレットが欠けている
ときも同様に扱っている（再起動しても直らないため）。

**5. 設定値（モデルの選択）だけは暗号化していない**
`store.db` の `settings` テーブルは平文。モデル名は利用者が書いた内容ではなく設定なので、
暗号化の対象から外している。**このテーブルにユーザーの入力を入れないこと。**

**6. Anthropic SDK の自動リトライを切っている**
SDKは既定で429や5xxを2回まで再送する。それを残すと自前の方針と二重になり、
`retry-after` どおりの待ちにもならないので `max_retries=0` にしてある
（`tests/test_claude_integration.py` で、1リクエストしか飛ばないことを確認している）。

## 制約

- テキスト、ファイル、画像のメッセージだけを扱う。
  スタンプ・音声・動画・位置情報は無視する（ログには残る）。
- 受け取るファイルは20MBまで。超えるとその旨を返す。
- テキストを抽出できるのは PDF・DOCX・プレーンテキスト系のみ。
  それ以外は本体を暗号化して保存し、テキスト抽出はしない。
- 画像から文字を読むのは Claude のvisionによる。専用のOCRほど正確ではない。
- 応答はLINEの上限に合わせて4900文字で切る。
- reply tokenは1回きりで期限があるため、返信の送信に失敗しても再送はしない。
- 会話履歴と資料はユーザーIDごとに完全に分かれていて、互いに参照できない。
- グループ・ルームからのメッセージは扱わない（個人用のため）。
- 1ユーザーあたりの待ち行列は8件まで。あふれたら「あとで送って」と返す。
