@line-personal-ai-spec.md

# LINE Personal AI

LINE経由の個人用アシスタント。仕様は上の `@line-personal-ai-spec.md` が正。
コードと仕様が食い違ったら仕様を確認し、実装を変えるか仕様を直すかを判断する。

> **注意: 現時点で `line-personal-ai-spec.md` はリポジトリに存在しない。**
> 上の参照は先に置いてあるだけで、ファイルが追加されるまで解決されない。
> 仕様に基づく判断が必要になったら、まずファイルの有無を確認すること。

## このリポジトリで守ること

**`data/` は読まない。** 暗号化された個人データが入っている。
`.claude/settings.json` の `permissions.deny` と、`scripts/hooks/block_data_access.sh`
（PreToolUse フック）で二重に塞いである。許可されているのは `ls data/` と
`du -sh data/` の2つだけ。中身を見る必要があると思ったら、まず人に確認する。

**シークレットは macOS Keychain にしかない。** `.env` などの平文ファイルを作らない。
`LINE_CHANNEL_SECRET` / `LINE_CHANNEL_ACCESS_TOKEN` / `ANTHROPIC_API_KEY` /
`MASTER_KEY` の4つ。詳細は README の「シークレットの置き場所」。

**環境変数は `LINE_AI_` を前置する。** 汎用的な `CLAUDE_*` は他ツールが使っていて
衝突する（実際に `CLAUDE_EFFORT` で踏んだ）。

**モデルIDは推測しない。** 現行は `claude-opus-5` と `claude-fable-5`。
日付サフィックスは付けない。

## よく使うコマンド

```bash
make sync      # 依存の取得
make test      # uv run pytest
make check     # Keychainのシークレットが読めるか確認
make deploy    # 依存更新 -> テスト -> launchd再起動 -> トンネル疎通
uv run ruff check .
```

## 構成の要点

- `app/main.py` — webhook。署名検証 → ローディング表示 → ユーザー別キューに投入 → 200
- `app/assistant.py` — 処理本体。文脈組み立て → Claude → 暗号化保存
- `app/retry.py` — 429は `retry-after` どおり待つ / 529は指数バックオフ。両者は別勘定
- `app/crypto.py` — マスター鍵 → HKDF → ユーザー別鍵 → AES-256-GCM
- `app/serve.py` — 起動前チェック。鍵が無ければ **exit 0** で止まる
  （launchd の `KeepAlive.SuccessfulExit=false` に対して「再起動するな」を意味する）

Anthropic SDK の自動リトライは `max_retries=0` で切ってある。`app/retry.py` が
唯一のリトライ経路であることを壊さないこと。
