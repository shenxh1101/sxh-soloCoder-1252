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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS queue_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            position INTEGER DEFAULT 0,
            FOREIGN KEY (episode_id) REFERENCES episodes(id)
        )
    """)

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


def search_episodes(keyword: str = "", status_filter: str = "all",
                    podcast_id: int = None, limit: int = 0) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT e.*, p.title as podcast_title
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE 1=1
    """
    params = []

    if keyword:
        query += " AND (e.title LIKE ? OR p.title LIKE ? OR e.description LIKE ?)"
        like_pattern = f"%{keyword}%"
        params.extend([like_pattern, like_pattern, like_pattern])

    if status_filter == "unplayed":
        query += " AND e.is_listened = 0 AND (e.progress = 0 OR e.progress IS NULL)"
    elif status_filter == "in_progress":
        query += " AND e.is_listened = 0 AND e.progress > 0"
    elif status_filter == "listened":
        query += " AND e.is_listened = 1"

    if podcast_id:
        query += " AND e.podcast_id = ?"
        params.append(podcast_id)

    query += " ORDER BY e.pub_date DESC"

    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_episode_progress(episode_id: int, progress_seconds: int) -> Tuple[int, bool, bool]:
    episode = get_episode_by_id(episode_id)
    if not episode:
        return 0, False, False

    total_duration = episode["duration"] or 0
    old_progress = episode["progress"] or 0
    old_is_listened = episode["is_listened"]
    progress_seconds = max(0, int(progress_seconds))
    if total_duration > 0:
        progress_seconds = min(progress_seconds, total_duration)

    was_completed = False
    was_reverted = False
    completion_threshold = 0.9

    if total_duration > 0 and progress_seconds >= total_duration * completion_threshold:
        was_completed = True

    if old_is_listened and not was_completed and progress_seconds < old_progress:
        was_reverted = True

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if was_completed:
        cursor.execute("""
            UPDATE episodes
            SET progress = ?, is_listened = 1, last_played = ?
            WHERE id = ?
        """, (progress_seconds, datetime.now().isoformat(), episode_id))
    elif was_reverted:
        cursor.execute("""
            UPDATE episodes
            SET progress = ?, is_listened = 0, last_played = ?
            WHERE id = ?
        """, (progress_seconds, datetime.now().isoformat(), episode_id))
    else:
        cursor.execute("""
            UPDATE episodes
            SET progress = ?, last_played = ?
            WHERE id = ?
        """, (progress_seconds, datetime.now().isoformat(), episode_id))

    conn.commit()
    conn.close()

    return progress_seconds, was_completed, was_reverted


def add_to_queue(episode_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(position) FROM queue_items")
        row = cursor.fetchone()
        next_pos = (row[0] or 0) + 1

        cursor.execute("""
            INSERT OR IGNORE INTO queue_items (episode_id, position)
            VALUES (?, ?)
        """, (episode_id, next_pos))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def remove_from_queue(episode_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM queue_items WHERE episode_id = ?", (episode_id,))
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0


def get_queue() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT q.*, e.title as episode_title, e.duration, e.progress, e.is_listened,
               e.audio_url, p.id as podcast_id, p.title as podcast_title
        FROM queue_items q
        JOIN episodes e ON q.episode_id = e.id
        JOIN podcasts p ON e.podcast_id = p.id
        ORDER BY q.position ASC, q.added_at ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_queue_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM queue_items")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def is_in_queue(episode_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM queue_items WHERE episode_id = ?", (episode_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def pop_queue() -> Optional[Dict]:
    queue = get_queue()
    if not queue:
        return None
    first = queue[0]
    remove_from_queue(first["episode_id"])
    return first


def update_episode_duration(episode_id: int, duration: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE episodes SET duration = ? WHERE id = ?",
        (duration, episode_id)
    )
    conn.commit()
    conn.close()


def batch_add_to_queue(episode_ids: List[int]) -> int:
    added = 0
    for eid in episode_ids:
        if add_to_queue(eid):
            added += 1
    return added


def move_queue_to_top(episode_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT position FROM queue_items WHERE episode_id = ?", (episode_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    current_pos = row[0]
    cursor.execute("SELECT MIN(position) FROM queue_items")
    min_pos = cursor.fetchone()[0] or 0

    new_pos = min_pos - 1
    cursor.execute(
        "UPDATE queue_items SET position = ? WHERE episode_id = ?",
        (new_pos, episode_id)
    )
    conn.commit()
    conn.close()
    return True


def move_queue_item(episode_id: int, direction: str) -> bool:
    items = get_queue()
    idx = None
    for i, item in enumerate(items):
        if item["episode_id"] == episode_id:
            idx = i
            break

    if idx is None:
        return False

    if direction == "up" and idx > 0:
        swap_with = items[idx - 1]["episode_id"]
    elif direction == "down" and idx < len(items) - 1:
        swap_with = items[idx + 1]["episode_id"]
    else:
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT position FROM queue_items WHERE episode_id = ?", (episode_id,))
    pos_a = cursor.fetchone()[0]
    cursor.execute("SELECT position FROM queue_items WHERE episode_id = ?", (swap_with,))
    pos_b = cursor.fetchone()[0]

    cursor.execute("UPDATE queue_items SET position = ? WHERE episode_id = ?", (pos_b, episode_id))
    cursor.execute("UPDATE queue_items SET position = ? WHERE episode_id = ?", (pos_a, swap_with))

    conn.commit()
    conn.close()
    return True


def skip_queue_current() -> Optional[Dict]:
    items = get_queue()
    if not items:
        return None
    first = items[0]
    remove_from_queue(first["episode_id"])
    if len(items) > 1:
        return items[1]
    return None


def get_recent_episodes(days: int = 7, only_unplayed: bool = True) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).isoformat()

    query = """
        SELECT e.*, p.title as podcast_title
        FROM episodes e
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE e.pub_date >= ?
    """
    params = [since]

    if only_unplayed:
        query += " AND e.is_listened = 0"

    query += " ORDER BY e.pub_date DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def find_episode_by_query(query: str) -> List[Dict]:
    return search_episodes(keyword=query, status_filter="all", limit=20)

