import feedparser
from datetime import datetime
from typing import List, Dict, Tuple
from dateutil import parser as date_parser

from . import database


def parse_duration(duration_str) -> int:
    if not duration_str:
        return 0
    if isinstance(duration_str, (int, float)):
        seconds = int(duration_str)
        if 0 < seconds <= 86400 * 10:
            return seconds
        return 0
    s = str(duration_str).strip()
    if not s:
        return 0
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 4:
                return int(parts[0]) * 86400 + int(parts[1]) * 3600 + int(parts[2]) * 60 + int(parts[3])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        seconds = float(s)
        if seconds <= 0:
            return 0
        if seconds > 86400 * 10:
            return 0
        return int(seconds)
    except (ValueError, TypeError):
        return 0


def extract_duration_from_entry(entry) -> int:
    duration = 0

    for attr in ("itunes_duration", "duration"):
        val = entry.get(attr, "")
        if val:
            d = parse_duration(val)
            if d > 0:
                return d

    if hasattr(entry, "itunes_duration"):
        d = parse_duration(entry.itunes_duration)
        if d > 0:
            return d

    if hasattr(entry, "duration"):
        d = parse_duration(entry.duration)
        if d > 0:
            return d

    if hasattr(entry, "media_content"):
        for media in entry.media_content:
            media_dur = media.get("duration", "")
            if media_dur:
                d = parse_duration(media_dur)
                if d > 0:
                    return d

    if hasattr(entry, "enclosures"):
        for enc in entry.enclosures:
            enc_dur = enc.get("duration", "")
            if enc_dur:
                d = parse_duration(enc_dur)
                if d > 0:
                    return d

    tag = entry.get("tags", [])
    for t in (tag if isinstance(tag, list) else []):
        attrs = t.get("attrs", {}) if isinstance(t, dict) else {}
        for key in ("duration", "itunes:duration"):
            val = attrs.get(key, "")
            if val:
                d = parse_duration(val)
                if d > 0:
                    return d

    return duration


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

        if hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                enc_type = enc.get("type", "")
                if enc_type.startswith("audio/") or enc_type == "":
                    href = enc.get("href", "")
                    if href and (href.endswith(".mp3") or href.endswith(".m4a") or
                                 href.endswith(".ogg") or href.endswith(".wav") or
                                 enc_type.startswith("audio/") or
                                 "audio" in href.lower()):
                        audio_url = href
                        break

        if not audio_url and hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                href = enc.get("href", "")
                if href:
                    audio_url = href
                    break

        if not audio_url and hasattr(entry, "media_content"):
            for media in entry.media_content:
                media_type = media.get("type", "")
                if media_type.startswith("audio/"):
                    audio_url = media.get("url", "")
                    break

        if not audio_url and hasattr(entry, "media_content"):
            for media in entry.media_content:
                url = media.get("url", "")
                if url:
                    audio_url = url
                    break

        if not audio_url and hasattr(entry, "link") and entry.link:
            link = entry.link
            if any(link.endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav", ".aac")):
                audio_url = link

        duration = extract_duration_from_entry(entry)

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


def refresh_podcast(podcast_id: int) -> Tuple[int, List[str], int]:
    podcast = database.get_podcast_by_id(podcast_id)
    if not podcast:
        raise ValueError(f"播客 ID {podcast_id} 不存在")

    _, episodes = fetch_feed(podcast["feed_url"])

    new_titles = []
    calibrated_count = 0
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
            new_titles.append(ep["title"])
        else:
            calibrated_id = database.update_episode_metadata(
                podcast_id=podcast_id,
                guid=ep["guid"],
                title=ep["title"],
                description=ep["description"],
                audio_url=ep["audio_url"],
                duration=ep["duration"],
                pub_date=ep["pub_date"],
            )
            if calibrated_id is not None:
                calibrated_count += 1

    database.update_podcast_last_updated(podcast_id)
    return len(new_titles), new_titles, calibrated_count


def refresh_all_podcasts() -> Tuple[int, int, List[Dict]]:
    podcasts = database.get_all_podcasts()
    total_new = 0
    total_calibrated = 0
    success_count = 0
    results = []

    for podcast in podcasts:
        try:
            new_count, new_titles, calibrated = refresh_podcast(podcast["id"])
            total_new += new_count
            total_calibrated += calibrated
            success_count += 1
            results.append({
                "title": podcast["title"],
                "feed_url": podcast["feed_url"],
                "success": True,
                "new_count": new_count,
                "new_titles": new_titles,
                "calibrated": calibrated,
                "error": None,
            })
        except Exception as e:
            results.append({
                "title": podcast["title"],
                "feed_url": podcast["feed_url"],
                "success": False,
                "new_count": 0,
                "new_titles": [],
                "calibrated": 0,
                "error": str(e),
            })

    return success_count, total_new, total_calibrated, results
