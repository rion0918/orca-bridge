# Orca Bridge

[English](README.md)

macOS上のOrcaとCodex CLIをBearer認証付きHTTP APIから利用する実験的なローカルBridgeです。タスク受付、ローカル承認、実行状況・結果・ログ取得を提供します。MITで公開している実験的なソフトウェアです。

## 前提条件

- macOS、Python 3.10以上（開発環境は3.13）。
- OrcaアプリとCLI、ログイン済みのCodex CLIが必須です。CLIの設定・権限形式に互換性があることを確認してください。
- トンネル利用時のみngrokまたはcloudflaredが必要です。ngrokはPATHまたは.tools/ngrok、cloudflaredは.tools/cloudflaredを使用。バイナリは同梱しません。
- Antigravityは現在無効です。CLIや残量があっても実行できません。
- requirements.lockの固定版を導入できる環境が必要です。最低Pythonバージョン表記だけで全依存の互換性を保証しません。

## 導入

このプロジェクト内で実行します。python3は3.10以上の解釈系を指定してください。

~~~sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
~~~

初期状態では.env、実行DB、ログ、接続状態、トンネルバイナリは含まれません。sandbox/は.gitkeepのみの作業場所です。初回起動時に.envとstate/が必要に応じて作成されます。connector/connection.example.jsonとconnector/openapi.jsonは予約ドメインを使った配布例です。

pyproject.tomlはorca-bridge 0.6.0を定義し、requirements.lockを依存定義として参照します。通常は上記の依存導入後、このディレクトリからスクリプトを実行してください。wheelへのconnector/・sandbox/の同梱と実行配置は未対応です。

## 設定

.envはKEY=VALUEを1行ずつ記載する形式です。シェル展開、export、引用符の除去はしません。空行と先頭が#の行を無視します。環境変数が.envより優先されます。

.envが存在しなければ、serve.pyのload()はBRIDGE_TOKENとBRIDGE_READ_TOKENをそれぞれランダム生成し、権限0600で保存します。既存.envがグループ・他ユーザーからアクセス可能なら起動を拒否します。既存ファイルの不足キーは補完しません。テンプレートから作る場合はトークンを必ず生成し直してください。手動編集にはumask 077を推奨します。

| 変数 | 既定値・用途 |
| --- | --- |
| BRIDGE_TOKEN | 未設定。初回load時に生成する読み書き用トークン。空では認証不可 |
| BRIDGE_READ_TOKEN | 未設定。初回load時に生成する読み取り専用トークン |
| BRIDGE_AUTORUN_SANDBOX | 未設定（無効）。1で開発を自動実行。名前に反してsandbox以外も対象 |
| ORCA_CLI_COMMAND | 未設定。Orcaの実行ファイル名またはパス。引数は含めない |
| ORCA_DEV_REPO_ROOT | 未設定。設定がある場合、ORCA_CLI_COMMAND未指定時はorca-devを使用 |
| ORCA_ENVIRONMENT | Orca呼び出し時に除去。--environment localを固定 |
| ORCA_PAIRING_CODE | Orca呼び出し時に除去。リモート接続に使用しない |
| HOME | OSから継承。新規プロジェクトのHOME/MuseProjects、CLI認証情報参照等に影響 |
| PATH | OSから継承。Orca/Codex等の探索 |
| USER | OSから継承。子CLIへ渡す基本環境 |
| LANG | OSから継承。子CLIへ渡す基本環境 |
| TMPDIR | OSから継承。子CLIへ渡す基本環境 |

設定テンプレートは.env.exampleです。

上表はコードが明示的に扱う変数です。OrcaにはBRIDGE_*、ORCA_ENVIRONMENT、ORCA_PAIRING_CODE以外の親環境が渡ります。Codex子プロセスにはHOME/PATH/USER/LANG/TMPDIRのみ渡し、生成コードのシェル環境は別途制限します。トークンを引数・共有ログに貼らないでください。

## 起動・停止と使い方

~~~sh
.venv/bin/python serve.py
~~~

127.0.0.1:8765で待ち受けます。フォアグラウンドではCtrl-Cで停止できます。別のローカル端末から記録済みプロセスを停止する場合は次を使います。

