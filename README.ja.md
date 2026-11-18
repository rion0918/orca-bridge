# Orca Bridge

[English](README.md) · [対応OS](#対応os) · [Macで起動](#macでbridgeを起動する) · [Museと接続](#museと接続する)

Orca Bridgeを使うと、自分のマシン上のOrcaに、Museから開発を頼めます。Museは進捗と結果を確認し、開発エージェントから質問が返ったら回答を送って作業を続けられます。同じHTTP APIを他のクライアントから使うこともできます。

`Muse → HTTPS → Orca Bridge → Orca → 開発エージェント`

## 対応OS

| OS | 離れた場所のBridgeをMuseから操作 | Bridgeをそのマシンで実行 |
| --- | --- | --- |
| macOS | [MuseのWeb版](https://muse.ai/) | 検証済み。下の手順で起動できます。 |
| Windows | [MuseのWeb版](https://muse.ai/) | Windowsネイティブ、WSLともに未検証。 |
| Linux | [MuseのWeb版](https://muse.ai/) | 未検証。 |
| iOS / Android | Museのモバイルアプリ | Bridgeは別のマシンで動かします。 |

[Orca本体はmacOS・Windows・Linuxに対応](https://orca.dev/docs/install)しています。LinuxのCLI名は `orca-ide` です。このBridgeはタスク取消にUnixのプロセスグループを使い、トークンのコピーにはMacのクリップボードを使います。他のOSでBridgeを提供するには、取消やファイル・ネットワークの隔離をその環境で検証する必要があります。

## MacでBridgeを起動する

Git、Python 3.10以上（検証は3.13）、[OrcaとそのCLI](https://orca.dev/docs/install)、[利用する開発エージェント](#開発エージェントを選ぶ)のCLIを用意してください。先にOrcaを起動します。

```sh
git clone https://github.com/rion0918/orca-bridge.git
cd orca-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python serve.py
```

Bridgeは `127.0.0.1:8765` で待ち受け、初回起動時に非公開の `.env` に読み書き用と読み取り専用のトークンを生成します。別のターミナルで `.venv/bin/python client.py status` を実行し、Orcaに接続できるか確認します。

## Museと接続する

個人用Custom Connectorを使えるMuseアカウントが必要です。設定はアカウントごとに初回だけ行い、同じコネクターをスマホとPCから使えます。

1. **MuseからBridgeにつながるようにする。** ngrokを導入・設定したMacで、Bridgeとは別のターミナルから次を実行します。認証付きAPIがHTTPS経由で利用できるようになります。

   ```sh
   .venv/bin/python tunnel.py --publish-approved --provider ngrok
   ```

2. **Museでコネクターを作る。** 次の文をMuseに貼り、Macの `state/connector-endpoint.json` の値でプレースホルダーを置き換えます。

   ```text
   個人用Custom Connector「Orca Bridge」を作ってください。

   接続先：<base_url>
   OpenAPI仕様：<openapi_url>
   既定のHTTPヘッダー：<default_headers>

   Authorization: Bearer で認証してください。トークンはMuseの安全な入力画面で入力します。
   接続後にGET /statusとGET /agentsを呼び、実際の応答を教えてください。
   まだ開発タスクは作成しないでください。
   次回以降は作業に応じてwork_kindを選び、実行プロファイルはBridgeに任せてください。
   ```

3. **トークンを入力する。** Macで `.venv/bin/python copy_token.py` を実行し、読み書き用トークンをMuseの安全な入力画面に貼ります。チャット本文には貼りません。普段の操作端末が別でも、この入力だけ同じアカウントのMuseをMacで開いて行えます。
4. **タスクを試す。** 両方の接続確認が通ったら、「Orca Bridgeで `sandbox` に `hello.txt` を作り、読み直した内容を教えて」と依頼します。初期設定では、返されたIDをMacで `.venv/bin/python bridge.py approve TASK_ID` に渡すと実行され、Museが結果を読めます。

外から使う間はMac、Orca、Bridge、トンネルを起動しておきます。URLが変わったらMuseの接続先とトークンの紐付けを確認してください。Bridgeが401を返す場合はトークン、トンネル事業者のHTMLエラーなら接続経路を確認します。

リモート接続を止めるには、Macで `.venv/bin/python process_control.py --tunnel-only` を実行します。

## 開発エージェントを選ぶ

Macの非公開 `.env` に `BRIDGE_AGENTS` を設定して、Bridgeを再起動します。既定は `codex` です。Claudeだけ使うなら `claude`、先にCodexを試すなら `codex,claude` を指定します。Bridgeは設定順に利用可能な候補を選びます。Museなどのクライアントは `work_kind` に `implementation`（実装。既定）、`design`（設計）、`recovery`（難しい修正）のいずれかを渡せます。既存の `execution_profile` 指定も使えます。認証状態は `GET /agents`、選択肢は `GET /models` で確認できます。

| エージェント | 前提と制限 |
| --- | --- |
| Codex | ログイン済みCLIと利用できる契約枠が必要です。隔離した環境でテストを実行できます。 |
| Claude Code（試験対応） | CLI 2.1.319以上とClaudeのサブスクリプションでのログインが必要です。有効にする前にClaude側で追加使用を無効にしてください。残量をBridgeで確認できないため、実行ごとにMacで承認します。プロジェクト内のファイル操作に対応し、シェルコマンドやテストは実行しません。[Claude CLIのオプション](https://code.claude.com/docs/en/cli-reference) |
| Antigravity | 隔離と取消の検証が完了するまで無効です。 |

## APIと利用範囲

[OpenAPI仕様](connector/openapi.json)、[接続例](connector/connection.example.json)、[設定テンプレート](.env.example)、[技術リファレンス](docs/reference.ja.md)を参照してください。動いているBridgeの仕様は、認証付きの `GET /openapi.json` でも取得できます。

- 初期設定ではMacでタスクを承認します。`BRIDGE_AUTORUN_SANDBOX=1` は実行可能なCodexタスクの自動実行を有効にし、既定のsandbox以外の対象プロジェクトにも適用されます。
- 読み書き用トークンは設定済みの全対象プロジェクトを操作できます。機密情報のないテスト用プロジェクトから始めてください。依頼とログはホストに残り、自動削除されません。
- このOSS版では、GitHub公開、本番デプロイ、課金は実行しません。

## 貢献とライセンス

[貢献ガイド](CONTRIBUTING.md) · [行動規範](CODE_OF_CONDUCT.md) · [セキュリティポリシー](SECURITY.md) · [MIT License](LICENSE)
