# Orca Bridge

[English](README.md) · [技術リファレンス](docs/reference.ja.md) · [MIT License](LICENSE)

**Museから、MacのOrcaとCodexに開発を頼めるようにする、自分で運用するBridgeです。** スマホやPCから依頼し、進捗・ログ・結果をMuseで確認できます。

`Muse → HTTPS → Orca Bridge → Orca → Codex`

Codexが仕様の質問を返したら、Museが回答を送り、同じプロジェクトで作業を続けられます。BridgeはHTTP APIなので、Muse以外のクライアントからも利用できます。

## 用意するもの

- macOS、Python 3.10以上、OrcaとそのCLI、ログイン済みのCodex CLI。開発環境はPython 3.13です。
- Museから使う場合は、個人用Custom Connectorが使えるMuseアカウントと、Museから到達できるHTTPS接続。個人用コネクターは、初回に自分のMuseアカウントで作ります。

## Macで起動する

```sh
git clone https://github.com/rion0918/orca-bridge.git
cd orca-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python serve.py
```

Bridgeは `127.0.0.1:8765` で起動します。初回は `.env` に読み書き用と読み取り専用のトークンが生成されます。別のターミナルで `.venv/bin/python client.py status` を実行し、Orcaに接続できることを確認してください。

Museから接続するには、設定済みのngrokをPATHに置き、別のターミナルで次を実行します。これはMac上の認証付きAPIをHTTPSで公開するコマンドです。

```sh
.venv/bin/python tunnel.py --publish-approved --provider ngrok
```

## Museと接続する

初回だけMuseのアカウントに個人用Custom Connectorを作ります。利用には、MuseのアカウントでCustom Connectorが使えることが必要です。前の手順で起動したら、`state/connector-endpoint.json` の `base_url`、`openapi_url`、`default_headers` を確認します。

次の文をMuseのチャットに貼り、URLと `default_headers` を自分の値に置き換えてください。

```text
このHTTPS APIに接続する個人用Custom Connector「Orca Bridge」を作ってください。
接続先：https://YOUR-BRIDGE-HOST.example
仕様：/openapi.json
認証：Authorization: Bearer。トークンはMuseの安全な入力画面で入力させてください。
既定のHTTPヘッダー：ここにdefault_headersだけを貼る
接続後にGET /statusとGET /agentsを呼び、実際の応答を教えてください。
まだ開発タスクは作成しないでください。
```

Museの安全な入力画面が出たら、Macで `.venv/bin/python copy_token.py` を実行してコピーした読み書き用トークンを入力します。トークンはチャット本文に貼りません。両方の接続確認が通ったら、「Orca Bridgeでsandboxにhello.txtを作って、読み直した内容を報告して」と依頼できます。既定では、返されたタスクIDをMacで `.venv/bin/python bridge.py approve TASK_ID` に渡すと実行が始まります。

トンネルのURLが変わった場合はMuse側の接続先を更新し、トークンが新しいホストに紐付いていることを確認してください。401なら認証情報を確認し、トンネル側のブロック画面が返る場合は先に接続経路を確認します。

## 運用の範囲

- 初期設定では、実行するタスクをMacで承認します。自動実行を有効にすると、既定のsandbox以外の対象プロジェクトにも適用されます。
- 読み書き用トークンで、Bridgeが扱うすべての対象プロジェクトを操作できます。最初は機密情報のないテスト用プロジェクトで試してください。
- このOSS版は開発・テスト・相談の往復を扱います。GitHub公開、デプロイ、課金は実行しません。
- リモート利用中はMac、Orca、Bridge、トンネルを動かしておきます。トンネルを止めるには `.venv/bin/python process_control.py --tunnel-only` を実行します。

APIは[OpenAPI](connector/openapi.json)、トンネルの代替手段や設定は[技術リファレンス](docs/reference.ja.md)を参照してください。

## コントリビュート

不具合を報告する際は、macOS・Python・Orca・Codexのバージョンと、秘密情報を含まない再現手順を添えてください。

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -B -m pytest -p no:cacheprovider tests
```

ライセンスは[MIT](LICENSE)です。