~~~sh
.venv/bin/python process_control.py
# トンネルだけ停止
.venv/bin/python process_control.py --tunnel-only
~~~

PIDに加え開始時刻・コマンドの一致を確認し、不一致なら停止を拒否します。Bridgeの停止だけで全タスクの終了は保証しません。必要なタスクは先にcancelしてください。

client.pyは.envから読み書きトークンを読み込みます。

~~~sh
.venv/bin/python client.py status
.venv/bin/python client.py agents
# request.txtに依頼を書く
.venv/bin/python client.py submit --project sandbox --prompt-file request.txt
.venv/bin/python client.py get TASK_ID
.venv/bin/python client.py logs TASK_ID
.venv/bin/python client.py result TASK_ID
.venv/bin/python client.py cancel TASK_ID
.venv/bin/python client.py retry TASK_ID
~~~

既定ではpending_approvalです。次のローカル操作で内容を確認し承認します。bridge.pyは.envを自動ロードしません。承認時にも残量を再確認します。

~~~sh
.venv/bin/python bridge.py list
.venv/bin/python bridge.py show TASK_ID
.venv/bin/python bridge.py approve TASK_ID
~~~

承認HTTP APIはありません。承認後、OrcaのターミナルがBridgeと同じPython解釈系でrunner.pyを起動します。BRIDGE_AUTORUN_SANDBOX=1でserve.pyを起動するとqueuedから自動実行します。sandboxに限らず全ての許可プロジェクトが対象です。通常フォルダの直接編集も含むことを理解したうえで設定してください。

トンネルは外部公開を伴う任意操作です。利用者が公開を承認した場合のみ実行します。通常のローカル利用には不要です。

~~~sh
.venv/bin/python tunnel.py --publish-approved --provider ngrok
# または --provider cloudflare
~~~

HTTPS経路の未認証401と認証済み応答を検査し、新経路の確認後に記録済み旧トンネルを停止します。ngrokのHTTP inspectionは無効です。実接続のbase_url・openapi_url・default_headersはstate/connector-endpoint.json、プロバイダ・PID等はstate/tunnel.jsonへ保存します。配布JSONと任意のmuse-prompt.txtは変更しません。プロンプト欠落はINFOログに記録してスキップします。クライアントには状態ファイルのURLとヘッダーを設定し、Bearerトークンは別の安全な保管場所から渡してください。

## API概要

全ルートでAuthorization: Bearerが必要です。読み取りトークンはGET、読み書きトークンは全操作を許可します。

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | /status | Orca稼働状態 |
| GET | /agents | CLIの有無・有効状態、ローカルログイン状態 |
| GET | /models | 固定プロファイルと残量・実行可否 |
| GET | /policy | 自動実行・同時実行の方針 |
| GET | /projects | 一覧と状態。絶対パスを含む |
| POST | /projects | 名前を指定しHOME/MuseProjects配下に新規作成 |
| POST | /projects/{project}/environment-example | ENVIRONMENT.exampleを.env.exampleへ改名（本文なし） |
| GET | /projects/{project}/environment-example | テンプレート全文取得。Bridgeトークン値は伏字 |
| PUT | /projects/{project}/environment-example | contentでテンプレート全文置換。応答は伏字 |
| POST | /projects/{project}/commit | pathsとmessageで指定ファイルのみローカルコミット。pushなし |
| POST | /projects/{project}/cleanup | 指定パスの安全な削除・dry_run |
| POST | /projects/{project}/python-environment | オフラインで.venvを準備（本文なし、テスト実行なし） |
| POST | /tasks | タスク受付。Idempotency-Key対応 |
| GET | /tasks/{id} | 状態取得 |
| GET | /tasks/{id}/result | 結果取得 |
| GET | /tasks/{id}/logs | ログ末尾取得 |
| POST | /tasks/{id}/cancel | 取消要求 |
| POST | /tasks/{id}/retry | 確定した失敗等の再試行 |
| GET | /openapi.json | 認証付き動的仕様。同梱仕様の18操作からは除外 |

