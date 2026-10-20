import os
import sqlite3
import threading
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_SERVICE_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, 'data', 'users.db')


class UserStore:
    def __init__(self, db_path=None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _ensure_schema(self):
        with self._init_lock:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with self._connect() as conn:
                conn.execute(
                    '''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_login TEXT
                    )
                    '''
                )
                row = conn.execute(
                    'SELECT id FROM users WHERE username = ? COLLATE NOCASE',
                    ('yumo',)
                ).fetchone()
                if row is None:
                    conn.execute(
                        'INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                        ('yumo', generate_password_hash('123456', method='pbkdf2:sha256'), self._now())
                    )

    @staticmethod
    def _now():
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def get_by_username(self, username):
        with self._connect() as conn:
            return conn.execute(
                'SELECT id, username, password_hash, created_at, last_login '
                'FROM users WHERE username = ? COLLATE NOCASE',
                (username,)
            ).fetchone()

    def get_by_id(self, user_id):
        with self._connect() as conn:
            return conn.execute(
                'SELECT id, username, created_at, last_login FROM users WHERE id = ?',
                (user_id,)
            ).fetchone()

    def create_user(self, username, password):
        password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    'INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                    (username, password_hash, self._now())
                )
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError('用户名已存在') from exc
        return self.get_by_id(user_id)

    def change_password(self, user_id, current_password, new_password):
        with self._connect() as conn:
            row = conn.execute(
                'SELECT password_hash FROM users WHERE id = ?',
                (user_id,)
            ).fetchone()
            if row is None or not check_password_hash(row['password_hash'], current_password):
                return False
            conn.execute(
                'UPDATE users SET password_hash = ? WHERE id = ?',
                (generate_password_hash(new_password, method='pbkdf2:sha256'), user_id)
            )
        return True

    def verify_user(self, username, password):
        row = self.get_by_username(username)
        if row is None:
            return None
        if not check_password_hash(row['password_hash'], password):
            return None
        with self._connect() as conn:
            conn.execute(
                'UPDATE users SET last_login = ? WHERE id = ?',
                (self._now(), row['id'])
            )
        return {'id': row['id'], 'username': row['username']}


_store = None
_store_lock = threading.Lock()


def get_user_store():
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = UserStore()
    return _store