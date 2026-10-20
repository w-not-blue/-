"""
应用下载统计模块

提供下载次数记录与查询功能:
- 多维度统计 (按平台/应用/日期)
- 持久化存储 (JSON文件)
- 并发安全 (文件锁)
"""

import json
import os
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from src.utils.logger import setup_logger

logger = setup_logger("download_stats")

class DownloadStats:
    """下载统计管理器"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.stats_file = os.path.join(data_dir, "download_stats.json")
        self._lock = threading.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self._ensure_stats_file()

    def _ensure_stats_file(self) -> None:
        if not os.path.exists(self.stats_file):
            self._write_stats({
                'version': '1.0',
                'created_at': datetime.now().isoformat(),
                'records': [],
                'total': 0,
                'by_platform': {},
                'by_app_type': {},
                'by_date': {}
            })

    def _read_stats(self) -> Dict[str, Any]:
        try:
            with open(self.stats_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'version': '1.0',
                'records': [],
                'total': 0,
                'by_platform': {},
                'by_app_type': {},
                'by_date': {}
            }

    def _write_stats(self, data: Dict[str, Any]) -> None:
        with open(self.stats_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def record_download(
        self, platform: str, app_type: str, filename: str,
        size_mb: float, user_agent: str = "", ip_address: str = ""
    ) -> Dict[str, Any]:
        with self._lock:
            stats = self._read_stats()

            record = {
                'platform': platform,
                'app_type': app_type,
                'filename': filename,
                'size_mb': size_mb,
                'user_agent': user_agent[:200],
                'ip_address': ip_address,
                'timestamp': datetime.now().isoformat()
            }
            stats['records'].append(record)

            stats['total'] = stats.get('total', 0) + 1

            stats['by_platform'][platform] = stats['by_platform'].get(platform, 0) + 1
            stats['by_app_type'][app_type] = stats['by_app_type'].get(app_type, 0) + 1

            today = datetime.now().strftime('%Y-%m-%d')
            stats['by_date'][today] = stats['by_date'].get(today, 0) + 1

            if len(stats['records']) > 10000:
                stats['records'] = stats['records'][-5000:]

            self._write_stats(stats)
            logger.info(f"Download recorded: {platform}/{app_type}/{filename}")

            return record

    def get_summary(self) -> Dict[str, Any]:
        stats = self._read_stats()
        return {
            'success': True,
            'total': stats.get('total', 0),
            'by_platform': stats.get('by_platform', {}),
            'by_app_type': stats.get('by_app_type', {}),
            'by_date': dict(sorted(stats.get('by_date', {}).items())[-7:]),
            'recent': stats['records'][-10:] if stats.get('records') else []
        }

    def get_by_platform(self, platform: str) -> int:
        stats = self._read_stats()
        return stats.get('by_platform', {}).get(platform, 0)

    def reset_stats(self) -> bool:
        with self._lock:
            self._write_stats({
                'version': '1.0',
                'created_at': datetime.now().isoformat(),
                'records': [],
                'total': 0,
                'by_platform': {},
                'by_app_type': {},
                'by_date': {}
            })
            logger.warning("Download stats reset")
            return True

_download_stats_instance: Optional[DownloadStats] = None

def get_download_stats() -> DownloadStats:
    global _download_stats_instance
    if _download_stats_instance is None:
        _download_stats_instance = DownloadStats()
    return _download_stats_instance
