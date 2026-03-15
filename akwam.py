"""
Akwam provider: stateless scraper for Akwam domains.
No caching or threading; domain list and User-Agent are supplied at construction
(e.g. from central manager / blueprint).
"""

import base64
import re
import logging
from urllib.parse import urlparse, quote, unquote

import requests
from bs4 import BeautifulSoup

from .base_provider import BaseProvider, make_content_id

logger = logging.getLogger("GameHub_Movies")

# Default domains if none provided (manager should override from blueprint)
DEFAULT_AKWAM_DOMAINS = ["ak.sv", "akwam.net", "ak.net.co", "akwam.cx", "akw.am"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AkwamProvider(BaseProvider):
    def __init__(self, domains=None, user_agent=None):
        self._domains = list(domains) if domains else list(DEFAULT_AKWAM_DOMAINS)
        self._headers = {
            "User-Agent": (user_agent or DEFAULT_USER_AGENT).strip(),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://google.com/",
        }
        self._session = requests.Session()
        self._session.headers.update(self._headers)

    @property
    def name(self) -> str:
        return "akwam"

    def _validate_html(self, text: str) -> None:
        if "Cloudflare" in text or "captcha" in text.lower() or "Just a moment..." in text:
            raise Exception("Blocked by Cloudflare or CAPTCHA")
        if len(text) < 3000: # Lowered from 5000 to allow landing pages
            raise Exception("Invalid / empty page")

    def _is_landing_page(self, text: str) -> bool:
        return 'href="https://' in text and '/main"' in text

    def _enter_site_then_get(self, url: str, domain: str, timeout: int = 15):
        try:
            res = self._session.get(url, timeout=timeout, verify=False)
            if not res.ok: return res
            if self._is_landing_page(res.text):
                # Try adding /main to url or session
                main_url = f"https://{domain}/main"
                self._session.get(main_url, timeout=timeout, verify=False)
                # Retry original
                return self._session.get(url, timeout=timeout, verify=False)
            return res
        except Exception:
            return None

    def _abs_url(self, href: str, domain: str) -> str:
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return f"https://{domain}{href}"
        return href

    def _item_type(self, details_url: str) -> str:
        return (
            "Series"
            if any(
                x in details_url.lower()
                for x in ["series", "season", "episode"]
            )
            else "Movie"
        )

    def search(self, query: str) -> list:
        if not self._domains or not (query or "").strip():
            return []
        for domain in self._domains[:3]: # Resilience: try first 3 domains
            try:
                search_url = f"https://{domain}/search/?q={quote(query)}"
                res = self._enter_site_then_get(search_url, domain, timeout=15)
                if res is None or not res.ok:
                    continue
                res.encoding = "utf-8"
                self._validate_html(res.text)
                soup = BeautifulSoup(res.text, "html.parser")
                items = (
                    soup.select(".entry-box-1")
                    or soup.select(".widget-movie")
                    or soup.select(".item-box")
                    or soup.select(".col-lg.col-4")
                    or soup.select("div.widget-body div.row > div")
                    or soup.select(".col-lg-3")
                    or soup.select(".col-md-4")
                    or soup.select(".col-6")
                    or soup.select("article")
                    or soup.select('div[class*="movie"]')
                )
                results = []
                for item in items[:24]:
                    title_el = (
                        item.select_one(".entry-title a")
                        or item.select_one(".title")
                        or item.select_one(".movie-title")
                        or item.select_one("h3")
                    )
                    if not title_el:
                        title_el = item.select_one("a.box") and item.select_one("h3 a")
                    link_el = item.select_one("a")
                    img_el = item.select_one("img")
                    if not title_el or not link_el:
                        continue
                    details_url = self._abs_url(link_el["href"], domain)
                    year_el = item.select_one(".badge-secondary") or item.select_one(".year")
                    year = year_el.text.strip() if year_el else ""
                    img_src = (
                        img_el.get("data-src")
                        or img_el.get("data-lazy-src")
                        or img_el.get("src")
                        or ""
                    )
                    img_src = self._abs_url(img_src, domain) if img_src else ""
                    ctype = self._item_type(details_url)
                    internal_id = base64.urlsafe_b64encode(details_url.encode()).decode()
                    results.append({
                        "id": make_content_id("akwam", ctype.lower(), internal_id),
                        "title": title_el.text.strip(),
                        "poster": img_src,
                        "type": ctype,
                        "year": year,
                        "rating": (item.select_one(".rating").text.strip().replace("+", "") if item.select_one(".rating") else ""),
                        "language": "Mixed",
                        "source": "Direct Server",
                    })
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[Akwam] Search failed on {domain}: {e}")
        return []

    def get_trending(self) -> list:
        # Standard trending: movies section (usually has latest)
        return self.get_category_content("/movies")

    def get_category_content(self, category_path: str) -> list:
        if not self._domains:
            return []
        for domain in self._domains[:3]:
            try:
                url = f"https://{domain}{category_path}"
                res = self._enter_site_then_get(url, domain, timeout=20)
                if res is None or not res.ok:
                    continue
                res.encoding = "utf-8"
                self._validate_html(res.text)
                soup = BeautifulSoup(res.text, "html.parser")
                items = (
                    soup.select(".item-box")
                    or soup.select(".widget-movie")
                    or soup.select(".entry-box-1")
                    or soup.select(".col-lg.col-4")
                    or soup.select("div.widget-body div.row > div")
                    or soup.select(".col-lg-3")
                    or soup.select(".col-md-4")
                    or soup.select(".col-6")
                    or soup.select("article")
                    or soup.select('div[class*="movie"]')
                )
                results = []
                for item in items[:48]:
                    title_el = (
                        item.select_one(".entry-title a")
                        or item.select_one(".entry-title")
                        or item.select_one(".title")
                        or item.select_one(".movie-title")
                        or item.select_one("h3")
                    )
                    link_el = item.select_one("a")
                    img_el = item.select_one("img")
                    if not title_el or not link_el:
                        continue
                    details_url = self._abs_url(link_el["href"], domain)
                    
                    # Enhanced Metadata Extraction
                    year = ""
                    year_el = item.select_one(".badge-secondary") or item.select_one(".year") or item.select_one(".movie-year")
                    if year_el:
                        m = re.search(r"(\d{4})", year_el.text)
                        if m: year = m.group(1)
                        
                    rating = ""
                    rating_el = item.select_one(".rating-badge") or item.select_one(".imdb-rating") or item.select_one(".quality") # Sometimes quality contains rating or vice versa
                    if rating_el:
                        m = re.search(r"(\d+(?:\.\d+)?)", rating_el.text)
                        if m: rating = m.group(1)
                    
                    img_src = (
                        img_el.get("id")
                        or img_el.get("data-src")
                        or img_el.get("data-lazy-src")
                        or img_el.get("src")
                        or ""
                    )
                    img_src = self._abs_url(img_src, domain) if img_src else ""
                    ctype = self._item_type(details_url)
                    internal_id = base64.urlsafe_b64encode(details_url.encode()).decode()
                    results.append({
                        "id": make_content_id("akwam", ctype.lower(), internal_id),
                        "title": title_el.text.strip(),
                        "poster": img_src,
                        "type": ctype,
                        "year": year,
                        "rating": (item.select_one(".rating").text.strip().replace("+", "") if item.select_one(".rating") else ""),
                        "language": "Mixed",
                        "source": "Direct Server",
                    })
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[Akwam] Category {category_path} failed on {domain}: {e}")
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
        parsed = urlparse(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        for domain in self._domains:
            try:
                try_url = f"https://{domain}{path}"
                return self._scrape_details(try_url)
            except Exception as e:
                logger.warning(f"[Akwam] Details failed for {domain}: {e}")
        return None

    def _scrape_details(self, url: str) -> dict | None:
        try:
            res = self._session.get(url, timeout=15, verify=False)
            res.encoding = "utf-8"
            self._validate_html(res.text)
            soup = BeautifulSoup(res.text, "html.parser")
            domain = urlparse(url).netloc

            # Story
            story = "No description available."
            for p in soup.select(".widget-body p, .post-content p, article p, div p"):
                text = p.text.strip()
                if len(text) > len(story) and len(text) > 50:
                    story = text

            # Rating
            rating = ""
            rating_el = soup.select_one(".font-size-18.text-white.font-weight-bold") or soup.find(
                string=re.compile(r"IMDB")
            )
            if rating_el:
                try:
                    rating_text = (
                        rating_el.parent.text.strip()
                        if hasattr(rating_el, "parent")
                        else getattr(rating_el, "text", "").strip()
                    )
                    match = re.findall(r"\d+\.\d+", rating_text)
                    if match:
                        rating = match[0]
                except Exception:
                    pass

            # Year
            year = ""
            year_el = soup.select_one(".badge-secondary, .year, .badge-year")
            if year_el and re.search(r"\b(19|20)\d{2}\b", year_el.get_text(strip=True)):
                year = year_el.get_text(strip=True)
            if not year:
                m = re.search(
                    r"\b(19[5-9]\d|20[0-4]\d)\b",
                    soup.get_text(" ", strip=True),
                )
                if m:
                    year = m.group(0)

            lower_url = url.lower()
            content_type = (
                "Series"
                if any(x in lower_url for x in ["series", "season", "episode"])
                else "Movie"
            )

            genres = []
            for a in soup.select(
                'a[href*="category"], a[href*="genre"], a[href*="/categories/"], a[href*="/genres/"]'
            ):
                label = a.get_text(strip=True)
                if label and len(label) <= 30 and label not in genres:
                    genres.append(label)

            quality_map = {"5": "1080P", "4": "720P", "3": "480P", "2": "360P", "1": "240P"}
            links = []

            # Watch links
            for idx, a in enumerate(soup.select('a[href*="/watch/"]')):
                href = a.get("href")
                if not href:
                    continue
                parent_row = a.find_parent(attrs={"data-quality": True})
                quality_str = quality_map.get(parent_row.get("data-quality", ""), "") if parent_row else ""
                if not quality_str:
                    label_text = a.text.strip()
                    parent_text = (a.parent.text.strip() if a.parent else "") if a.parent else ""
                    qm = re.search(
                        r"(4k|1080p|720p|480p|360p|240p|1080|720|480)",
                        label_text + " " + parent_text,
                        re.I,
                    )
                    if qm:
                        quality_str = qm.group(1).upper()
                        if quality_str.isdigit():
                            quality_str += "P"
                href = self._abs_url(href, domain)
                links.append({
                    "quality": quality_str or "HD",
                    "url": href,
                    "size": "",
                    "label": f"Server {idx + 1} ({quality_str})" if quality_str else f"Watch Server {idx + 1}",
                    "type": "STREAM_RESOLVE",
                })

            # Download links
            for a in soup.select("a.download-link, .link-btn, a[href*='/download/']"):
                href = a.get("href", "")
                if not href or "/watch/" in href:
                    continue
                href = self._abs_url(href, urlparse(url).netloc)
                label_text = a.text.strip()
                parent_text = (a.parent.text.strip() if a.parent else "") if a.parent else ""
                parent_row = a.find_parent(attrs={"data-quality": True})
                quality = "HD"
                if parent_row and parent_row.get("data-quality") in quality_map:
                    quality = quality_map[parent_row.get("data-quality")]
                else:
                    qm = re.search(
                        r"(4k|1080p|720p|480p|360p|240p|1080|720|480|SD|HD|Bluray|WEB-DL)",
                        label_text + " " + parent_text,
                        re.I,
                    )
                    if qm:
                        quality = qm.group(1).upper()
                        if quality.isdigit():
                            quality += "P"
                size_match = re.search(
                    r"(\d+(?:\.\d+)?)\s*(MB|GB|KB)",
                    label_text + " " + parent_text,
                    re.I,
                )
                size = f"{size_match.group(1)} {size_match.group(2).upper()}" if size_match else ""
                links.append({
                    "quality": quality,
                    "url": href,
                    "size": size,
                    "label": f"{quality} ({size})" if size else f"{quality} Download",
                    "type": "STREAM_RESOLVE",
                })

            # Episodes
            for a in reversed(soup.select('a[href*="/episode/"]')):
                href = a.get("href", "")
                if not href:
                    continue
                href = self._abs_url(href, domain)
                label = a.text.strip() or unquote(href.split("/")[-1]).replace("-", " ")
                ep_internal = base64.urlsafe_b64encode(href.encode()).decode()
                ep_id = make_content_id("akwam", "series", ep_internal)
                links.append({
                    "quality": "",
                    "url": href,
                    "size": "",
                    "label": label,
                    "type": "EPISODE",
                    "id": ep_id,
                })

            title_el = soup.select_one("h1")
            img_el = soup.select_one("img.img-fluid") or soup.select_one(".poster img")
            poster = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            if poster.startswith("//"):
                poster = "https:" + poster

            return {
                "title": title_el.text.strip() if title_el else "Unknown Title",
                "summary": story,
                "rating": rating,
                "year": year,
                "poster": poster,
                "type": content_type,
                "genres": genres,
                "links": links,
            }
        except Exception as e:
            logger.error(f"[Akwam] Scrape details failed: {e}")
            return None
