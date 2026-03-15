import base64
import re
import logging
from urllib.parse import urlparse, quote

import requests
from bs4 import BeautifulSoup

from .base_provider import BaseProvider, make_content_id

logger = logging.getLogger("GameHub_Movies")

DEFAULT_WECIMA_DOMAINS = ["wecima.io", "wecima.show", "wecima.tube", "mycima.biz"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

class WeCimaProvider(BaseProvider):
    def __init__(self, domains=None, user_agent=None):
        self._domains = list(domains) if domains else list(DEFAULT_WECIMA_DOMAINS)
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
        return "wecima"

    def _validate_html(self, text: str) -> None:
        if "Cloudflare" in text or "captcha" in text.lower() or "Just a moment..." in text or "Redirecting" in text:
            raise Exception("Blocked by anti-bot/Cloudflare")
        if len(text) < 1000:
            raise Exception("Invalid / empty page")

    def _item_type(self, title_or_url: str) -> str:
        lower = (title_or_url or "").lower()
        if "مسلسل" in lower or "seri" in lower or "season" in lower or "episode" in lower or "حلقة" in lower:
            return "Series"
        return "Movie"

    def _parse_list_page(self, html_text: str) -> list:
        soup = BeautifulSoup(html_text, "html.parser")
        items = soup.select(".GridItem, .Thumb--GridItem")
        results = []
        for item in items[:24]:
            a = item.select_one("a[href]")
            if not a:
                continue
            href = a.get("href")
            
            # Wecima puts title both in 'title' attr and in strong.
            title_el = item.select_one("strong, .hasyear")
            title = title_el.get_text(strip=True) if title_el else a.get("title", "")
            
            # Remove "مشاهدة فيلم" / "مشاهدة مسلسل"
            title = re.sub(r'^(?:مشاهدة|تحميل)\s*(?:فيلم|مسلسل|انمي|برنامج)?\s*', '', title).strip()

            img = item.select_one("span.BG--GridItem, img")
            poster = ""
            if img:
                if img.name == "img":
                    poster = img.get("data-lazy-src") or img.get("src") or ""
                else:
                    bg = img.get("data-lazy-style") or img.get("style", "")
                    m = re.search(r'url\((["\']?)([^)]+)\1\)', bg)
                    if m:
                        poster = m.group(2)
            
            year_el = item.select_one(".year")
            year = year_el.get_text(strip=True) if year_el else ""
            
            ctype = self._item_type(href + " " + title)
            internal_id = base64.urlsafe_b64encode(href.encode()).decode()
            
            results.append({
                "id": make_content_id("wecima", ctype.lower(), internal_id),
                "title": title,
                "poster": poster,
                "type": ctype,
                "year": year,
                "rating": "",
                "language": "Mixed",
                "source": "Direct Server",
            })
        return results

    def search(self, query: str) -> list:
        if not self._domains or not (query or "").strip():
            return []
        q = quote(query)
        for domain in self._domains:
            url = f"https://{domain}/search/{q}/"
            try:
                res = self._session.get(url, timeout=10, verify=False)
                if not res.ok:
                    continue
                self._validate_html(res.text)
                results = self._parse_list_page(res.text)
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[WeCima] Search failed on {domain}: {e}")
        return []

    def get_trending(self) -> list:
        for domain in self._domains[:2]:
            url = f"https://{domain}/"
            try:
                res = self._session.get(url, timeout=10, verify=False)
                if not res.ok:
                    continue
                self._validate_html(res.text)
                results = self._parse_list_page(res.text)
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[WeCima] Trending failed on {domain}: {e}")
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
        parsed = urlparse(url)
        for domain in self._domains:
            try:
                try_url = f"https://{domain}{parsed.path}"
                res = self._session.get(try_url, timeout=10, verify=False)
                if res.ok:
                    self._validate_html(res.text)
                    return self._scrape_details(res.text, try_url)
            except Exception as e:
                logger.warning(f"[WeCima] Details failed on {domain}: {e}")
        return None

    def _scrape_details(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        
        title_el = soup.select_one("h1, .Title--Content--Single-begin h1")
        title = title_el.get_text(strip=True) if title_el else "Unknown Title"
        title = re.sub(r'^(?:مشاهدة|تحميل)\s*(?:فيلم|مسلسل|انمي|برنامج)?\s*', '', title).strip()

        story_el = soup.select_one(".StoryMovieContent, .Story")
        story = story_el.get_text(strip=True) if story_el else "No description available."
        
        year = ""
        year_el = soup.select_one("a[href*='year']")
        if year_el:
            year = year_el.get_text(strip=True)
            
        rating = ""
        rating_el = soup.select_one("a[href*='imdb']")
        if rating_el:
            rating = rating_el.get_text(strip=True)
            
        img_el = soup.select_one("wecima.show img, .Main--Thumb--single img, .Thumb--GridItem img")
        poster = ""
        if img_el:
            poster = img_el.get("data-lazy-src") or img_el.get("src") or ""

        ctype = self._item_type(url + " " + title)
        links = []
        
        # WeCima Watch Servers
        for watch in soup.select("ul.List--Watch--Wecima--Single li, .WatchServersList li"):
            v_url = ""
            btn = watch.select_one("btn")
            if btn and btn.get("data-url"):
                v_url = btn.get("data-url")
            a_tag = watch.select_one("a")
            if a_tag and a_tag.get("href"):
                v_url = a_tag.get("href")
                
            if v_url:
                label_el = watch.select_one("strong")
                label = label_el.get_text(strip=True) if label_el else "Watch Server"
                links.append({
                    "quality": "HD",
                    "url": v_url,
                    "size": "",
                    "label": label,
                    "type": "STREAM_RESOLVE" if "wecima" in v_url else "STREAM"
                })

        # WeCima Download Links
        for dl in soup.select("ul.List--Download--Wecima--Single li, .DownloadServersList li"):
            a_tag = dl.select_one("a")
            if a_tag and a_tag.get("href"):
                v_url = a_tag.get("href")
                label_el = dl.select_one("strong, .resolution")
                label = label_el.get_text(strip=True) if label_el else "Download"
                quality = "HD"
                qm = re.search(r'(1080|720|480|360|240|4k)', label)
                if qm:
                    quality = qm.group(1) + ("p" if qm.group(1).isdigit() else "")
                    
                size_el = dl.select_one("em, .size")
                size = size_el.get_text(strip=True) if size_el else ""
                
                links.append({
                    "quality": quality.upper(),
                    "url": v_url,
                    "size": size,
                    "label": f"[Download] {label}",
                    "type": "STREAM_RESOLVE"
                })
                
        # Episodes (if series)
        for ep in soup.select(".Episodes--Seasons--Episodes a"):
            href = ep.get("href")
            if href:
                ep_title = ep.get_text(strip=True)
                internal_id = base64.urlsafe_b64encode(href.encode()).decode()
                links.append({
                    "quality": "",
                    "url": href,
                    "size": "",
                    "label": ep_title or "Episode",
                    "type": "EPISODE",
                    "id": make_content_id("wecima", "series", internal_id)
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
