"""
src/scraper/wam_rss_engine.py
──────────────────────────────
RSS-based scraper for WAM (wam.ae) — bypasses F5 bot protection entirely.

WAM publishes RSS feeds that are publicly accessible without JavaScript:
  https://www.wam.ae/en/rss/sports

No Playwright needed — uses simple HTTP requests via httpx.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
from loguru import logger

from src.core.config import Settings
from src.core.models import RawArticle

WAM_RSS_FEEDS = [
    {"name": "Sports", "url": "https://www.wam.ae/en/rss/sports"},
]

WAM_BASE = "https://www.wam.ae"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}


class WAMRSSScraper:
    """
    Lightweight RSS-based scraper for WAM sports news.
    Uses httpx (no browser needed) — much faster and reliable.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._seen_urls: set[str] = set()
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        logger.info("WAM RSS scraper started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
        logger.info("WAM RSS scraper stopped")

    async def __aenter__(self) -> "WAMRSSScraper":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    async def seed_seen_urls(self, known_urls: set[str]) -> None:
        self._seen_urls.update(known_urls)
        logger.info(f"Seeded {len(known_urls)} known article URLs into seen set")

    async def poll_subcategory(self, subcategory: dict) -> list[RawArticle]:
        name = subcategory["name"]
        url = subcategory["url"]

        logger.info(f"Polling WAM RSS: {name}", url=url)

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"Failed to fetch RSS feed {url}: {exc}")
            return []

        articles = self._parse_rss(resp.text, name)
        new_articles = [a for a in articles if a.url not in self._seen_urls]
        logger.info(f"{len(new_articles)} new articles in {name}")

        for a in new_articles:
            self._seen_urls.add(a.url)

        return new_articles

    def _parse_rss(self, xml_text: str, subcategory: str) -> list[RawArticle]:
        articles = []
        root = None

        # First attempt: strict stdlib parser (fastest)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning(f"Strict XML parse failed ({exc}), retrying with lxml recovery parser")
            try:
                from lxml import etree as lxml_etree
                # lxml's recover=True silently fixes common malformed XML
                parser = lxml_etree.XMLParser(recover=True, encoding="utf-8")
                lxml_root = lxml_etree.fromstring(xml_text.encode("utf-8"), parser)
                # Convert to string and re-parse with stdlib so downstream code
                # works with standard ET.Element objects
                recovered_xml = lxml_etree.tostring(lxml_root, encoding="unicode")
                root = ET.fromstring(recovered_xml)
                logger.info("RSS recovered via lxml fallback parser")
            except Exception as fallback_exc:
                logger.error(f"RSS fallback parse also failed: {fallback_exc}")
                return []

        if root is None:
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> found in RSS feed")
            return []

        for item in channel.findall("item"):
            try:
                article = self._parse_item(item, subcategory)
                if article:
                    articles.append(article)
            except Exception as exc:
                logger.warning(f"Failed to parse RSS item: {exc}")

        return articles

    def _parse_item(self, item: ET.Element, subcategory: str) -> Optional[RawArticle]:
        title = self._text(item, "title")
        url = self._text(item, "link")
        summary = self._text(item, "description")
        pub_date_str = self._text(item, "pubDate")

        if not title or not url:
            return None

        # Clean URL
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = urljoin(WAM_BASE, url)

        # Parse date
        publish_date: Optional[datetime] = None
        if pub_date_str:
            try:
                import email.utils
                parsed = email.utils.parsedate_to_datetime(pub_date_str)
                publish_date = parsed.replace(tzinfo=None)
            except Exception:
                pass

        # Image from enclosure or media:content
        image_url: Optional[str] = None
        enclosure = item.find("enclosure")
        if enclosure is not None:
            image_url = enclosure.get("url")

        if not image_url:
            # Try media:content
            media_ns = "{http://search.yahoo.com/mrss/}"
            media = item.find(f"{media_ns}content")
            if media is not None:
                image_url = media.get("url")

        # Clean summary (strip HTML tags)
        if summary:
            import re
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]

        return RawArticle(
            title=title.strip(),
            url=url.strip(),
            image_url=image_url,
            summary=summary,
            publish_date=publish_date,
            subcategory=subcategory,
        )

    @staticmethod
    def _text(element: ET.Element, tag: str) -> Optional[str]:
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return None