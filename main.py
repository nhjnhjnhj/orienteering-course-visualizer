"""Cloud Run / Functions Framework向けHTTPエントリポイント。"""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

import functions_framework
import matplotlib.pyplot as plt
from flask import Response, make_response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from course_visualizer import (
    CourseDataError,
    draw_course_map,
    figure_to_png_bytes,
    load_network,
    validate_course,
)


BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
OBJECTIVE_LABELS = {
    "f_map": "コントロール数のずれ",
    "f_dist": "密集ペナルティ",
    "f_time": "時間のずれ",
    "f_route": "累積登り超過",
}

logger = logging.getLogger(__name__)
templates = Environment(
    loader=FileSystemLoader(BASE_DIR / "templates"),
    autoescape=select_autoescape(("html", "xml")),
)

# コンテナ起動時に一度だけ構築し、各リクエストでは読み取り専用で共有する。
NETWORK = load_network(BASE_DIR / "data" / "nodes.csv", BASE_DIR / "data" / "edges.csv")


def _render(template_name: str, *, status: int = 200, **context: Any) -> Response:
    html = templates.get_template(template_name).render(**context)
    response = make_response(html, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def _error(message: str, status: int = 400) -> Response:
    return _render("error.html", status=status, message=message)


def _parse_upload(request: Any) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > MAX_UPLOAD_BYTES + 64_000:
        raise CourseDataError("ファイルサイズが上限（2 MB）を超えています。")

    uploaded = request.files.get("course_json")
    if uploaded is None or not uploaded.filename:
        raise CourseDataError("JSONファイルを選択してください。")
    if not uploaded.filename.lower().endswith(".json"):
        raise CourseDataError("拡張子が .json のファイルを選択してください。")

    content = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise CourseDataError("ファイルサイズが上限（2 MB）を超えています。")
    if not content:
        raise CourseDataError("アップロードされたファイルが空です。")

    try:
        course = json.load(io.BytesIO(content))
    except UnicodeDecodeError as exc:
        raise CourseDataError("JSONファイルはUTF-8で保存してください。") from exc
    except json.JSONDecodeError as exc:
        raise CourseDataError(
            f"JSONの形式が正しくありません（{exc.lineno}行目、{exc.colno}列目）。"
        ) from exc
    if not isinstance(course, dict):
        raise CourseDataError("JSONの最上位はオブジェクト（{ ... }）である必要があります。")
    return course


@functions_framework.http
def app(request: Any) -> Response:
    """入力画面を表示し、アップロードされたコースを可視化する。"""

    if request.method == "GET":
        return _render("index.html", max_upload_mb=MAX_UPLOAD_BYTES // 1024 // 1024)
    if request.method != "POST":
        response = _error("このHTTPメソッドには対応していません。", 405)
        response.headers["Allow"] = "GET, POST"
        return response

    figure = None
    try:
        course = _parse_upload(request)
        input_warnings = validate_course(course, NETWORK.graph)
        render_result = draw_course_map(course, NETWORK.graph)
        figure = render_result.figure
        image_base64 = base64.b64encode(figure_to_png_bytes(figure)).decode("ascii")

        controls = [dict(control, order=index) for index, control in enumerate(course["controls"], 1)]
        objectives = []
        for key in ("f_map", "f_dist", "f_time", "f_route"):
            value = float(course["objectives"][key])
            weight = float(course["weights"][key])
            objectives.append(
                {
                    "key": key,
                    "label": OBJECTIVE_LABELS[key],
                    "value": value,
                    "weight": weight,
                    "weighted": value * weight,
                }
            )

        warnings = list(dict.fromkeys([*input_warnings, *render_result.warnings]))
        return _render(
            "result.html",
            course=course,
            controls=controls,
            objectives=objectives,
            warnings=warnings,
            image_base64=image_base64,
        )
    except CourseDataError as exc:
        return _error(str(exc), 400)
    except Exception:
        logger.exception("コース可視化中に予期しないエラーが発生しました")
        return _error("処理中にエラーが発生しました。時間をおいて再度お試しください。", 500)
    finally:
        if figure is not None:
            plt.close(figure)
