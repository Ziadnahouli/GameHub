import base64
import re
import logging
import urllib.parse
import json

import requests
from bs4 import BeautifulSoup

from .base_provider import BaseProvider, make_content_id

logger = logging.getLogger("GameHub_Movies")

DEFAULT_FASEL_DOMAINS = ["faselhd.club", "faselhd.center"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

class FaselHDProvider(BaseProvider):
    def __init__(self, domains=None, user_agent=None):
        self._domains = list(domains) if domains else list(DEFAULT_FASEL_DOMAINS)
        self._headers = {
            "User-Agent": (user_agent or DEFAULT_USER_AGENT).strip(),
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://google.com/",
        }
        self._session = requests.Session()
        self._session.headers.update(self._headers)

    @property
    def name(self) -> str:
        return "faselhd"

    def _validate_html(self, text: str) -> None:
        if "<title>Just a moment...</title>" in text or "id=\"cf-wrapper\"" in text:
            raise Exception("Blocked by anti-bot/Cloudflare")
        if len(text) < 500:
            raise Exception("Invalid / empty page")

    def _item_type(self, title_or_url: str) -> str:
        lower = (title_or_url or "").lower()
        if "مسلسل" in lower or "seri" in lower or "season" in lower or "episode" in lower or "حلقة" in lower or "/episodes/" in lower:
            return "Series"
        return "Movie"

    def _parse_list_page(self, html_text: str) -> list:
        soup = BeautifulSoup(html_text, "html.parser")
        items = soup.select(".postDiv")
        results = []
        for item in items[:24]:
            a = item.select_one("a[href]")
            if not a:
                continue
            href = a.get("href")
            
            title_el = item.select_one(".postInner .h1")
            title = title_el.get_text(strip=True) if title_el else a.get("title", "")
            
            # Remove redundant prefixes
            title = re.sub(r'^(?:مشاهدة|تحميل)\s*(?:فيلم|مسلسل|انمي|برنامج)?\s*', '', title).strip()

            img = item.select_one("img.img-fluid")
            poster = img.get("data-src") or img.get("src") or "" if img else ""
            
            # Additional details usually hidden in hover or outside
            year = ""
            m_year = re.search(r'\b(19\d{2}|20\d{2})\b', title)
            if m_year:
                year = m_year.group(1)

            ctype = self._item_type(href + " " + title)
            internal_id = base64.urlsafe_b64encode(href.encode()).decode()
            
            results.append({
                "id": make_content_id("faselhd", ctype.lower(), internal_id),
                "title": title,
                "poster": poster,
                "type": ctype,
                "year": year,
                "rating": "",
                "language": "Mixed",
                "source": "FaselHD",
            })
        return results

    def search(self, query: str) -> list:
        if not self._domains or not (query or "").strip():
            return []
        q = urllib.parse.quote(query)
        for domain in self._domains:
            url = f"https://{domain}/?s={q}"
            try:
                res = self._session.get(url, timeout=10, verify=False)
                if not res.ok:
                    continue
                self._validate_html(res.text)
                results = self._parse_list_page(res.text)
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[FaselHD] Search failed on {domain}: {e}")
        return []

    def get_trending(self) -> list:
        return self.get_category_content("/")

    def get_category_content(self, category_path: str) -> list:
        for domain in self._domains[:2]:
            url = f"https://{domain}{category_path}"
            try:
                res = self._session.get(url, timeout=10, verify=False)
                if not res.ok:
                    continue
                self._validate_html(res.text)
                
                soup = BeautifulSoup(res.text, "html.parser")
                blocks = soup.select(".movies_series_display_home .postDiv")
                blocks.extend(soup.select(".slider .postDiv"))
                # Fallback to general listing if blocks are empty
                if not blocks:
                    blocks = soup.select(".postDiv")
                
                items_html = "".join([str(b) for b in blocks[:48]])
                if items_html:
                    return self._parse_list_page(items_html)
            except Exception as e:
                logger.warning(f"[FaselHD] Category {category_path} failed on {domain}: {e}")
        return []

    def get_details(self, content_id: str) -> dict | None:
        from .base_provider import parse_content_id
        provider, ctype, internal_id = parse_content_id(content_id)
        if provider != self.name or not internal_id:
            return None
            
        try:
            url = base64.urlsafe_b64decode(internal_id).decode()
        except Exception:
            return None
        
        # Override domain to a working one potentially
        parsed = urllib.parse.urlparse(url)
        for domain in self._domains:
            try:
                try_url = f"https://{domain}{parsed.path}{parsed.params}"
                if parsed.query:
                    try_url += "?" + parsed.query
                    
                res = self._session.get(try_url, timeout=10, verify=False)
                if res.ok:
                    self._validate_html(res.text)
                    return self._scrape_details(res.text, try_url)
            except Exception as e:
                logger.warning(f"[FaselHD] Details failed on {domain}: {e}")
        return None

    def _scrape_details(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        
        # Title
        title_el = soup.select_one("title")
        title = title_el.get_text(strip=True) if title_el else "Unknown Title"
        title = re.sub(r'^(?:مشاهدة|تحميل)\s*(?:فيلم|مسلسل|انمي|برنامج)?\s*', '', title)
        title = title.split("|")[0].strip()

        # Story
        story_el = soup.select_one(".singleDesc p")
        story = story_el.get_text(strip=True) if story_el else "No description available."
        
        # Meta info
        year = ""
        rating = ""
        poster = ""

        # Extract meta via IMDB / Date labels
        for row in soup.select(".info-list li"):
            text = row.get_text(strip=True)
            if "سنة الإصدار" in text or "Year" in text:
                m_year = re.search(r'\b(19\d{2}|20\d{2})\b', text)
                if m_year: year = m_year.group(1)
            elif "IMDb" in text or "التقييم" in text:
                m_rate = re.search(r'[\d.]+', text)
                if m_rate: rating = m_rate.group(0)

        # Poster
        img_el = soup.select_one(".movie-poster img, .img-col img")
        if img_el:
            poster = img_el.get("src") or ""

        ctype = self._item_type(url + " " + title)
        links = []
        
        # We handle 3 main cases in FaselHD: 
        # 1. Seasons / Episodes list
        for ep in soup.select(".epAll a, .seasonsAll a"):
            href = ep.get("href")
            if href:
                ep_title = ep.get_text(strip=True) or "Episode"
                internal_id = base64.urlsafe_b64encode(href.encode()).decode()
                links.append({
                    "quality": "",
                    "url": href,
                    "size": "",
                    "label": ep_title.strip(),
                    "type": "EPISODE",
                    "id": make_content_id("faselhd", "series", internal_id)
                })

        # 2. Iframe Stream Link (Direct Watch in FaselHD player)
        iframe_src = None
        iframe = soup.select_one("iframe[src*='video_player']")
        if iframe:
            iframe_src = iframe.get("src")
        
        if iframe_src:
            links.append({
                "quality": "HD",
                "url": iframe_src,
                "size": "",
                "label": "FaselHD Player (Browser)",
                # Important: GameHub opens "WATCH" links directly in a new tab
                "type": "WATCH" 
            })

            # Also provide it as a pseudo-download link that users can open
            links.append({
                "quality": "HD",
                "url": iframe_src,
                "size": "Stream",
                "label": "[Browser] Download / Stream Externally",
                "type": "BROWSER"
            })
            
        return {
            "title": title,
            "summary": story,
            "rating": rating,
            "year": year,
            "poster": poster,
            "type": ctype,
            "genres": [],
            "links": links,
        }