タスクはprompt、project（既定sandbox）、timeout_seconds（10〜900、既定300）、operation（既定development）、execution_profile、execution_reasonを受け付けます。未知フィールドは禁止です。deployment/spendingは409で拒否します。プロンプトは最大8000文字、空白のみは拒否。HTTP本文上限32768バイト、待ち行列上限100件、実行同時数は1です。

追加のプロジェクト操作も既存のプロジェクトIDとパス解決を使います。任意のプロジェクトパスは指定できません。ファイル操作のpathsはプロジェクト相対パスで、範囲外、シンボリックリンク、.git・.aws・.codex、.env系、PEMは422で拒否します。.env.exampleのみテンプレート専用操作で扱い、.envは扱いません。欠落ファイルは404、状態やファイル操作の競合は409です。

environment-exampleのPOSTはENVIRONMENT.exampleが必要で、既存の.env.exampleは上書きしません。GETとPUTはUTF-8の全文を扱い、改行を維持します。PUTの本文は必須のcontent文字列です（未作成なら作成）。応答ではBRIDGE_TOKENとBRIDGE_READ_TOKENの代入値を常に<redacted>に置換し、保存内容は指定された全文のままです。

commitの本文は必須のpaths（1件以上の文字列）とmessage（空白のみ不可）です。既存の通常ファイルだけを対象とし、Git作業ツリー直下・user.nameとuser.emailの設定・ステージ済み変更がないことをステージ前に確認します。設定参照失敗も409で停止し、本人情報を補いません。パスはGitのリテラル指定で渡し、フックと署名を無効化し、指定ファイルだけをコミットします。応答はproject、commit（ハッシュ）、files（重複除去後の指定件数）、published: falseです。失敗後のインデックスは自動復旧せず、ローカル確認が必要です。

cleanupは必須のpathsと任意のrecursive・dry_run・empty_treeを受け付け、真偽値の既定はすべてfalseです。全対象を削除前に検査し、dry_runでも同じ安全検査を行います。recursive=trueでディレクトリを削除できるのは__pycache__、.pytest_cache、.mypy_cache、.ruff_cacheのみで、それ以外は422です。非再帰の非空ディレクトリは409です。empty_tree=trueはファイルを含まない空ディレクトリのツリーだけを対象にし、ファイルがあれば409で削除しません。通常の空ツリーはrecursiveを指定せずempty_treeだけで削除できます。応答はproject、paths、dry_runです。

python-environmentは全プロジェクトの実行中・状態不明タスクがあれば409で待機を促します。準備中は既存の実行状態トランザクションを保持し、タスク承認との競合を防ぎます。.venv/bin/pythonとpyvenv.cfgがあれば再利用し、なければBridgeと同じ解釈系の標準venvで作成します。解釈系同梱のensurepip資材のみを使い、PIP_NO_INDEX=1を指定します。プロジェクト依存のダウンロード・追加インストールは行いません。応答はproject、python（.venv/bin/pythonの絶対パス）、offline: true、tests_executed: falseです。

## プロジェクト種別とモデル

既定sandboxは実在とシンボリックリンクを確認し、実行可能なディレクトリはready、それ以外はblockedです。Orca登録先は/projectsのIDで指定します。任意のパスは渡せません。

- Gitは元の作業状態がクリーンでcheckoutフックに問題がないことを確認し、setupをスキップした専用Orca作業コピーを作ります。Gitの共有メタデータもあり、作業コピーだけでOS隔離を保証するものではありません。
- 通常フォルダは登録先を直接編集します。専用コピーは作りません。
- 新規プロジェクトは名前を検証し、HOME/MuseProjects配下でGit初期化してOrcaへ登録します。既存パスは上書きしません。

仕様判断が必要なタスクは、結果の `handoff.outcome` に `needs_decision` を返せます。同じプロジェクトで `continues_task` に元のタスクIDを指定すると、相談履歴を引き継いで続行します。

モデルは任意指定・自動発見ではなく固定5プロファイルです。

| プロファイル | モデル | effort | 実行 |
| --- | --- | --- | --- |
| sol_low（既定） | gpt-6.1-sol | low | Codex |
| sol_high | gpt-6.1-sol | high | Codex |
| sol_max | gpt-6.1-sol | max | Codex |
| hydrogen_high | gemini-4-hydrogen-high | high | Antigravity・無効 |
| argon_high | gemini-4-argon-high | high | Antigravity・無効 |

