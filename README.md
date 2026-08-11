# オリエンテーリングコース可視化アプリ

コンペティションプログラムが出力した `best_course.json` をアップロードし、道路ネットワーク上のコース、コントロール地点、目的関数値をブラウザで確認するWebアプリです。Functions FrameworkをHTTP入口としてDockerコンテナ化し、Cloud Runサービスで動かします。

アップロードJSONと生成PNGはメモリ上だけで処理し、サーバーへ永続保存しません。

## 主な機能

- JSONファイルの検証（UTF-8、最大2 MB）
- 道路ネットワーク上の最短経路描画
- コース概要、コントロール一覧、評価値の表示
- 地図PNGのダウンロード
- 利用者向けエラー画面

入力仕様は [JSON_FORMAT.md](JSON_FORMAT.md) を参照してください。

## 構成

```text
main.py                 Functions FrameworkのHTTP入口
course_visualizer.py    JSON検証・経路計算・Matplotlib描画
templates/              Jinja2 HTML/CSS
data/                   道路ネットワークCSVの配置先（CSV自体は非公開）
Dockerfile              Cloud Run用コンテナ定義
```

## 道路ネットワークデータ

大学周辺の位置情報を含むため、`data/nodes.csv`と`data/edges.csv`はGitHubへ登録しません。GitHub上では空の`data/`フォルダを維持するため、`data/.gitkeep`だけを管理します。

ローカル実行またはコンテナのビルド前に、次のファイルを手元の`data/`へ配置してください。

```text
data/
├── .gitkeep
├── nodes.csv
└── edges.csv
```

`nodes.csv`の必須列:

```text
node_id,lat,lon
```

`edges.csv`の必須列:

```text
from_node,to_node,length_m
```

CSVは`.gitignore`で除外されています。`main.py`は起動時に両方のCSVを読み込むため、未配置の場合はアプリを起動できません。

## 入力JSON

可視化するコースJSONはブラウザからアップロードするため、本番環境にサンプルJSONは必要ありません。位置情報を含む`samples/*.json`もGitHubへ登録しません。

## ローカル起動

Python 3.12と [uv](https://docs.astral.sh/uv/) を使用します。

```bash
uv sync
env -u DEBUG uv run functions-framework --target=app --host=127.0.0.1 --port=8080
```

`http://localhost:8080`を開き、手元のコースJSONをアップロードしてください。簡易テストは次で実行できますが、ローカルの`samples/best_course.json`が必要です。

```bash
uv run python smoke_test.py
```

## Docker

```bash
docker build -t orienteering-visualizer .
docker run --rm -p 8080:8080 -e PORT=8080 orienteering-visualizer
```

Dockerイメージには日本語描画用のNoto CJKフォントと、GUIを必要としないMatplotlibのAggバックエンドが含まれます。

## Cloud Runへデプロイ

Google Cloudプロジェクトとgcloud CLIを設定後、プロジェクトルートで実行します。

```bash
gcloud run deploy orienteering-visualizer \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --cpu 1 \
  --memory 1Gi \
  --concurrency 1 \
  --min 0 \
  --max 3 \
  --timeout 120s
```

`--source .` は同梱のDockerfileをCloud Buildでビルドします。詳細な例外は標準エラー経由でCloud Loggingへ記録され、レスポンスにはスタックトレースを表示しません。
