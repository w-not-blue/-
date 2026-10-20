"""
环境重置与恢复模块

实现测试环境的快速重置与恢复机制:
- 状态快照 (Snapshot)
- 快速回滚 (Rollback)
- 自动清理 (Auto-cleanup)
- 环境隔离 (Isolation)

参考项目: Docker Snapshot, Vagrant, Terraform
"""

import os
import json
import time
import shutil
import threading
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("environment_manager")
config = Config()

class EnvironmentState(Enum):
    CLEAN = "clean"
    INITIALIZED = "initialized"
    DIRTY = "dirty"
    RESTORING = "restoring"
    SNAPSHOTTING = "snapshotting"

@dataclass
class Snapshot:
    snapshot_id: str
    timestamp: str
    state: Dict[str, Any]
    description: str
    size_bytes: int = 0

class EnvironmentManager:
    """
    环境管理器
    
    提供测试环境快照/回滚能力:
    1. 创建快照 - 保存当前完整状态
    2. 恢复快照 - 一键回滚到历史状态
    3. 清理环境 - 重置到初始状态
    4. 状态对比 - 比较环境变更
    """

    def __init__(self, snapshot_dir: str = "test_snapshots"):
        self.snapshot_dir = snapshot_dir
        self.max_snapshots = 10
        self.current_state = EnvironmentState.CLEAN
        self._snapshots: List[Snapshot] = []
        self._state_data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        
        os.makedirs(snapshot_dir, exist_ok=True)

    def save_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._state_data[key] = value
            self.current_state = EnvironmentState.DIRTY

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state_data.get(key, default)

    def create_snapshot(self, description: str = "") -> Snapshot:
        timestamp = datetime.now()
        snapshot_id = f"snapshot_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}"
        
        self.current_state = EnvironmentState.SNAPSHOTTING
        
        snapshot_data = {
            'state': dict(self._state_data),
            'meta': {
                'description': description,
                'created_at': timestamp.isoformat(),
                'snapshot_id': snapshot_id
            }
        }

        filepath = os.path.join(self.snapshot_dir, f"{snapshot_id}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(snapshot_data, f, indent=2, default=str, ensure_ascii=False)

        size = os.path.getsize(filepath)
        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp.isoformat(),
            state=dict(self._state_data),
            description=description,
            size_bytes=size
        )

        with self._lock:
            self._snapshots.append(snapshot)
            if len(self._snapshots) > self.max_snapshots:
                oldest = self._snapshots.pop(0)
                old_path = os.path.join(self.snapshot_dir, f"{oldest.snapshot_id}.json")
                if os.path.exists(old_path):
                    os.remove(old_path)

        self.current_state = EnvironmentState.INITIALIZED
        logger.info(f"Snapshot created: {snapshot_id}")
        return snapshot

    def restore_snapshot(self, snapshot_id: str = None) -> bool:
        self.current_state = EnvironmentState.RESTORING

        if snapshot_id is None and self._snapshots:
            snapshot_id = self._snapshots[-1].snapshot_id

        filepath = os.path.join(self.snapshot_dir, f"{snapshot_id}.json")
        if not os.path.exists(filepath):
            logger.error(f"Snapshot not found: {snapshot_id}")
            return False

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                snapshot_data = json.load(f)

            with self._lock:
                self._state_data = dict(snapshot_data['state'])
            
            logger.info(f"Restored snapshot: {snapshot_id}")
            self.current_state = EnvironmentState.INITIALIZED
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            self.current_state = EnvironmentState.DIRTY
            return False

    def reset_to_clean(self) -> None:
        with self._lock:
            self._state_data.clear()
        
        self.current_state = EnvironmentState.CLEAN
        logger.info("Environment reset to clean state")

    def cleanup_snapshots(self, keep_last: int = 5) -> int:
        removed = 0
        with self._lock:
            while len(self._snapshots) > keep_last:
                oldest = self._snapshots.pop(0)
                path = os.path.join(self.snapshot_dir, f"{oldest.snapshot_id}.json")
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
        
        logger.info(f"Cleaned up {removed} old snapshots")
        return removed

    def list_snapshots(self) -> List[Dict[str, Any]]:
        return [{
            'id': s.snapshot_id,
            'timestamp': s.timestamp,
            'description': s.description,
            'size_kb': s.size_bytes / 1024
        } for s in self._snapshots]

    def compare_snapshots(self, id1: str, id2: str) -> Dict[str, Any]:
        file1 = os.path.join(self.snapshot_dir, f"{id1}.json")
        file2 = os.path.join(self.snapshot_dir, f"{id2}.json")

        if not os.path.exists(file1) or not os.path.exists(file2):
            return {'error': 'One or both snapshots not found'}

        with open(file1, 'r') as f:
            data1 = json.load(f)['state']
        with open(file2, 'r') as f:
            data2 = json.load(f)['state']

        keys1, keys2 = set(data1.keys()), set(data2.keys())
        added = keys2 - keys1
        removed = keys1 - keys2
        common = keys1 & keys2
        
        changed = [k for k in common if data1[k] != data2[k]]

        return {
            'added_keys': list(added),
            'removed_keys': list(removed),
            'changed_keys': changed,
            'unchanged_keys': list(common - set(changed))
        }
