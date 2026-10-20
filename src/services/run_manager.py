"""Serialized simulation runner with a small event bus for live UI updates."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from queue import Empty, Full, Queue
from typing import Any, Dict, Optional, Tuple

from src.plugin import PluginManager
from src.plugin.protocol import PluginResult
from src.plugin.simulation_plugin import SimulationPlugin
from src.services import run_store
from src.simulation.metrics import summarize
from src.utils import load_run_metadata, load_simulation_data, save_run_metadata
from src.utils.logger import setup_logger
from src.utils.validation import validate_simulation_params

logger = setup_logger("run_manager")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class EventBus:
    def __init__(self) -> None:
        self._subscribers = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Queue:
        queue: Queue = Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except Full:
                pass


class RunManager:
    def __init__(self) -> None:
        self.events = EventBus()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dcs-run")
        self._run_lock = threading.Lock()
        try:
            run_store.import_legacy_run()
        except Exception as exc:
            logger.warning("导入历史运行失败: %s" % exc)
        try:
            for record in run_store.list_runs(50):
                if record.get("status") in ("queued", "running"):
                    run_store.update_run(
                        record["run_id"],
                        status="failed",
                        progress=100,
                        finished_at=_now(),
                        error="服务重启，任务已中断",
                    )
                    logger.warning("标记中断运行: %s", record["run_id"])
        except Exception as exc:
            logger.warning("清理中断运行失败: %s" % exc)

    def submit(self, params: Dict[str, Any], origin: str = "web") -> str:
        validated = self._validate(params)
        run_id = run_store.create_run(validated, origin=origin)
        self._publish(run_id, "run_created", 0, "queued", validated)
        self._executor.submit(self._execute, run_id, validated, origin)
        return run_id

    def run_sync(self, params: Dict[str, Any], origin: str = "web") -> Tuple[str, PluginResult]:
        validated = self._validate(params)
        run_id = run_store.create_run(validated, origin=origin)
        self._publish(run_id, "run_created", 0, "queued", validated)
        result = self._execute(run_id, validated, origin)
        return run_id, result

    def run_prediction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], PluginResult]:
        """Run a forecast scenario without overwriting the main simulation results."""
        validated = self._validate(params)
        config = {
            "num_servers": validated["num_servers"],
            "simulation_hours": validated["simulation_hours"],
            "parallel_workers": validated["parallel_workers"],
            "run_mode": validated["run_mode"],
            "server_type": validated["server_type"],
        }
        if validated.get("site_id"):
            config["site_id"] = validated["site_id"]

        with self._run_lock:
            manager = PluginManager()
            manager.register_plugin(SimulationPlugin)
            if not manager.load_plugin("SimulationPlugin", config):
                raise RuntimeError("预测仿真加载失败")
            try:
                result = manager.execute_plugin(
                    "SimulationPlugin",
                    algorithms=validated["algorithms"],
                    save_results=False,
                    visualization=False,
                    include_series=True,
                )
            finally:
                manager.unload_plugin("SimulationPlugin")
        return validated, result

    def simulation_lock(self):
        return self._run_lock

    def wait(self, run_id: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = run_store.get_run(run_id)
            if record and record.get("status") in ("success", "failed"):
                return record
            time.sleep(0.5)
        return run_store.get_run(run_id)

    def active_run(self) -> Optional[Dict[str, Any]]:
        for record in run_store.list_runs(20):
            if record.get("status") in ("queued", "running"):
                return record
        return None

    def _validate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(params or {})
        validated = validate_simulation_params(raw)
        site_id = raw.get("site_id")
        if site_id is not None:
            site_id = str(site_id).strip()[:64]
            if site_id:
                validated["site_id"] = site_id
        school_name = raw.get("school_name")
        if school_name is not None:
            school_name = str(school_name).strip()[:128]
            if school_name:
                validated["school_name"] = school_name
        return validated

    def _execute(self, run_id: str, params: Dict[str, Any], origin: str) -> PluginResult:
        with self._run_lock:
            run_store.update_run(run_id, status="running", progress=5, started_at=_now())
            self._publish(run_id, "run_progress", 5, "running", params)
            manager = PluginManager()
            try:
                manager.register_plugin(SimulationPlugin)
                manager.discover_plugins(plugin_dir="src/plugins")
                config = {
                    "num_servers": params["num_servers"],
                    "simulation_hours": params["simulation_hours"],
                    "parallel_workers": params["parallel_workers"],
                    "run_mode": params["run_mode"],
                    "server_type": params["server_type"],
                }
                if params.get("site_id"):
                    config["site_id"] = params["site_id"]
                if not manager.load_plugin("SimulationPlugin", config):
                    raise RuntimeError("SimulationPlugin 加载失败")

                run_store.update_run(run_id, progress=20)
                self._publish(run_id, "run_progress", 20, "running", params)
                result = manager.execute_plugin(
                    "SimulationPlugin", algorithms=params["algorithms"]
                )
                manager.unload_plugin("SimulationPlugin")
                if not result.success:
                    raise RuntimeError(result.message or "仿真执行失败")

                run_store.update_run(run_id, progress=80)
                self._publish(run_id, "run_progress", 80, "running", params)
                df = load_simulation_data()
                if df is None or df.empty:
                    raise RuntimeError("仿真结果文件为空")
                metadata = load_run_metadata() or {}
                metadata.update({
                    "run_id": run_id,
                    "origin": origin,
                    "generated_at": _now(),
                    "num_servers": params["num_servers"],
                    "simulation_hours": params["simulation_hours"],
                    "algorithms": params["algorithms"],
                })
                if params.get("site_id") and not metadata.get("site_id"):
                    metadata["site_id"] = params["site_id"]
                if params.get("school_name"):
                    metadata["school_name"] = params["school_name"]
                save_run_metadata(metadata)
                summary = summarize(df)
                run_store.save_results(run_id, df, metadata, summary)
                run_store.update_run(
                    run_id,
                    status="success",
                    progress=100,
                    finished_at=_now(),
                )
                self._publish(
                    run_id, "run_completed", 100, "success", params,
                    summary=summary, origin=origin,
                )
                return result
            except Exception as exc:
                logger.error("运行 %s 失败: %s" % (run_id, exc))
                try:
                    manager.unload_plugin("SimulationPlugin")
                except Exception:
                    pass
                run_store.update_run(
                    run_id,
                    status="failed",
                    progress=100,
                    finished_at=_now(),
                    error=str(exc),
                )
                self._publish(
                    run_id, "run_failed", 100, "failed", params,
                    error=str(exc), origin=origin,
                )
                return PluginResult.fail(
                    message=str(exc), error_code="RUN_FAILED", error_detail=str(exc)
                )

    def _publish(self, run_id: str, event_type: str, progress: int,
                 status: str, params: Dict[str, Any], **extra: Any) -> None:
        event = {
            "type": event_type,
            "run_id": run_id,
            "progress": progress,
            "status": status,
            "params": params,
            "timestamp": _now(),
        }
        event.update(extra)
        self.events.publish(event)


_manager: Optional[RunManager] = None
_manager_lock = threading.Lock()


def get_run_manager() -> RunManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = RunManager()
    return _manager


