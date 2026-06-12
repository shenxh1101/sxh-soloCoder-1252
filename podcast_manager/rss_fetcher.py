import feedparser
from datetime import datetime
from typing import List, Dict, Tuple
from dateutil import parser as date_parser

from . import database


def parse_duration(duration_str: str) -> int:
    if not duration_str:
        return 0
    if isinstance(duration_str, int):
        return duration_str
    s = str(duration_str).strip()
    if not s:
        return 0
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        seconds = float(s)
        if seconds > 86400 * 10:
            return 0
        return int(seconds)
    except (ValueError, TypeError):
        return 0


def parse_pub_date(date_str: str) -> str:
    if not date_str:
        return datetime.now().isoformat()
    try:
        dt = date_parser.parse(date_str)
        return dt.isoformat()
    except (ValueError, TypeError):
        return datetime.now().isoformat()


def fetch_feed(feed_url: str) -> Tuple[Dict, List[Dict]]:
    feed = feedparser.parse(feed_url)

    if feed.bozo and not feed.entries:
        raise ValueError(f"无法解析播客源: {feed.bozo_exception}")

    podcast_info = {
        "title": feed.feed.get("title", "Unknown Podcast"),
        "description": feed.feed.get("description", ""),
        "image_url": "",
    }

    if hasattr(feed.feed, "image") and feed.feed.image:
        podcast_info["image_url"] = feed.feed.image.get("href", "")

    episodes = []
    for entry in feed.entries:
        audio_url = ""
        file_size = 0
        duration = 0

        if hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                if enc.get("type", "").startswith("audio/"):
                    audio_url = enc.get("href", "")
                    try:
                        file_size = int(enc.get("length", "0"))
                    except (ValueError, TypeError):
                        file_size = 0
                    break

        if not audio_url and hasattr(entry, "media_content"):
            for media in entry.media_content:
                if media.get("type", "").startswith("audio/"):
                    audio_url = media.get("url", "")
                    media_duration = media.get("duration", "0")
                    if media_duration:
                        duration = parse_duration(media_duration)
                    break

        itunes_duration = entry.get("itunes_duration", "")
        if itunes_duration:
            duration = parse_duration(itunes_duration)

        if duration == 0 and hasattr(entry, "itunes_duration"):
            duration = parse_duration(entry.itunes_duration)

        guid = entry.get("id", entry.get("guid", getattr(entry, "link", "")))

        episode = {
            "guid": str(guid),
            "title": entry.get("title", "Untitled Episode"),
            "description": entry.get("description", entry.get("summary", "")),
            "audio_url": audio_url,
            "duration": duration,
            "pub_date": parse_pub_date(entry.get("published", entry.get("updated", ""))),
        }
        episodes.append(episode)

    return podcast_info, episodes


def add_podcast_from_url(feed_url: str) -> Tuple[int, int]:
    podcast_info, episodes = fetch_feed(feed_url)

    existing = database.get_podcast_by_url(feed_url)
    if existing:
        podcast_id = existing["id"]
    else:
        podcast_id = database.add_podcast(
            title=podcast_info["title"],
            feed_url=feed_url,
            description=podcast_info["description"],
            image_url=podcast_info["image_url"],
        )

    new_count = 0
    for ep in episodes:
        ep_id = database.add_episode(
            podcast_id=podcast_id,
            guid=ep["guid"],
            title=ep["title"],
            description=ep["description"],
            audio_url=ep["audio_url"],
            duration=ep["duration"],
            pub_date=ep["pub_date"],
        )
        if ep_id is not None:
            new_count += 1

    database.update_podcast_last_updated(podcast_id)

    return podcast_id, new_count


def refresh_podcast(podcast_id: int) -> int:
    podcast = database.get_podcast_by_id(podcast_id)
    if not podcast:
        raise ValueError(f"播客 ID {podcast_id} 不存在")

    _, episodes = fetch_feed(podcast["feed_url"])

    new_count = 0
    for ep in episodes:
        ep_id = database.add_episode(
            podcast_id=podcast_id,
            guid=ep["guid"],
            title=ep["title"],
            description=ep["description"],
            audio_url=ep["audio_url"],
            duration=ep["duration"],
            pub_date=ep["pub_date"],
        )
        if ep_id is not None:
            new_count += 1

    database.update_podcast_last_updated(podcast_id)
    return new_count


def refresh_all_podcasts() -> Tuple[int, int, List[Tuple[str, str, bool]]]:
    podcasts = database.get_all_podcasts()
    total_new = 0
    success_count = 0
    results = []

    for podcast in podcasts:
        try:
            new_count = refresh_podcast(podcast["id"])
            total_new += new_count
            success_count += 1
            results.append((podcast["title"], podcast["feed_url"], True))
        except Exception as e:
            results.append((podcast["title"], podcast["feed_url"], False))

    return success_count, total_new, results
