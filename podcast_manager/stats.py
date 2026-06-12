import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

from . import database
from .player import format_duration


def get_podcast_stats(podcast_id: int) -> Dict:
    conn = sqlite3.connect(database.get_db_path())
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM episodes WHERE podcast_id = ?", (podcast_id,))
    total_episodes = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM episodes WHERE podcast_id = ? AND is_listened = 1", (podcast_id,))
    listened_episodes = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(duration), 0) FROM episodes WHERE podcast_id = ?", (podcast_id,))
    total_duration = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(ps.duration_listened), 0)
        FROM play_sessions ps
        JOIN episodes e ON ps.episode_id = e.id
        WHERE e.podcast_id = ?
    """, (podcast_id,))
    total_listened = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN ps.was_skipped = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(COUNT(ps.id), 0)
        FROM play_sessions ps
        JOIN episodes e ON ps.episode_id = e.id
        WHERE e.podcast_id = ? AND ps.end_time IS NOT NULL
    """, (podcast_id,))
    skipped, total_sessions = cursor.fetchone()

    skip_rate = skipped / total_sessions if total_sessions > 0 else 0.0

    conn.close()

    return {
        "total_episodes": total_episodes,
        "listened_episodes": listened_episodes,
        "unplayed_episodes": total_episodes - listened_episodes,
        "total_duration": total_duration,
        "total_listened": total_listened,
        "skip_rate": skip_rate,
        "skipped_count": skipped,
        "play_sessions": total_sessions,
    }


def get_overall_stats() -> Dict:
    conn = sqlite3.connect(database.get_db_path())
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM podcasts")
    total_podcasts = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM episodes")
    total_episodes = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM episodes WHERE is_listened = 1")
    listened_episodes = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(duration_listened), 0) FROM play_sessions")
    total_listened = cursor.fetchone()[0]

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    cursor.execute("SELECT COALESCE(SUM(duration_listened), 0) FROM play_sessions WHERE start_time >= ?", (week_ago,))
    weekly_listened = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN was_skipped = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(COUNT(id), 0)
        FROM play_sessions
        WHERE end_time IS NOT NULL
    """)
    skipped, total_sessions = cursor.fetchone()

    overall_skip_rate = skipped / total_sessions if total_sessions > 0 else 0.0

    conn.close()

    return {
        "total_podcasts": total_podcasts,
        "total_episodes": total_episodes,
        "listened_episodes": listened_episodes,
        "unplayed_episodes": total_episodes - listened_episodes,
        "total_listened": total_listened,
        "weekly_listened": weekly_listened,
        "overall_skip_rate": overall_skip_rate,
        "total_sessions": total_sessions,
        "skipped_count": skipped,
    }


def get_daily_listen_history(days: int = 7) -> List[Tuple[str, int]]:
    conn = sqlite3.connect(database.get_db_path())
    cursor = conn.cursor()

    results = []
    for i in range(days - 1, -1, -1):
        day_start = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)).isoformat()
        day_end = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i - 1)).isoformat()

        cursor.execute("""
            SELECT COALESCE(SUM(duration_listened), 0)
            FROM play_sessions
            WHERE start_time >= ? AND start_time < ?
        """, (day_start, day_end))
        duration = cursor.fetchone()[0] or 0

        day_label = (datetime.now() - timedelta(days=i)).strftime("%m-%d")
        results.append((day_label, duration))

    conn.close()
    return results


def get_podcasts_ranked_by_skip_rate() -> List[Tuple[Dict, float, int]]:
    podcasts = database.get_all_podcasts()
    results = []

    for podcast in podcasts:
        skip_rate = database.get_podcast_skip_rate(podcast["id"])
        unplayed = database.get_unplayed_episodes_count(podcast["id"])
        results.append((podcast, skip_rate, unplayed))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
