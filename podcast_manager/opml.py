import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple
from datetime import datetime

from . import database


def import_opml(file_path: str) -> Tuple[int, int]:
    tree = ET.parse(file_path)
    root = tree.getroot()

    feeds: List[Dict] = []

    for outline in root.iter("outline"):
        xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
        if xml_url:
            title = outline.get("title") or outline.get("text", "Unknown")
            feeds.append({
                "title": title,
                "feed_url": xml_url,
            })

    imported = 0
    skipped = 0

    for feed in feeds:
        existing = database.get_podcast_by_url(feed["feed_url"])
        if existing:
            skipped += 1
        else:
            database.add_podcast(
                title=feed["title"],
                feed_url=feed["feed_url"],
                description="",
                image_url="",
            )
            imported += 1

    return imported, skipped


def export_opml(file_path: str) -> int:
    podcasts = database.get_all_podcasts()

    opml = ET.Element("opml", version="2.0")

    head = ET.SubElement(opml, "head")
    title = ET.SubElement(head, "title")
    title.text = "我的播客订阅"
    date_created = ET.SubElement(head, "dateCreated")
    date_created.text = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")

    body = ET.SubElement(opml, "body")

    for podcast in podcasts:
        outline = ET.SubElement(body, "outline", {
            "type": "rss",
            "text": podcast["title"],
            "title": podcast["title"],
            "xmlUrl": podcast["feed_url"],
            "htmlUrl": "",
        })

    tree = ET.ElementTree(opml)
    ET.indent(tree, space="  ", level=0)
    tree.write(file_path, encoding="utf-8", xml_declaration=True)

    return len(podcasts)
