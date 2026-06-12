import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple


DB_PATH = os.path.join(os.path.expanduser("~"), ".podcast_manager", "podcasts.db")


def get_db_path() -> str:
    return DB_PATH


def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            feed_url TEXT UNIQUE NOT NULL,
            description TEXT,
            image_url TEXT,
            last_updated TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            podcast_id INTEGER NOT NULL,
            guid TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            audio_url TEXT NOT NULL,
            duration INTEGER,
            pub_date TIMESTAMP,
            is_listened INTEGER DEFAULT 0,
            progress INTEGER DEFAULT 0,
            play_count INTEGER DEFAULT 0,
            last_played TIMESTAMP,
            skip_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            FOREIGN KEY (podcast_id) REFERENCES podcasts(id),
            UNIQUE(podcast_id, guid)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS play_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER NOT NULL,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            start_position INTEGER DEFAULT 0,
            end_position INTEGER DEFAULT 0,
            duration_listened INTEGER DEFAULT 0,
            was_skipped INTEGER DEFAULT 0,
            was_completed INTEGER DEFAULT 0,
            FOREIGN KEY (episode_id) REFERENCES episodes(id)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_pub_date ON episodes(pub_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_episode_id ON play_sessions(episode_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON play_sessions(start_time)")

    conn.commit()
    conn.close()


def add_podcast(title: str, feed_url: str, description: str = "", image_url: str = "") -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO podcasts (title, feed_url, description, image_url) VALUES (?, ?, ?, ?)",
            (title, feed_url, description, image_url)
        )
        conn.commit()
        podcast_id = cursor.lastrowid
        return podcast_id
    except sqlite3.IntegrityError:
        cursor.execute("SELECT id FROM podcasts WHERE feed_url = ?", (feed_url,))
        row = cursor.fetchone()
        return row[0] if row else -1
    finally:
        conn.close()


def get_podcast_by_id(podcast_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_podcast_by_url(feed_url: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM podcasts WHERE feed_url = ?", (feed_url,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_podcasts() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM podcasts ORDER BY title")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_podcast(podcast_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM play_sessions WHERE episode_id IN (SELECT id FROM episodes WHERE podcast_id = ?)", (podcast_id,))
    cursor.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))
    cursor.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
    conn.commit()
    conn.close()


def add_episode(podcast_id: int, guid: str, title: str, description: str,
                audio_url: str, duration: int, pub_date: str) -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO episodes (podcast_id, guid, title, description, audio_url, duration, pub_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (podcast_id, guid, title, description, audio_url, duration, pub_date))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def update_podcast_last_updated(podcast_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE podcasts SET last_updated = ? WHERE id = ?",
        (datetime.now().isoformat(), podcast_id)
    )
    conn.commit()
    conn.close()


def get_episodes_by_podcast(podcast_id: int, only_unplayed: bool = False,
                            limit: int = 0) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM episodes WHERE podcast_id = ?"
    params = [podcast_id]

    if only_unplayed:
        query += " AND is_listened = 0"

    query += " ORDER BY pub_date DESC"

    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_episode_by_id(episode_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def mark_episode_listened(episode_id: int, listened: bool = True) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE episodes SET is_listened = ? WHERE id = ?",
        (1 if listened else 0, episode_id)
    )
    conn.commit()
    conn.close()


def update_episode_progress(episode_id: int, progress: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE episodes SET progress = ?, last_played = ? WHERE id = ?",
        (progress, datetime.now().isoformat(), episode_id)
    )
    conn.commit()
    conn.close()


def increment_play_count(episode_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE episodes SET play_count = play_count + 1 WHERE id = ?",
        (episode_id,)
    )
    conn.commit()
    conn.close()


def start_play_session(episode_id: int, start_position: int = 0) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO play_sessions (episode_id, start_position, start_time)
        VALUES (?, ?, ?)
    """, (episode_id, start_position, datetime.now().isoformat()))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


def end_play_session(session_id: int, end_position: int, duration_listened: int,
                     was_skipped: bool = False, was_completed: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE play_sessions
        SET end_time = ?, end_position = ?, duration_listened = ?,
            was_skipped = ?, was_completed = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), end_position, duration_listened,
          1 if was_skipped else 0, 1 if was_completed else 0, session_id))

    cursor.execute("SELECT episode_id FROM play_sessions WHERE id = ?", (session_id,))
    row = cursor.fetchone()
    if row:
        episode_id = row[0]
        if was_skipped:
            cursor.execute(
                "UPDATE episodes SET skip_count = skip_count + 1 WHERE id = ?",
                (episode_id,)
            )
        if was_completed:
            cursor.execute(
                "UPDATE episodes SET completed_count = completed_count + 1, is_listened = 1 WHERE id = ?",
                (episode_id,)
            )

    conn.commit()
    conn.close()


def get_podcast_skip_rate(podcast_id: int) -> float:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN ps.was_skipped = 1 THEN 1 ELSE 0 END), 0) as skipped,
            COALESCE(COUNT(ps.id), 0) as total
        FROM play_sessions ps
        JOIN episodes e ON ps.episode_id = e.id
        WHERE e.podcast_id = ? AND ps.end_time IS NOT NULL
    """, (podcast_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[1] > 0:
        return row[0] / row[1]
    return 0.0


def get_weekly_listen_duration() -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    week_start = datetime.now() - timedelta(days=7)
    cursor.execute("""
        SELECT COALESCE(SUM(duration_listened), 0)
        FROM play_sessions
        WHERE start_time >= ?
    """, (week_start.isoformat(),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] else 0


def get_total_listen_duration() -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(SUM(duration_listened), 0) FROM play_sessions")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] else 0


def get_unplayed_episodes_count(podcast_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM episodes WHERE podcast_id = ? AND is_listened = 0",
        (podcast_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def get_podcast_with_episode_count() -> List[Tuple[Dict, int, int]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.*,
               COUNT(e.id) as total_episodes,
               SUM(CASE WHEN e.is_listened = 0 THEN 1 ELSE 0 END) as unplayed_count
        FROM podcasts p
        LEFT JOIN episodes e ON p.id = e.podcast_id
        GROUP BY p.id
        ORDER BY p.title
    """)
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        total = d.pop('total_episodes', 0)
        unplayed = d.pop('unplayed_count', 0) or 0
        result.append((d, total, unplayed))
    return result