残量は60秒キャッシュし、枯渇・不明なら実行を止めます。Codexの残量は契約枠で、モデル固有とは限りません。追加クレジット購入・有料枠へのフォールバックはありません。/agentsはローカルのcodex login statusを確認し、モデル要求を送りません。通常実行と残量照会には各CLI・サービスへの接続が必要です。

## セキュリティと隔離の限界

トークンを定数時間比較し読み取りと読み書きを分けます。ただしトークン単位のプロジェクト制限はありません。読み書きトークンの所持者は登録プロジェクト全体に依頼でき、読み取りトークンでも/projectsの絶対パス等を取得できます。HTTPから任意コマンドや実行承認を指定できません。

runnerは固定Codexコマンドに、プロジェクト範囲の書き込み、外部ファイル・.env系・PEM・.aws・一時領域の拒否、生成コードのネットワーク禁止、権限拡張の承認不可を設定します。ユーザー設定・ルール・アプリ・Web検索を無効化し、実行対象直下に.codexがあれば承認を拒否します。子CLIにはBridgeトークン等を継承しません。

OS隔離はCodex CLIの設定解釈とバージョンに依存します。実機の読み書き・通信拒否検証は今後の課題です。設定文字列の検査だけで安全性を保証しません。Codex自身のモデル接続と生成コードの通信禁止は区別されます。プロンプトによる禁止も単独の強制境界ではありません。機密を含まない専用プロジェクトで評価してください。

state/にはDB・監査・実行ログを保存します。DB・ログファイルは0600、状態ディレクトリは作成時0700です。結果・ログの伏字処理は完全な秘密検出ではありません。監査は操作・状態中心ですが、DBや実行ログには依頼・出力が残り得ます。.gitignoreはアクセス制御ではありません。ログ保持期限、自動削除、削除APIはありません。

## 対応状況

| 機能 | 状況 |
| --- | --- |
| 認証、読み取り権限、ローカル承認 | 対応 |
| 開発の自動実行 | 明示設定で対応。全許可プロジェクトが対象 |
| キャンセル、タイムアウト | 対応。外部プロセス状態による限界あり |
| 再試行 | failed/timed_out/cancelledに限定 |
| Git専用コピー、フォルダ、新規作成 | 対応 |
| ngrok/cloudflaredの経路検証・状態保存 | 対応 |
| 残量照会・固定モデル選択 | 対応。不明・枯渇時は停止 |
| Antigravity実行 | 無効。安全性監査未完了 |
| 状態不明からの自動復旧 | 未対応。launch_unknown等は後続実行を止め、ローカル照合が必要 |
| デプロイ・購入・支出 | operationで拒否。実行経路なし |
| トークンごとのプロジェクト制限 | 未対応 |
| ログ保持期限・削除機能 | 未対応 |
| CLI互換性・実機でのOS隔離保証 | 未検証 |

## テスト

~~~sh
.venv/bin/python -B -m pytest -p no:cacheprovider tests
~~~

元の55ケースに回帰ケースと6操作の契約テストを追加しています。通常テストはモックとフィクスチャを使います。test_sandbox.pyは実際のCodex/macOS隔離を使い、モデル要求はしませんがループバック通信を試みます。ネットワーク操作禁止の環境では実行せず、制約を報告してください。

~~~sh
.venv/bin/python -B -m pytest -p no:cacheprovider tests --ignore=tests/test_sandbox.py
~~~

依存なしの追加操作確認は次で実行できます。実装関数をASTから抽出して検証し、Git・venv・テンプレートIO・削除システムコールはモックします。HTTP・Pydantic・実削除の検証はpytest側に残ります。

~~~sh
python3 -B tests/check_project_operations.py
~~~

同梱OpenAPIはアプリから生成した18操作の仕様です。ディスカバリーや接続には英語版READMEも参照してください。

部分検証はスイート合格や実機隔離確認と同一ではありません。今回の検証結果・不足項目はVALIDATION.mdを参照してください。

## ライセンス

MIT Licenseです。全文は[LICENSE](LICENSE)を参照してください。
