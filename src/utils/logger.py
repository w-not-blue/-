import logging
import sys
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler


class _ResilientStream:
    """Console stream that degrades unencodable characters instead of raising.

    A Chinese Windows console defaults to GBK, where markers such as ✓ / ✗ /
    → do not exist. Without this wrapper a single log line can abort a whole
    CLI command with UnicodeEncodeError.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text: str):
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, 'encoding', None) or 'ascii'
            safe = text.encode(encoding, 'replace').decode(encoding, 'replace')
            return self._stream.write(safe)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return bool(getattr(self._stream, 'isatty', lambda: False)())


def _console_stream():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass
    return _ResilientStream(sys.stdout)


def setup_logger(name: str = "datacenter_sim", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        ch = logging.StreamHandler(_console_stream())
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(
            '[%(asctime)s] [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(ch)

        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)

        # 使用 RotatingFileHandler 实现日志轮转，单个文件最大 10MB，保留 5 个备份
        fh = RotatingFileHandler(
            log_dir / "simulation.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(name)s] %(message)s'
        ))
        logger.addHandler(fh)

    return logger