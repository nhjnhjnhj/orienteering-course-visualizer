"""オリエンテーリングコースの読み込み・検証・描画処理。"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib

# Cloud Runのような画面を持たない環境でも描画できるようにする。
matplotlib.use("Agg")

import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import networkx as nx
import pandas as pd


GATE_LAT = 35.56216314920026
GATE_LON = 139.57681269368194

REQUIRED_NODE_COLUMNS = {"node_id", "lat", "lon"}
REQUIRED_EDGE_COLUMNS = {"from_node", "to_node", "length_m"}
REQUIRED_COURSE_KEYS = {
    "n_controls",
    "total_distance_m",
    "total_gain_m",
    "estimated_time_min",
    "fitness",
    "objectives",
    "weights",
    "controls",
    "course_nodes",
}
REQUIRED_OBJECTIVE_KEYS = {"f_map", "f_dist", "f_time", "f_route"}
REQUIRED_CONTROL_KEYS = {"name", "feature", "lat", "lon", "attraction_score"}


class CourseDataError(ValueError):
    """入力ファイルの内容が可視化に必要な形式を満たさない場合の例外。"""


@dataclass(frozen=True)
class NetworkData:
    """道路ネットワークと元データをまとめたコンテナ。"""

    graph: nx.DiGraph
    nodes: pd.DataFrame
    edges: pd.DataFrame


@dataclass(frozen=True)
class RenderResult:
    """地図描画結果。"""

    figure: Figure
    warnings: tuple[str, ...]


def configure_japanese_font() -> str:
    """利用可能な日本語フォントを選び、Matplotlibへ設定する。"""

    preferred_fonts = (
        "Hiragino Sans",
        "Yu Gothic",
        "YuGothic",
        "Meiryo",
        "MS Gothic",
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "IPAGothic",
        "DejaVu Sans",
    )
    available_fonts = {font.name for font in font_manager.fontManager.ttflist}

    selected = "DejaVu Sans"
    for font_name in preferred_fonts:
        if font_name in available_fonts:
            selected = font_name
            break

    plt.rcParams["font.family"] = selected
    plt.rcParams["axes.unicode_minus"] = False
    return selected


def _require_columns(df: pd.DataFrame, required: set[str], file_label: str) -> None:
    missing = sorted(required - set(df.columns))
    if missing:
        raise CourseDataError(
            f"{file_label} に必要な列がありません: {', '.join(missing)}"
        )


def load_network(nodes_path: str | Path, edges_path: str | Path) -> NetworkData:
    """CSVから有向道路ネットワークを構築する。"""

    nodes_path = Path(nodes_path)
    edges_path = Path(edges_path)

    if not nodes_path.exists():
        raise FileNotFoundError(f"nodes.csv が見つかりません: {nodes_path}")
    if not edges_path.exists():
        raise FileNotFoundError(f"edges.csv が見つかりません: {edges_path}")

    nodes_df = pd.read_csv(nodes_path)
    edges_df = pd.read_csv(edges_path)
    _require_columns(nodes_df, REQUIRED_NODE_COLUMNS, "nodes.csv")
    _require_columns(edges_df, REQUIRED_EDGE_COLUMNS, "edges.csv")

    nodes_df = nodes_df.copy()
    edges_df = edges_df.copy()

    try:
        nodes_df["node_id"] = pd.to_numeric(nodes_df["node_id"], errors="raise").astype("int64")
        nodes_df["lat"] = pd.to_numeric(nodes_df["lat"], errors="raise")
        nodes_df["lon"] = pd.to_numeric(nodes_df["lon"], errors="raise")
        edges_df["from_node"] = pd.to_numeric(edges_df["from_node"], errors="raise").astype("int64")
        edges_df["to_node"] = pd.to_numeric(edges_df["to_node"], errors="raise").astype("int64")
        edges_df["length_m"] = pd.to_numeric(edges_df["length_m"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise CourseDataError(f"CSVに数値へ変換できない値があります: {exc}") from exc

    if nodes_df["node_id"].duplicated().any():
        duplicate_ids = nodes_df.loc[nodes_df["node_id"].duplicated(), "node_id"].head(5).tolist()
        raise CourseDataError(f"nodes.csv の node_id が重複しています: {duplicate_ids}")

    node_ids = set(nodes_df["node_id"].tolist())
    edge_node_ids = set(edges_df["from_node"]).union(edges_df["to_node"])
    missing_node_ids = sorted(edge_node_ids - node_ids)
    if missing_node_ids:
        preview = ", ".join(map(str, missing_node_ids[:5]))
        raise CourseDataError(
            f"edges.csv が nodes.csv に存在しないノードを参照しています: {preview}"
        )

    graph = nx.DiGraph()
    for row in nodes_df.itertuples(index=False):
        attributes = {"y": float(row.lat), "x": float(row.lon)}
        if hasattr(row, "elevation"):
            attributes["elevation"] = float(row.elevation)
        graph.add_node(int(row.node_id), **attributes)

    for row in edges_df.itertuples(index=False):
        attributes: dict[str, float] = {"length": float(row.length_m)}
        if hasattr(row, "elevation_change"):
            attributes["elevation_change"] = float(row.elevation_change)
        if hasattr(row, "elevation_gain"):
            attributes["elevation_gain"] = float(row.elevation_gain)
        graph.add_edge(int(row.from_node), int(row.to_node), **attributes)

    return NetworkData(
        graph=graph,
        nodes=nodes_df.set_index("node_id", drop=False),
        edges=edges_df,
    )


def load_course_json(source: str | Path | io.BytesIO | Any) -> dict[str, Any]:
    """パスまたはファイルオブジェクトからコースJSONを読み込む。"""

    try:
        if isinstance(source, (str, Path)):
            with Path(source).open("r", encoding="utf-8") as file:
                data = json.load(file)
        else:
            data = json.load(source)
    except json.JSONDecodeError as exc:
        raise CourseDataError(
            f"JSONの形式が正しくありません（{exc.lineno}行目、{exc.colno}列目）。"
        ) from exc

    if not isinstance(data, dict):
        raise CourseDataError("JSONの最上位はオブジェクト（{ ... }）である必要があります。")
    return data


def _require_numeric(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CourseDataError(f"{label} は数値である必要があります。")
    if not math.isfinite(float(value)):
        raise CourseDataError(f"{label} は有限の数値である必要があります。")


def validate_course(course: dict[str, Any], graph: nx.DiGraph | None = None) -> list[str]:
    """コースJSONを検証し、致命的でない差異を警告として返す。"""

    missing = sorted(REQUIRED_COURSE_KEYS - set(course))
    if missing:
        raise CourseDataError(f"JSONに必要な項目がありません: {', '.join(missing)}")

    for key in (
        "n_controls",
        "total_distance_m",
        "total_gain_m",
        "estimated_time_min",
        "fitness",
    ):
        _require_numeric(course[key], key)

    if int(course["n_controls"]) < 0:
        raise CourseDataError("n_controls は0以上である必要があります。")

    controls = course["controls"]
    if not isinstance(controls, list):
        raise CourseDataError("controls は配列である必要があります。")

    for index, control in enumerate(controls, start=1):
        if not isinstance(control, dict):
            raise CourseDataError(f"controls[{index - 1}] はオブジェクトである必要があります。")
        missing_control = sorted(REQUIRED_CONTROL_KEYS - set(control))
        if missing_control:
            raise CourseDataError(
                f"{index}番目のコントロールに必要な項目がありません: "
                f"{', '.join(missing_control)}"
            )
        if not isinstance(control["name"], str) or not control["name"].strip():
            raise CourseDataError(f"{index}番目の name は空でない文字列にしてください。")
        if not isinstance(control["feature"], str):
            raise CourseDataError(f"{index}番目の feature は文字列にしてください。")
        _require_numeric(control["lat"], f"controls[{index - 1}].lat")
        _require_numeric(control["lon"], f"controls[{index - 1}].lon")
        _require_numeric(
            control["attraction_score"],
            f"controls[{index - 1}].attraction_score",
        )

    objectives = course["objectives"]
    weights = course["weights"]
    if not isinstance(objectives, dict) or not isinstance(weights, dict):
        raise CourseDataError("objectives と weights はオブジェクトである必要があります。")

    missing_objectives = sorted(REQUIRED_OBJECTIVE_KEYS - set(objectives))
    missing_weights = sorted(REQUIRED_OBJECTIVE_KEYS - set(weights))
    if missing_objectives:
        raise CourseDataError(
            f"objectives に必要な項目がありません: {', '.join(missing_objectives)}"
        )
    if missing_weights:
        raise CourseDataError(
            f"weights に必要な項目がありません: {', '.join(missing_weights)}"
        )

    for key in REQUIRED_OBJECTIVE_KEYS:
        _require_numeric(objectives[key], f"objectives.{key}")
        _require_numeric(weights[key], f"weights.{key}")

    course_nodes = course["course_nodes"]
    if not isinstance(course_nodes, list) or len(course_nodes) < 2:
        raise CourseDataError("course_nodes は2件以上のノードIDを持つ配列にしてください。")
    if any(isinstance(node_id, bool) or not isinstance(node_id, int) for node_id in course_nodes):
        raise CourseDataError("course_nodes の各要素は整数のノードIDにしてください。")

    warnings: list[str] = []
    if int(course["n_controls"]) != len(controls):
        warnings.append(
            f"n_controls（{int(course['n_controls'])}）と controls の件数"
            f"（{len(controls)}）が一致していません。"
        )
    if course_nodes[0] != course_nodes[-1]:
        warnings.append("course_nodes の先頭と末尾が異なるため、周回コースではありません。")

    if graph is not None:
        missing_graph_nodes = [node_id for node_id in course_nodes if node_id not in graph]
        if missing_graph_nodes:
            preview = ", ".join(map(str, missing_graph_nodes[:5]))
            raise CourseDataError(
                "course_nodes に道路ネットワークへ存在しないノードIDがあります: "
                f"{preview}"
            )

    return warnings


def _network_segments(graph: nx.DiGraph) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    for source, target in graph.edges:
        segments.append(
            [
                (graph.nodes[source]["x"], graph.nodes[source]["y"]),
                (graph.nodes[target]["x"], graph.nodes[target]["y"]),
            ]
        )
    return segments


def _route_segments(
    graph: nx.DiGraph,
    course_nodes: list[int],
) -> tuple[list[list[tuple[float, float]]], list[str]]:
    segments: list[list[tuple[float, float]]] = []
    warnings: list[str] = []

    for source, target in zip(course_nodes, course_nodes[1:]):
        try:
            path = nx.shortest_path(graph, source, target, weight="length")
        except nx.NetworkXNoPath:
            warnings.append(f"ノード {source} から {target} への経路が見つかりませんでした。")
            continue

        for path_source, path_target in zip(path, path[1:]):
            segments.append(
                [
                    (graph.nodes[path_source]["x"], graph.nodes[path_source]["y"]),
                    (graph.nodes[path_target]["x"], graph.nodes[path_target]["y"]),
                ]
            )

    return segments, warnings


def draw_course_map(
    course: dict[str, Any],
    graph: nx.DiGraph,
    *,
    show_network: bool = True,
    show_labels: bool = True,
    label_length: int = 12,
) -> RenderResult:
    """道路ネットワーク、経路、コントロール地点をMatplotlibで描画する。"""

    configure_japanese_font()
    validation_warnings = validate_course(course, graph)

    figure, axis = plt.subplots(figsize=(11, 11))

    if show_network:
        network_collection = LineCollection(
            _network_segments(graph),
            colors="#d8dce2",
            linewidths=0.45,
            alpha=0.85,
            zorder=1,
        )
        axis.add_collection(network_collection)

    route_segments, route_warnings = _route_segments(graph, course["course_nodes"])
    route_collection = LineCollection(
        route_segments,
        colors="#2557d6",
        linewidths=2.6,
        alpha=0.95,
        zorder=3,
    )
    axis.add_collection(route_collection)

    for index, control in enumerate(course["controls"], start=1):
        longitude = float(control["lon"])
        latitude = float(control["lat"])
        axis.scatter(
            longitude,
            latitude,
            c="white",
            edgecolors="#2557d6",
            s=165,
            linewidths=2.4,
            zorder=4,
        )
        axis.text(
            longitude,
            latitude + 0.0003,
            str(index),
            fontsize=8.5,
            fontweight="bold",
            ha="center",
            va="center",
            color="#17306e",
            zorder=5,
        )
        if show_labels:
            short_name = control["name"][:label_length]
            axis.annotate(
                f"{index}: {short_name}",
                xy=(longitude, latitude),
                xytext=(0, 14),
                textcoords="offset points",
                fontsize=8,
                ha="center",
                zorder=5,
                bbox={
                    "boxstyle": "round,pad=0.22",
                    "facecolor": "white",
                    "edgecolor": "#bcc5d6",
                    "alpha": 0.88,
                },
            )

    axis.scatter(
        GATE_LON,
        GATE_LAT,
        c="#f8c630",
        edgecolors="#222222",
        s=320,
        marker="*",
        linewidths=1.2,
        zorder=6,
    )

    legend_items = [
        Line2D([0], [0], color="#2557d6", linewidth=2.6, label="コース経路"),
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="white",
            markeredgecolor="#2557d6",
            markeredgewidth=2,
            markersize=9,
            label="コントロール地点",
        ),
        Line2D(
            [0],
            [0],
            marker="*",
            color="none",
            markerfacecolor="#f8c630",
            markeredgecolor="#222222",
            markersize=13,
            label="正門（スタート・ゴール）",
        ),
    ]
    axis.legend(handles=legend_items, fontsize=9, loc="upper left", framealpha=0.95)

    axis.set_title(
        "最良コース（GA）\n"
        f"コントロール数: {int(course['n_controls'])}件  "
        f"距離: {float(course['total_distance_m']):,.0f}m  "
        f"推定時間: {float(course['estimated_time_min']):.1f}分  "
        f"累積登り: {float(course['total_gain_m']):.1f}m",
        fontsize=13,
        pad=14,
    )
    axis.set_xlabel("経度", fontsize=10)
    axis.set_ylabel("緯度", fontsize=10)
    axis.grid(True, alpha=0.18, linewidth=0.6)

    all_lons = [float(attributes["x"]) for attributes in graph.nodes.values()]
    all_lats = [float(attributes["y"]) for attributes in graph.nodes.values()]
    if all_lons and all_lats:
        lon_margin = max((max(all_lons) - min(all_lons)) * 0.025, 0.0005)
        lat_margin = max((max(all_lats) - min(all_lats)) * 0.025, 0.0005)
        axis.set_xlim(min(all_lons) - lon_margin, max(all_lons) + lon_margin)
        axis.set_ylim(min(all_lats) - lat_margin, max(all_lats) + lat_margin)
        mean_latitude = sum(all_lats) / len(all_lats)
        axis.set_aspect(1 / math.cos(math.radians(mean_latitude)))

    figure.tight_layout()
    return RenderResult(
        figure=figure,
        warnings=tuple(validation_warnings + route_warnings),
    )


def figure_to_png_bytes(figure: Figure, dpi: int = 160) -> bytes:
    """Matplotlib FigureをPNGのバイト列へ変換する。"""

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
    buffer.seek(0)
    return buffer.getvalue()


def _escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_markdown_report(course: dict[str, Any]) -> str:
    """元プログラムと同じ内容を中心にMarkdownレポートを生成する。"""

    validate_course(course)
    generated_at = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    objectives = course["objectives"]
    weights = course["weights"]

    lines = [
        "# 最良コース 詳細レポート（GA）",
        "",
        f"生成日時：{generated_at}",
        "",
        "---",
        "",
        "## コース概要",
        "",
        "| 項目 | 値 |",
        "|---|---:|",
        f"| コントロール数 | {int(course['n_controls'])} 件 |",
        f"| 総距離 | {float(course['total_distance_m']):.1f} m |",
        f"| 累積登り高低差 | {float(course['total_gain_m']):.1f} m |",
        f"| 推定所要時間 | {float(course['estimated_time_min']):.1f} 分 |",
        f"| fitness（重み付き和） | {float(course['fitness']):.6f} |",
        "",
        "---",
        "",
        "## 目的関数値と重み",
        "",
        "| 目的関数 | 値 | 重み | 重み付き値 |",
        "|---|---:|---:|---:|",
    ]

    labels = {
        "f_map": "f_map（コントロール数のずれ）",
        "f_dist": "f_dist（密集ペナルティ）",
        "f_time": "f_time（時間のずれ・分）",
        "f_route": "f_route（累積登り超過・m）",
    }
    for key in ("f_map", "f_dist", "f_time", "f_route"):
        value = float(objectives[key])
        weight = float(weights[key])
        lines.append(
            f"| {labels[key]} | {value:.3f} | {weight:.2f} | {value * weight:.3f} |"
        )

    formula = " + ".join(
        f"{key}×{float(weights[key]):.2f}"
        for key in ("f_map", "f_dist", "f_time", "f_route")
    )
    lines.extend(
        [
            "",
            f"> fitness = {formula}",
            "",
            "---",
            "",
            "## コントロール地点一覧",
            "",
            "| 順番 | 名前 | 種別 | 緯度 | 経度 | 魅力スコア |",
            "|---:|---|---|---:|---:|---:|",
        ]
    )

    for index, control in enumerate(course["controls"], start=1):
        lines.append(
            "| "
            f"{index} | {_escape_markdown_cell(control['name'])} | "
            f"{_escape_markdown_cell(control['feature'])} | "
            f"{float(control['lat']):.7f} | {float(control['lon']):.7f} | "
            f"{float(control['attraction_score']):.2f} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## コース地図",
            "",
            "同時にダウンロードした `course_best.png` を参照してください。",
            "",
        ]
    )
    return "\n".join(lines)
