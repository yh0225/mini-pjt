"""Download raw wikitext from Wikivoyage, respecting robots.txt.

Pages are fetched via `/wiki/<title>?action=raw`, which robots.txt allows on both
en and ko Wikivoyage (the `/w/api.php` endpoint is disallowed on en).
Results are cached under data/raw/<lang>/ so re-runs do not hit the network.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import quote

import httpx

from travel_agent.config import CRAWL_DELAY_SECONDS, RAW_DIR, USER_AGENT

log = logging.getLogger(__name__)

REDIRECT_RE = re.compile(r"^\s*#(?:REDIRECT|넘겨주기)\s*\[\[([^\]|#]+)", re.IGNORECASE)
MAX_REDIRECTS = 3


def cache_path(lang: str, title: str) -> Path:
    safe = title.replace("/", "__").replace(" ", "_")
    return RAW_DIR / lang / f"{safe}.wiki"


class WikivoyageCrawler:
    def __init__(self, delay: float = CRAWL_DELAY_SECONDS) -> None:
        self.delay = delay
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request = 0.0

    def close(self) -> None:
        self.client.close()

    def _base(self, lang: str) -> str:
        return f"https://{lang}.wikivoyage.org"

    def _robots_for(self, lang: str) -> urllib.robotparser.RobotFileParser:
        if lang not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            resp = self._get(f"{self._base(lang)}/robots.txt")
            rp.parse(resp.text.splitlines())
            self._robots[lang] = rp
        return self._robots[lang]

    def _get(self, url: str) -> httpx.Response:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            return self.client.get(url)
        finally:
            self._last_request = time.monotonic()

    def fetch(self, lang: str, title: str, use_cache: bool = True) -> str | None:
        """Return wikitext for `title`, following redirects. None if missing/blocked."""
        path = cache_path(lang, title)
        if use_cache and path.exists():
            return path.read_text(encoding="utf-8")

        current = title
        for _ in range(MAX_REDIRECTS + 1):
            url = f"{self._base(lang)}/wiki/{quote(current.replace(' ', '_'))}?action=raw"
            if not self._robots_for(lang).can_fetch(USER_AGENT, url):
                log.warning("robots.txt disallows %s", url)
                return None
            resp = self._get(url)
            if resp.status_code != 200:
                log.warning("HTTP %s for %s:%s", resp.status_code, lang, current)
                return None
            match = REDIRECT_RE.match(resp.text)
            if not match:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resp.text, encoding="utf-8")
                return resp.text
            # ko Wikivoyage redirects use an incubator-style "Wy/ko/" prefix.
            current = match.group(1).strip().removeprefix("Wy/ko/")
            log.info("redirect %s:%s -> %s", lang, title, current)

        log.warning("Too many redirects for %s:%s", lang, title)
        return None
