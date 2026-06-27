"""
db.py — طبقة قاعدة البيانات لبوت PT SGU PDF.
SQLite بسيطة بدون اعتماديات خارجية، مع ترقية تلقائية للجداول القديمة
(لو عندك ملف pt_sgu.db شغال بالفعل، بياناتك مش هتضيع).
"""

import sqlite3
from datetime import datetime, timedelta

_DB_PATH = "pt_sgu.db"


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def init_db(db_path: str, default_subjects: list):
    global _DB_PATH
    _DB_PATH = db_path
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            content_type TEXT NOT NULL,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT,
            uploaded_by INTEGER,
            uploaded_at TEXT
        )
    """)
    # ترقية الجدول القديم لو ناقصه أعمدة جديدة (بدون فقدان بيانات)
    if not _column_exists(conn, "files", "file_size"):
        cur.execute("ALTER TABLE files ADD COLUMN file_size INTEGER")
    if not _column_exists(conn, "files", "downloads"):
        cur.execute("ALTER TABLE files ADD COLUMN downloads INTEGER DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT,
            created_by INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TEXT,
            last_seen TEXT,
            is_blocked INTEGER DEFAULT 0,
            blocked_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
    """)

    # تعبئة المواد الافتراضية أول تشغيل فقط (لو الجدول فاضي)
    cur.execute("SELECT COUNT(*) FROM subjects")
    if cur.fetchone()[0] == 0:
        now = datetime.now().isoformat()
        for name in default_subjects:
            cur.execute(
                "INSERT OR IGNORE INTO subjects (name, created_at, created_by) VALUES (?, ?, NULL)",
                (name, now),
            )

    conn.commit()
    conn.close()


# ---------------- الملفات ----------------

def add_file(subject, content_type, title, file_id, file_name, uploaded_by, file_size=None):
    conn = _connect()
    conn.execute(
        "INSERT INTO files (subject, content_type, title, file_id, file_name, uploaded_by, uploaded_at, file_size, downloads) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (subject, content_type, title, file_id, file_name, uploaded_by, datetime.now().isoformat(), file_size),
    )
    conn.commit()
    conn.close()


def get_files_by_subject(subject, offset=0, limit=1000):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM files WHERE subject = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (subject, limit, offset),
    ).fetchall()
    conn.close()
    return rows


def count_files_by_subject(subject):
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM files WHERE subject = ?", (subject,)).fetchone()[0]
    conn.close()
    return n


def search_files(keyword, offset=0, limit=1000):
    conn = _connect()
    like = f"%{keyword}%"
    rows = conn.execute(
        "SELECT * FROM files WHERE title LIKE ? OR file_name LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (like, like, limit, offset),
    ).fetchall()
    conn.close()
    return rows


def count_search_results(keyword):
    conn = _connect()
    like = f"%{keyword}%"
    n = conn.execute(
        "SELECT COUNT(*) FROM files WHERE title LIKE ? OR file_name LIKE ?", (like, like)
    ).fetchone()[0]
    conn.close()
    return n


def get_file_by_id(file_pk):
    conn = _connect()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_pk,)).fetchone()
    conn.close()
    return row


def delete_file(file_pk):
    conn = _connect()
    conn.execute("DELETE FROM files WHERE id = ?", (file_pk,))
    conn.commit()
    conn.close()


def increment_download(file_pk):
    conn = _connect()
    conn.execute("UPDATE files SET downloads = COALESCE(downloads, 0) + 1 WHERE id = ?", (file_pk,))
    conn.commit()
    conn.close()


def get_all_files():
    conn = _connect()
    rows = conn.execute("SELECT * FROM files ORDER BY subject, id DESC").fetchall()
    conn.close()
    return rows


# ---------------- المواد ----------------

def get_subjects():
    conn = _connect()
    rows = conn.execute("SELECT name FROM subjects ORDER BY id ASC").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_subject(name, created_by):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO subjects (name, created_at, created_by) VALUES (?, ?, ?)",
            (name, datetime.now().isoformat(), created_by),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_subject(name):
    conn = _connect()
    cur = conn.execute("DELETE FROM subjects WHERE name = ?", (name,))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


# ---------------- المستخدمين ----------------

def upsert_user(user_id, username, first_name, last_name):
    """يرجع True لو المستخدم جديد، False لو موجود من قبل."""
    conn = _connect()
    now = datetime.now().isoformat()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?, is_blocked=0 WHERE user_id=?",
            (username, first_name, last_name, now, user_id),
        )
        conn.commit()
        conn.close()
        return False
    conn.execute(
        "INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_seen, is_blocked) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (user_id, username, first_name, last_name, now, now),
    )
    conn.commit()
    conn.close()
    return True


def mark_user_blocked(user_id):
    conn = _connect()
    conn.execute(
        "UPDATE users SET is_blocked = 1, blocked_at = ? WHERE user_id = ?",
        (datetime.now().isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def mark_user_active(user_id):
    conn = _connect()
    conn.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_users(offset=0, limit=1000):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    conn.close()
    return rows


def count_users(active_only=False):
    conn = _connect()
    if active_only:
        n = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 0").fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n


def get_active_user_ids():
    conn = _connect()
    rows = conn.execute("SELECT user_id FROM users WHERE is_blocked = 0").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


# ---------------- المشرفين ----------------

def is_admin_in_db(user_id):
    conn = _connect()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def add_admin(user_id, added_by):
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
        (user_id, added_by, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def remove_admin(user_id):
    conn = _connect()
    conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_admins():
    conn = _connect()
    rows = conn.execute("SELECT * FROM admins ORDER BY added_at ASC").fetchall()
    conn.close()
    return rows


# ---------------- سجل الأنشطة ----------------

def log_activity(user_id, username, action, details=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO activity_log (user_id, username, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, action, details, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_activity(offset=0, limit=1000):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    conn.close()
    return rows


def count_activity():
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    conn.close()
    return n


# ---------------- الإحصائيات ----------------

def get_stats():
    conn = _connect()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    blocked_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1").fetchone()[0]
    total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    total_downloads = conn.execute("SELECT COALESCE(SUM(downloads), 0) FROM files").fetchone()[0]
    total_admins = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    recent_uploads = conn.execute(
        "SELECT COUNT(*) FROM files WHERE uploaded_at >= ?", (week_ago,)
    ).fetchone()[0]

    files_per_subject = conn.execute(
        "SELECT subject, COUNT(*) as c FROM files GROUP BY subject ORDER BY c DESC"
    ).fetchall()

    top = conn.execute(
        "SELECT title, downloads FROM files WHERE downloads > 0 ORDER BY downloads DESC LIMIT 1"
    ).fetchone()

    conn.close()
    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "total_files": total_files,
        "total_downloads": total_downloads,
        "total_admins": total_admins,
        "recent_uploads": recent_uploads,
        "files_per_subject": [(r["subject"], r["c"]) for r in files_per_subject],
        "top_file": {"title": top["title"], "downloads": top["downloads"]} if top else None,
    }
