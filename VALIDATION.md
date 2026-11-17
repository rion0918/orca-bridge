# ローカル整備・検証記録

## 実施範囲

このプロジェクトのみを変更。外部通信、モデル呼び出し、トンネル起動、公開・push・デプロイ・購入、元Bridgeや他プロジェクトの利用・変更、プラグイン利用、.codex設定変更は実施していません。.envは開いていません。認証・隔離・承認・残量判定の仕様は変更していません。

## 変更ファイル

| ファイル | 変更 |
| --- | --- |
| .gitignore | .env.*、!.env.example、state/connector-endpoint.jsonを追記。既存state/除外も維持 |
| bridge.py | 起動Pythonをsys.executableへ変更。既定プロジェクトのready判定に既存project_pathの実在・安全性確認を使用 |
| tunnel.py | 配布JSONとプロンプトへの書き戻しを廃止。接続情報はstate/connector-endpoint.jsonへ保存。既存state/tunnel.jsonも維持。プロンプト欠落はINFOログでスキップ |
| tests/test_tunnel.py | 実在形式の固定ホストを予約ドメインへ置換。配布物不変・状態保存・プロンプト欠落を検証 |
| tests/test_bridge.py | 空白を含む解釈系パスでもsys.executableを使う回帰テスト |
| tests/test_projects.py | 既定sandboxの欠落・実ディレクトリ・シンボリックリンクの3ケース |
| sandbox/.gitkeep | 既定作業ディレクトリのプレースホルダ |
| README.md | 日本語の利用者向け手順、設定全変数、API、固定5モデル、機能と隔離の限界、未定ライセンス |
| ENVIRONMENT.example | 要求された.env.exampleの内容をレビューできる代替テンプレート |
| pyproject.toml | orca-bridge 0.5.0、Python >=3.10、requirements.lockから依存参照。ライセンス欄なし |
| VALIDATION.md | 本記録 |

キャッシュ内容も削除しました。connector/connection.example.json、connector/openapi.json自体は変更していません。

## 検証結果

| 検証 | 結果 |
| --- | --- |
| 依存なしの回帰確認（修正前） | 6項目中1成功・5失敗。配布物更新、プロンプト欠落、欠落/リンクsandboxのready誤表示、固定Pythonを再現 |
| 同回帰確認（修正後） | 6成功・0失敗 |
| tests/test_quota.pyの直接実行 | 4成功・0失敗 |
| Pythonソースのast.parse | 16ファイル成功・0失敗 |
| 配布JSONのjson.loads | 2ファイル成功・0失敗 |
| APIルートと同梱OpenAPIの静的照合 | 12操作が完全一致、差分0。認証付き/openapi.jsonの取得ルート自体は同梱仕様から除外 |
| テストケース数の静的集計 | 元55ケース + 追加5ケース = 60ケース |
| pytestスイート | 起動失敗1回（No module named pytest）。ケース実行0件。ケース単位の成功・失敗は未判定 |
| 配布対象の再走査 | 25ファイル。個人絶対パス0件、実トンネルURL0件。トークン形式候補1件はENVIRONMENT.exampleのREPLACE_WITH_DIFFERENT_RANDOM_READ_TOKENというダミー |
| キャッシュ内容 | 残存ファイル0件。空ディレクトリ削除は権限拒否 |

回帰の直接確認は、実ソースをASTで取り出し、save_endpoint、list_projectsとproject_pathを標準ライブラリ・ローカルフィクスチャで実行しました。起動コマンドはapprove内の式を評価し、空白を含む解釈系パスをshlexで往復確認しました。FastAPIのHTTP経由の検証・approve全体の検証ではありません。pytest向けの回帰テストは追加済みですが、この環境では実行できていません。残量テストはtests/test_quota.pyをimportして4つのtest関数を直接実行し、通信するCLI関数は呼んでいません。

再走査は配布対象テキストを対象に、個人ホーム絶対パス、ngrok/trycloudflareの実URL形式、長いBearer/sk-/Bridgeトークン代入を検出しました。.git、.venv、.tools、state、キャッシュ、一時検証領域、.env*、PEMは除外し、秘密ファイルを開いていません。正規表現による走査であり、あらゆる秘密の不存在を保証するものではありません。

## 実行環境と未完了項目

