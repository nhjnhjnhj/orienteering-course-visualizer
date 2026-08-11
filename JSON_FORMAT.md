# 入力JSON形式

本アプリは、コンペティションプログラムが出力したJSONを読み込みます。
同梱の `samples/best_course.json` をひな型として利用してください。

## 必須項目

```json
{
  "n_controls": 6,
  "total_distance_m": 4994.9,
  "total_gain_m": 71.1,
  "estimated_time_min": 88.8,
  "fitness": 19.860382,
  "objectives": {
    "f_map": 2,
    "f_dist": 0.0,
    "f_time": 28.771,
    "f_route": 21.1
  },
  "weights": {
    "f_map": 0.1,
    "f_dist": 0.25,
    "f_time": 0.5,
    "f_route": 0.25
  },
  "controls": [],
  "course_nodes": []
}
```

## `controls` の各要素

| 項目 | 型 | 内容 |
|---|---|---|
| `name` | 文字列 | コントロール地点名 |
| `feature` | 文字列 | 地点の種別 |
| `lat` | 数値 | 緯度 |
| `lon` | 数値 | 経度 |
| `attraction_score` | 数値 | 魅力スコア |

## `course_nodes`

道路ネットワーク上で巡回するノードIDを、訪問順に整数で並べます。
地図の青い経路は、隣り合うノードID間の最短経路を `length_m` を重みとして計算して描画します。

```json
"course_nodes": [7368660689, 7368660671, 3732569009, 7368660689]
```

周回コースの場合は、先頭と末尾を同じノードIDにしてください。
また、すべてのノードIDが `data/nodes.csv` に存在する必要があります。
