import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "/data/posts.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS posts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  topic         TEXT,
  source_url    TEXT,
  title         TEXT,
  post_text     TEXT,
  image_prompt  TEXT,
  image_path    TEXT,
  seed          INTEGER,
  status        TEXT DEFAULT 'draft',
  linkedin_urn  TEXT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP
);
"""


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.execute(_CREATE_TABLE)


def insert_post(
    *,
    topic: str,
    source_url: str,
    title: str,
    post_text: str = "",
    image_prompt: str = "",
    image_path: str = "",
    seed: int = 0,
    status: str = "draft",
) -> int:
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO posts
               (topic, source_url, title, post_text, image_prompt, image_path, seed, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (topic, source_url, title, post_text, image_prompt, image_path, seed, status),
        )
        return cur.lastrowid


def list_posts(status: Optional[str] = None) -> list[dict]:
    with _conn() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM posts WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM posts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_post(post_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None


def source_url_exists(source_url: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM posts WHERE source_url = ?", (source_url,)
        ).fetchone()
        return row is not None


def update_post(post_id: int, **fields) -> bool:
    if not fields:
        return False
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [post_id]
    with _conn() as con:
        con.execute(f"UPDATE posts SET {set_clause} WHERE id = ?", values)
    return True