- PATH探索では/usr/bin/python3（3.9.6）とpip3のみ確認。python3.10〜3.14、brew、pyenv、uvは見つかりませんでした。Homebrew、Python Framework、pyenvの標準配置の実行ファイル候補にも利用可能な結果はありません。プロジェクト外の環境・設定・キャッシュは読み取っていません。この制約内でPython 3.10以上を確保できなかったという結果であり、マシン全域に存在しないとの断定ではありません。
- pytest、FastAPI、httpx、uvicorn、pydanticはすべて未導入。プロジェクト内にオフライン導入用wheel・tar.gzはありませんでした。ネットワークや他プロジェクトの依存を使わずには揃えられず、.venvは作成していません。
- pytest起動は「/usr/bin/python3 -B -m pytest -p no:cacheprovider tests」で試行。実行前のimport段階で失敗しました。テストによる外部通信は起きていません。
- .env.exampleへの新規書き込みはPermissionError（Operation not permitted）で拒否されました。.env*を拒否する実行環境の制限です。回避や権限昇格はせず、内容をENVIRONMENT.exampleに用意しました。要求されたファイル名での作成は未完了です。
- キャッシュ内ファイルの削除は成功しましたが、rmdirはOperation not permittedでした。.pytest_cache/v/cache、.pytest_cache/v、.pytest_cache、__pycache__、tests/__pycache__の空ディレクトリは残っています。rm -rfも実行ツールが禁止しているため実行されていません。一時検証領域.verification/は36ファイルを削除し、残存ファイル0件を確認しました。同じ制約で空ディレクトリ48個が残っています（Gitの配布対象には含まれません）。
- Python/gitランチャーはXcodeの一時キャッシュ作成拒否を表示しましたが、Pythonの直接検証は上記の通り実行できました。git statusはグローバル設定参照を無効にして確認し、この候補コピーのソースは未追跡でした。
- OS隔離の実機テストは未実施です。依存不足に加え、test_sandbox.pyはローカル通信を試すため今回のネットワーク禁止条件では実行しません。CLI互換性、実モデル実行、トンネル接続、パッケージビルド・インストールも未検証です。
- ライセンスは未定のままです。配布・公開可能との判定はしていません。

## 2026-11-17 ライセンス追加・初回コミット確認

- 所有者の決定に従い、LICENSEに標準MIT License全文を追加。著作権行はCopyright (c) 2026 Rion Hashimoto。
- README.mdのライセンス欄のみをMIT LicenseとLICENSEへの参照に変更。pyproject.tomlの[project]にlicense = {text = "MIT"}のみを追加。以前の未定・ライセンス欄なしの記述は変更前の記録です。
- 変更前にLICENSE欠落のAssertionError（RED）を確認。変更後はLICENSE全文一致、READMEの他の内容不変、pyproject.tomlの追加以外不変、本記録の末尾追記を確認し、4項目成功（GREEN）。
- 初回コミットは未実施。通常のgit config user.name / user.emailはグローバル設定へのアクセス拒否で確認できず、グローバル・システム設定参照を無効化したローカル設定確認でも両項目は未設定（終了コード1）。名前・メールは作成せず、git add -Aも実行していません。
- git status --shortでは配布候補ファイルは未追跡。git ls-filesの出力は空で、追跡ファイルは0件。グローバル設定参照を無効化した確認のみ成功。Git/PythonランチャーのXcode一時キャッシュ作成拒否も発生しました。
- apply_patchは実行ヘルパーへのアクセス拒否で失敗したため、プロジェクト内ファイルをPythonで更新しました。ネットワーク操作、外部プロジェクトの変更、設定の書き換えは行っていません。

## 2026-11-17 残件仕上げの実施結果

- テンプレート: mv ENVIRONMENT.example .env.example は Operation not permitted（終了コード1）。再試行・迂回せず、ENVIRONMENT.exampleとREADME.mdの既存記載は維持しました。
- 空ディレクトリ: 空であることを確認してrmdirする処理は、シェルのhere-document用一時ファイル作成が Operation not permitted で失敗（終了コード1）。rmdir自体は未実行、削除可否は未確認。再試行・迂回はしていません。
- Git本人情報: git config user.name と git config user.email は各1回確認しましたが、グローバル設定参照が Operation not permitted（終了コード128）。実効値の未設定とアクセス拒否は区別し、代替本人情報は設定・使用していません。グローバル・ローカルとも設定変更なし。
- Git初回コミット: 通常のGit操作が設定参照拒否で停止するため、迂回せずgit add -A・ステージ一覧確認・コミットは未実施。コミットハッシュはありません。git statusとgit logの確認も設定参照拒否で失敗。git ls-filesの全一覧は未取得です。
- 検証: python3 -B -m pytest -p no:cacheprovider tests --ignore=tests/test_sandbox.py は No module named pytest で起動失敗（ケース実行0件）。ネットワークを試みるtest_sandbox.pyは対象外としました。
- 変更範囲: 本記録の末尾追記のみ。ネットワーク操作、公開、push、リモート追加、外部プロジェクト変更、秘密ファイル参照、プラグイン利用、.codex設定変更はしていません。


