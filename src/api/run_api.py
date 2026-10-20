"""Run registry, async simulation jobs and live events."""

import json
from queue import Empty

import pandas as pd
from flask import Blueprint, Response, jsonify, request, stream_with_context

from src.services import run_store
from src.services.run_manager import get_run_manager

run_bp = Blueprint("runs", __name__)


@run_bp.route("/api/v1/runs", methods=["GET"])
def list_runs():
    get_run_manager()
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        limit = 20
    return jsonify({"success": True, "runs": run_store.list_runs(limit)})


@run_bp.route("/api/v1/runs", methods=["POST"])
def create_run():
    body = request.get_json(silent=True) or {}
    origin = "ai" if body.get("origin") == "ai" else "web"
    try:
        run_id = get_run_manager().submit(body, origin=origin)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": "创建运行失败: %s" % exc}), 500
    return jsonify({"success": True, "run_id": run_id, "status": "queued"})


@run_bp.route("/api/v1/runs/latest", methods=["GET"])
def latest_run():
    get_run_manager()
    record = run_store.latest_run()
    if not record:
        return jsonify({"success": False, "message": "暂无运行记录"}), 404
    return jsonify({"success": True, "run": record})


@run_bp.route("/api/v1/runs/compare", methods=["GET"])
def compare_runs():
    left = (request.args.get("left") or "").strip()
    right = (request.args.get("right") or "").strip()
    if not left or not right:
        runs = run_store.list_runs(2)
        if len(runs) < 2:
            return jsonify({"success": False, "message": "至少需要两次运行才能对比"}), 404
        right, left = runs[0]["run_id"], runs[1]["run_id"]
    comparison = run_store.compare_runs(left, right)
    if not comparison:
        return jsonify({"success": False, "message": "运行记录不存在"}), 404
    return jsonify({"success": True, "comparison": comparison})


@run_bp.route("/api/v1/runs/<run_id>", methods=["GET"])
def get_run(run_id: str):
    record = run_store.get_run(run_id)
    if not record:
        return jsonify({"success": False, "message": "运行记录不存在"}), 404
    return jsonify({"success": True, "run": record})


@run_bp.route("/api/v1/runs/<run_id>/summary", methods=["GET"])
def get_run_summary(run_id: str):
    record = run_store.get_run(run_id)
    if not record:
        return jsonify({"success": False, "message": "运行记录不存在"}), 404
    return jsonify({
        "success": True,
        "run_id": run_id,
        "status": record.get("status"),
        "summary": record.get("summary") or {},
        "params": record.get("params") or {},
    })


@run_bp.route("/api/v1/runs/<run_id>/series", methods=["GET"])
def get_run_series(run_id: str):
    record = run_store.get_run(run_id)
    if not record:
        return jsonify({"success": False, "message": "运行记录不存在"}), 404
    df = run_store.load_results(run_id)
    if df is None:
        return jsonify({"success": False, "message": "结果文件不存在"}), 404
    algorithm = (request.args.get("algorithm") or "").strip()
    if algorithm:
        df = df[df["algorithm"] == algorithm]
    df = df.where(pd.notnull(df), None)
    return jsonify({
        "success": True,
        "run_id": run_id,
        "data": df.to_dict("records"),
    })


@run_bp.route("/api/v1/events", methods=["GET"])
def run_events():
    manager = get_run_manager()
    queue = manager.events.subscribe()

    def stream():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    event = queue.get(timeout=15)
                except Empty:
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
        finally:
            manager.events.unsubscribe(queue)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