## 2026-11-17 テンプレート案内更新・再検証

- README.md: 設定節の1文を「設定テンプレートは.env.exampleです。」へ更新。旧記載の検出（RED）後、置換結果と全文を比較して他の箇所が不変であることを確認（GREEN）。
- .env.example: Bridge APIでリネーム済みとの前提に従って案内を更新。ただし実行環境が.env*の読み取りを明示的に禁止しているため、内容確認・先頭コメント編集は未実施。書き込みも試行せず、迂回なし。.envも開いていません。
- pytest: TMPDIRをプロジェクト内.tmpに指定して、python3 -B -m pytest -p no:cacheprovider tests --ignore=tests/test_sandbox.py を実行。No module named pytestで起動失敗、ケース実行0件。Pythonは3.9.6（要求>=3.10未達）。依存導入・ネットワーク操作なし。
- 依存なし直接検証: tests/test_quota.pyの4関数が4成功・0失敗。実ソースからASTで抽出したtunnel.pyのsave_endpointは2ケース成功（配布ファイル不変・接続情報とトンネル状態保存、プロンプト欠落時ログ）。bridge.pyのproject_path/list_projectsは3ケース成功（欠落blocked、実ディレクトリready、リンクblocked）。合計9成功・0失敗。外部CLI・HTTP経由・実機隔離の検証ではありません。
- 静的検証: Python全16ファイルのast.parse成功、JSON全2ファイルのjson.loads成功。APIルートと同梱OpenAPIは12操作一致、差分0（/openapi.json自体を除外）。
- 配布対象走査: 読み取り可能な25ファイルで個人ホーム絶対パス0件、実ngrok/trycloudflare URL 0件、長いBearer/sk-/BRIDGEトークン代入候補0件。.env*・PEM・状態・キャッシュ・一時領域・Git管理領域を除外。.env.exampleは走査未実施。正規表現検査のため秘密の完全な不存在を保証するものではありません。
- 空ディレクトリ削除: 対象の空ディレクトリへのrmdir結果は下記。拒否した箇所は再試行・迂回なし。非空ディレクトリは維持。
  - 削除成功: 0個。削除拒否: 35個。非空のため維持: 18個。
  - 削除拒否の相対パス: __pycache__, tests/__pycache__, .pytest_cache/v/cache, .verification/case-1794918785472612000/state, .verification/case-1794918785472612000/connector, .verification/tmp7d6m9xi7/connector, .verification/case-1794918785478873000/sandbox, .verification/case-1794918765898972000/state, .verification/case-1794918765898972000/connector, .verification/case-1794919047693695000/sandbox, .verification/tmpebmcl84h, .verification/case-1794919047689185000/state, .verification/case-1794919047689185000/connector, .verification/case-1794918785475350000, .verification/case-1794918765900912000/connector, .verification/case-1794918765908729000, .verification/tmpfseoq0hv/state, .verification/tmpfseoq0hv/connector, .verification/case-1794918757080150000, .verification/case-1794919047697225000, .verification/case-1794918785474286000/state, .verification/case-1794918785474286000/connector, .verification/case-1794918765901926000, .verification/case-1794918785482401000, .verification/case-1794918757076966000/state, .verification/case-1794918757076966000/connector, .verification/case-1794919047687606000/state, .verification/case-1794919047687606000/connector, .verification/case-1794918757087129000, .verification/case-1794919047690249000, .verification/tmplb95ad8g/sandbox, .verification/case-1794918757083649000/sandbox, .verification/case-1794918757078988000/connector, .verification/tmp1ae0s1v6, .verification/case-1794918765905330000/sandbox
- 一時検証フィクスチャのファイルは削除済み。.tmp/regression/stateのrmdirがOperation not permittedとなったため再試行せず、空ディレクトリstate・directory・connectorと親regressionが残存（directory・connectorも各1回のrmdirが拒否）。.tmpは空でないため維持。
- 実行上の失敗: 最初のgit statusはグローバル.gitconfig参照拒否。Git操作はそこで停止し設定を無効化する迂回なし。Python/GitランチャーはOS一時領域のXcodeキャッシュ作成拒否を表示（TMPDIR指定後も発生）。初回git確認前にTMPDIRを指定し忘れた。以後のPython処理はプロジェクト内TMPDIRを指定。
- 今回の変更はREADME.mdとVALIDATION.mdのみ。公開・push・通信・購入・メッセージ送信・プラグイン利用・他プロジェクト変更・.codex設定変更なし。
