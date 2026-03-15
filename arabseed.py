"""
Arabseed provider: stateless scraper for Arabseed.
Uses different HTML paths than Akwam; specialized logic for post pages and download links.
"""

import re
import base64
import logging
from urllib.parse import urljoin, urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from .base_provider import BaseProvider, make_content_id

logger = logging.getLogger("GameHub_Movies")

# Try mobile first (redirect target); then desktop
DEFAULT_ARABSEED_DOMAINS = ["asd.pics", "m.arabseed.show", "arabseed.com", "arabseed.net"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ArabseedProvider(BaseProvider):
    def __init__(self, domains=None, user_agent=None):
        self._domains = list(domains) if domains else list(DEFAULT_ARABSEED_DOMAINS)
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
        return "arabseed"

    def _validate_html(self, text: str, min_len: int = 1500) -> None:
        if "Cloudflare" in text or "captcha" in text.lower() or "Just a moment..." in text:
            raise Exception("Blocked by Cloudflare or CAPTCHA")
        if len(text) < min_len:
            raise Exception("Invalid / empty page")

    def _abs_url(self, href: str, domain: str, scheme: str = "https") -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return scheme + ":" + href
        if href.startswith("/"):
            return f"{scheme}://{domain}{href}"
        return href

    def _item_type(self, url_or_path: str) -> str:
        lower = (url_or_path or "").lower()
        if "مسلسل" in lower or "series" in lower or "season" in lower or "episode" in lower or "حلقة" in lower:
            return "Series"
        return "Movie"

    # Site-level gate: the "الدخول للموقع" (Enter the site) landing page
    _LANDING_PAGE_MARKERS = re.compile(r"الدخول للموقع|اقسام الموقع")

    def _is_landing_page(self, html_text: str) -> bool:
        """True if this is the gateway page with 'Enter the site' / اقسام الموقع."""
        return bool(html_text and self._LANDING_PAGE_MARKERS.search(html_text))

    def _find_enter_site_link(self, soup: BeautifulSoup, domain: str, scheme: str = "https") -> str | None:
        """Find the 'الدخول للموقع' (Enter the site) button/link. Returns absolute URL or None."""
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            text = a.get_text(strip=True)
            if "الدخول للموقع" in text or "الدخول" in text and "الموقع" in text:
                return self._abs_url(href, domain, scheme)
        return None

    def _enter_site_then_get(
        self, url: str, domain: str, timeout: int = 25
    ) -> tuple[requests.Response | None, str]:
        """
        GET url; if restricted or blocked with 403, follow the 'Enter the site' flow.
        """
        try:
            # First attempt
            res = self._session.get(url, timeout=timeout, verify=False)
            res.encoding = "utf-8"
            
            logger.info(f"[Arabseed] First attempt {url}: {res.status_code} (Len: {len(res.text)})")

            # If 403 or we see landing page markers, we must 'Enter'
            needs_enter = not res.ok and res.status_code == 403
            if res.ok and self._is_landing_page(res.text):
                needs_enter = True
                
            if needs_enter:
                logger.info(f"[Arabseed] Accessing gateway for {domain}...")
                # Go to home to find the enter button
                home_url = f"{urlparse(url).scheme or 'https'}://{domain}/"
                res_home = self._session.get(home_url, timeout=timeout, verify=False)
                logger.info(f"[Arabseed] Home status: {res_home.status_code} (Len: {len(res_home.text)})")
                if res_home.ok:
                    soup = BeautifulSoup(res_home.text, "html.parser")
                    enter_url = self._find_enter_site_link(soup, domain, urlparse(url).scheme or "https")
                    if enter_url:
                        logger.info(f"[Arabseed] Clicking 'Enter Site': {enter_url}")
                        res_gate = self._session.get(enter_url, timeout=timeout, verify=False, allow_redirects=True)
                        effective_domain = urlparse(res_gate.url).netloc or domain
                        logger.info(f"[Arabseed] Gate internal URL: {res_gate.url}")
                        
                        # Re-try the original path on the correct domain
                        if effective_domain != domain:
                            parsed = urlparse(url)
                            url = f"{parsed.scheme or 'https'}://{effective_domain}{parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''}"
                            domain = effective_domain
                        
                        logger.info(f"[Arabseed] Re-requesting: {url}")
                        res = self._session.get(url, timeout=timeout, verify=False)
                        res.encoding = "utf-8"
                        logger.info(f"[Arabseed] Final attempt status: {res.status_code} (Len: {len(res.text)})")

            return res, domain
        except Exception as e:
            logger.warning(f"[Arabseed] _enter_site_then_get failed: {e}")
            return None, domain

    # Gate-button text (Arabic/English): "watch", "download", "continue", "go to links", etc.
    _GATE_PATTERNS = re.compile(
        r"مشاهدة|تحميل|اضغط\s*للمشاهدة|اذهب\s*للمشاهدة|عرض\s*الروابط|watch|download|continue|go\s*to\s*(watch|links)|رابط\s*المشاهدة",
        re.I,
    )

    def _find_gate_link(self, soup: "BeautifulSoup", current_url: str, domain: str) -> str | None:
        """
        Find the main CTA that leads to the page with actual watch/download links.
        Returns the absolute URL to follow, or None if not found.
        """
        parsed = urlparse(current_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        candidates = []
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            text = a.get_text(strip=True)
            if not self._GATE_PATTERNS.search(text):
                continue
            full = self._abs_url(href, domain, parsed.scheme or "https")
            if full == current_url:
                continue
            # Prefer same-domain and path that looks like watch/download page
            if domain not in full:
                continue
            candidates.append((full, text))
        if not candidates:
            return None
        # Prefer link whose text suggests "watch" or "download" over generic "continue"
        for url, text in candidates:
            if re.search(r"مشاهدة|watch|تحميل|download|رابط|links", text, re.I):
                return url
        return candidates[0][0]

    # Skip these href patterns (nav, tags, not content)
    _SKIP_HREF = re.compile(
        r"wp-login|wp-admin|/tag/|/author/|/page/\d|#|javascript:|mailto:|tel:|\?replytocom=",
        re.I,
    )

    def _parse_list_page(self, soup: BeautifulSoup, effective_domain: str, max_items: int = 24) -> list:
        """
        Extract movie/series items from a listing page. Tries multiple selectors
        then fallback: any content link with an image (WordPress/theme-agnostic).
        """
        def norm(href: str) -> str:
            return self._abs_url(href, effective_domain) if href else ""

        def is_content_link(href: str) -> bool:
            if not href or href.startswith("#"):
                return False
            return not self._SKIP_HREF.search(href)

        # 1) Try known item containers
        main_container = soup.select_one(".blocks__ul, .Blocks-List, .Movie-List, .series__list, #main, main")
        root = main_container if main_container else soup

        for selector in (
            "li[class*='box__']", # asd.pics latest
            "article.post",
            "article",
            ".movie-item",
            ".item-box",
            ".entry-box",
            ".post-item",
            ".col-lg-3",
            ".col-md-4",
            ".col-6",
            ".entry",
            'div[class*="movie"]',
            'div[class*="post"]',
            "div.post",
            "li",
        ):
            items = root.select(selector)
            if not items:
                continue
            results = []
            for item in items[: max_items * 2]:  # allow some skips
                link_el = item.select_one("a[href]")
                if not link_el:
                    continue
                href = link_el.get("href") or ""
                if not is_content_link(href):
                    continue
                details_url = norm(href)
                if not details_url:
                    continue
                title = ""
                # Safest bet is a long 'title' attribute if it exists
                if link_el and link_el.get("title") and len(link_el.get("title")) > 2:
                    title = link_el.get("title")
                else:
                    title_el = (
                        item.select_one("h3")
                        or item.select_one("h2")
                        or item.select_one(".movie__title")
                        or item.select_one(".entry-title")
                        or item.select_one(".title")
                    )
                    if title_el:
                        title = title_el.get_text(strip=True)
                
                if not title or len(title) < 2:
                    title = "Untitled"
                img_el = item.select_one("img")
                img_src = ""
                if img_el:
                    img_src = img_el.get("data-src") or img_el.get("data-lazy-src") or img_el.get("src") or ""
                img_src = norm(img_src) if img_src else ""
                if img_src:
                    img_src = f"/api/proxy-image?url={quote(img_src)}"
                year_el = item.select_one(".year") or item.select_one(".date") or item.select_one(".badge")
                year = (year_el.get_text(strip=True) if year_el else "") or ""
                if year and not re.search(r"\d{4}", year):
                    year = ""
                
                rating = ""
                rating_el = item.select_one(".rating") or item.select_one(".imdb") or item.select_one(".rating-average")
                if rating_el:
                    m = re.search(r"(\d+(?:\.\d+)?)", rating_el.get_text())
                    if m: rating = m.group(1)
                
                ctype = self._item_type(details_url + " " + title)
                internal_id = base64.urlsafe_b64encode(details_url.encode()).decode()
                results.append({
                    "id": make_content_id("arabseed", ctype.lower(), internal_id),
                    "title": title,
                    "poster": img_src,
                    "type": ctype,
                    "year": year,
                    "rating": rating,
                    "language": "Mixed",
                    "source": "Direct Server",
                })
            if results:
                return results[:max_items]

        # 2) Fallback: links that contain an image and point to content-like URLs
        content_el = soup.select_one("#content, main, .content, .main-content, .site-main, .posts")
        root = content_el if content_el else soup
        seen_urls = set()
        results = []
        for a in root.select("a[href]"):
            if len(results) >= max_items:
                break
            href = (a.get("href") or "").strip()
            if not is_content_link(href):
                continue
            details_url = norm(href)
            if not details_url or details_url in seen_urls:
                continue
            img = a.select_one("img")
            if not img:
                continue
            seen_urls.add(details_url)
            title = img.get("alt") or a.get_text(strip=True) or a.get("title") or "Untitled"
            if len(title) < 2:
                title = "Untitled"
            img_src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
            img_src = norm(img_src) if img_src else ""
            if img_src:
                img_src = f"/api/proxy-image?url={quote(img_src)}"
            year = ""
            ctype = self._item_type(details_url + " " + title)
            internal_id = base64.urlsafe_b64encode(details_url.encode()).decode()
            results.append({
                "id": make_content_id("arabseed", ctype.lower(), internal_id),
                "title": title,
                "poster": img_src,
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
        search_paths = [f"/find/?word={q}", f"/?s={q}", f"/search/{q}/"]
        for domain in self._domains:
            try:
                for path in search_paths:
                    search_url = f"https://{domain}{path}"
                    res, effective_domain = self._enter_site_then_get(search_url, domain)
                    if res is None or not res.ok:
                        continue
                    if self._is_landing_page(res.text):
                        continue
                    self._validate_html(res.text, min_len=1500)
                    soup = BeautifulSoup(res.text, "html.parser")
                    results = self._parse_list_page(soup, effective_domain)
                    if results:
                        grouped = []
                        seen_titles = set()
                        for item in results:
                            title = item.get("title", "")
                            if "حلقة" in title.lower() or "episode" in title.lower() or item.get("type") == "tv":
                                series_title = re.sub(r'(?i)الحلقة\s*\d+.*$', '', title).strip()
                                series_title = re.sub(r'(?i)episode\s*\d+.*$', '', series_title).strip()
                                if not series_title:
                                    series_title = title
                                if series_title in seen_titles:
                                    continue
                                seen_titles.add(series_title)
                                item["title"] = series_title
                                item["type"] = "tv"
                                if ":" in item.get("id", ""):
                                    parts = item["id"].split(":", 2)
                                    item["id"] = f"{parts[0]}:tv:{parts[2]}"
                            elif title not in seen_titles:
                                seen_titles.add(title)
                            grouped.append(item)
                        return grouped
            except Exception as e:
                logger.warning(f"[Arabseed] Search failed on {domain}: {e}")
        return []

    def get_trending(self) -> list:
        # Standard trending: homepage
        return self.get_category_content("/")

    def get_category_content(self, category_path: str) -> list:
        if not self._domains:
            return []
        # Try up to 3 domains for resilience
        trend_timeout = 15
        for domain in self._domains[:3]:
            try:
                url = f"https://{domain}{category_path}"
                res, effective_domain = self._enter_site_then_get(url, domain, timeout=trend_timeout)
                if res is None or not res.ok:
                    continue
                if self._is_landing_page(res.text):
                    continue
                soup = BeautifulSoup(res.text, "html.parser")
                results = self._parse_list_page(soup, effective_domain, max_items=48)
                if results:
                    return results
            except Exception as e:
                logger.warning(f"[Arabseed] Category {category_path} failed on {domain}: {e}")
        return []

    def get_details(self, content_id: str) -> dict | None:
        from .base_provider import parse_content_id
        provider, ctype, internal_id = parse_content_id(content_id)
        if provider != self.name or not internal_id:
            return None
        
        try:
            url = base64.urlsafe_b64decode(internal_id).decode()
            return self._scrape_details(url, urlparse(url).netloc, ctype)
        except Exception as e:
            logger.error(f"[Arabseed] Decode ID failed {content_id}: {e}")
            return None

    def _scrape_details(self, url: str, domain: str, ctype: str = "") -> dict | None:
        try:
            logger.debug(f"[Arabseed] Scraping details for {url}")
            res, effective_domain = self._enter_site_then_get(url, domain)
            if res is None or not res.ok:
                logger.warning(f"[Arabseed] Scrape failed to get 200/OK: {res.status_code if res else 'None'} for {url}")
                return None
            self._validate_html(res.text)
            soup = BeautifulSoup(res.text, "html.parser")
            domain = effective_domain  # use redirect host for link resolution
            logger.debug(f"[Arabseed] Page fetched successfully. Domain: {domain}")

            # If this is an episode, try to find the series link
            is_episode = "/episode/" in url or "حلقة" in (soup.title.string or "")
            
            if ctype in ("tv", "series") and is_episode:
                series_link = None
                # Check breadcrumbs for /selary/ or /series/
                for bc in soup.select('.bread__crumbs a, .breadcrumb a, .page__path a'):
                    bc_href = bc.get('href', '')
                    if '/selary/' in bc_href or '/series/' in bc_href:
                        series_link = self._abs_url(bc_href, effective_domain)
                        # We prefer the most specific one, so we don't break yet
                
                # If we found a series link and it's different, we might want to fetch it
                # However, Arabseed episode pages often have the full story and episode list.
                # If we lack summary or actors, we fetch series link.
                if series_link and series_link != url and not soup.select_one(".post__story"):
                    try:
                        res_series = self._session.get(series_link, timeout=10, verify=False)
                        if res_series.ok:
                            soup = BeautifulSoup(res_series.text, "html.parser")
                            url = series_link
                    except: pass

            # Title
            title_el = soup.select_one("h1.post__name") or soup.select_one("h1") or soup.select_one(".entry-title")
            title = title_el.get_text(strip=True) if title_el else "Unknown Title"
            
            # Summary
            story = "No description available."
            story_el = soup.select_one(".post__story") or soup.select_one(".entry-content") or soup.select_one(".post-content")
            if story_el:
                story = story_el.get_text(strip=True)
            else:
                for sel in [".content", ".widget-body", "article"]:
                    for p in soup.select(f"{sel} p"):
                        text = p.get_text(strip=True)
                        if len(text) > len(story) and len(text) > 50:
                            story = text

            # Rating
            rating = ""
            rating_el = soup.select_one(".rating-average") or soup.select_one(".rating") or soup.select_one(".imdb")
            if rating_el:
                t = rating_el.get_text(strip=True)
                m = re.search(r"(\d+(?:\.\d+)?)", t)
                if m:
                    rating = m.group(1)

            # Year
            year = ""
            year_el = soup.select_one("a[href*='/release-year/']") or soup.select_one(".year") or soup.select_one(".date")
            if year_el:
                t = year_el.get_text(strip=True)
                m = re.search(r"\b(19|20)\d{2}\b", t)
                if m:
                    year = m.group(0)
            if not year:
                m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", soup.get_text(" ", strip=True))
                if m:
                    year = m.group(0)

            # Type
            content_type = self._item_type(url + " " + soup.get_text(" ", strip=True)[:500])

            links = []
            
            landing_pages = []
            seen_urls = set()
            
            def extract_links_from_page(page_soup, label_prefix="", is_download_page=False):
                # 1. Look for direct video links and download buttons
                # Added /l/ (redirector) and more button selectors
                for a in page_soup.select(
                    'a[href*="download"], a.download-link, a[href*="watch"], '
                    '.download-btn a, .links a, .quality-link, a[href*="/l/"], '
                    'a[href*="cdn"], a[href*="stream"], a[href*="reviewrate.net"]'
                ):
                    href = a.get("href")
                    if not href or not href.startswith(("http", "/")):
                        continue
                    
                    # Skip known unreliable hosters (REMOVED: frdl is now supported)
                    # if any(d in href.lower() for d in ["fredl.ru", "freedl.ink", "frdl.to"]):
                    #     continue
                    
                    href = self._abs_url(href, domain)
                    if "/watch/" in href or "/download/" in href:
                         if href not in landing_pages: landing_pages.append(href)
                         continue
                    
                    # Remove strict filtering for asd.pics so redirector links are caught
                    # if "arabseed" in href or "asd.pics" in href:
                    #      if not any(ext in href.lower() for ext in ['.mp4', '.mkv', '.m3u8']):
                    #         continue
                    
                    if href in seen_urls: continue
                    seen_urls.add(href)
                    
                    label_text = a.get_text(strip=True)
                    parent_text = (a.parent.get_text(strip=True) if a.parent else "")
                    quality = "HD"
                    # Prioritize numbers (1080p, 720p, etc.) over generic terms
                    qm = re.search(r"(4k|1080p|720p|480p|360p|240p|1080|720|480|SD|HD|Bluray|WEB-DL|فورك)", label_text + " " + parent_text, re.I)
                    if qm:
                        quality = qm.group(1).upper()
                        if quality.isdigit(): quality += "P"
                    else:
                        # Fallback to generic terms if no resolution found
                        gm = re.search(r"(سيرفر)", label_text + " " + parent_text, re.I)
                        if gm:
                            quality = gm.group(1).upper()
                    
                    size_match = re.search(r"(\d+(?:\.\d+)?)\s*(MB|GB|KB)", label_text + " " + parent_text, re.I)
                    size = f"{size_match.group(1)} {size_match.group(2).upper()}" if size_match else ""
                    
                    # Ensure label contains 'Watch' if it's a stream link for the UI to show 'Watch in-App'
                    # EXCEPT for links definitely from a download page or with download keywords
                    final_label = f"{label_prefix} {label_text or f'{quality} Download'}".strip()
                    
                    if not is_download_page:
                        # Watch keywords (English + Arabic)
                        watch_ks = ['watch', 'مشاهدة', 'server', 'online', 'سيرفر', 'بث']
                        # Download keywords (English + Arabic)
                        dl_ks = ['download', 'تحميل', 'تنزيل', 'link', 'ملف', 'file']
                        
                        has_watch = any(k in final_label.lower() for k in watch_ks)
                        has_dl = any(k in final_label.lower() for k in dl_ks)
                        
                        if not has_watch and not has_dl:
                            final_label = f"Watch: {final_label}"
                    else:
                        # For download pages, ensure 'Watch' doesn't sneak into the label
                        for k in ['Watch:', 'Watch', 'مشاهدة']:
                            final_label = final_label.replace(k, "")
                        final_label = final_label.strip()
                        if not final_label: final_label = f"{quality} Download"

                    links.append({
                        "quality": quality, "url": href, "size": size,
                        "label": final_label, "type": "STREAM_RESOLVE",
                    })

                # 2. Extract iframes (Player embeds)
                for iframe in page_soup.select('iframe[src], iframe[data-src]'):
                    src = iframe.get('src') or iframe.get('data-src')
                    if not src or "facebook.com" in src or "twitter.com" in src: continue
                    
                    abs_src = self._abs_url(src, domain)
                    if abs_src in seen_urls: continue
                    seen_urls.add(abs_src)

                    # Decoded URL is usually an external embed
                    if "play.php?url=" in src:
                        match = re.search(r'url=([A-Za-z0-9+/=]+)', src)
                        if match:
                            try:
                                stream_url = base64.b64decode(match.group(1)).decode('utf-8')
                                if stream_url not in seen_urls:
                                    seen_urls.add(stream_url)
                                    links.append({
                                        "quality": "HD", "url": stream_url, "size": "Stream",
                                        "label": f"{label_prefix} Arabseed Watch Ad-Free (Direct)".strip(), "type": "STREAM_RESOLVE",
                                    })
                            except: pass

                    # Ensure ALL player links are STREAM_RESOLVE so they go through the backend proxy
                    links.append({
                        "quality": "HD", "url": abs_src, "size": "Stream",
                        "label": f"{label_prefix} Arabseed Watch on Server".strip(), "type": "STREAM_RESOLVE",
                    })

            # Perform initial extraction
            extract_links_from_page(soup)
            
            # Discovery Phase: Try to find dedicated landing pages
            effective_url = res.url.rstrip("/")
            
            # Prioritize finding ONE watch and ONE download landing page
            final_targets = {}
            for lp in landing_pages:
                if "/download/" in lp and "download" not in final_targets:
                    final_targets["download"] = lp
                elif "/watch/" in lp and "watch" not in final_targets:
                    final_targets["watch"] = lp
            
            # Failsafe: if not found, add suffixes
            if "download" not in final_targets:
                final_targets["download"] = effective_url + "/download/"
            if "watch" not in final_targets:
                final_targets["watch"] = effective_url + "/watch/"

            # Parallel Fetching Phase: Process landing pages concurrently
            def fetch_landing_page(target_url, prefix, is_dl):
                try:
                    res_p = self._session.get(target_url, timeout=5, verify=False)
                    if res_p.ok:
                        return (BeautifulSoup(res_p.text, "html.parser"), prefix, is_dl)
                except: pass
                return None

            targets = []
            if "watch" in final_targets: targets.append((final_targets["watch"], "[Watch]", False))
            if "download" in final_targets: targets.append((final_targets["download"], "[Download]", True))
            
            if targets:
                with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                    futures = [executor.submit(fetch_landing_page, *t) for t in targets]
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            p_soup, prefix, is_dl = result
                            extract_links_from_page(p_soup, prefix, is_download_page=is_dl)

            # Final check: if we still have NO links, we might need a last-ditch effort, 
            # but usually the above covers it.

            # Episode list extraction
            ep_items = []
            ep_seen = set()
            for a in soup.select('.episodes__list a, a[href*="/episode/"], a[href*="حلقة"]'):
                href = a.get("href")
                if not href or any(s in href for s in ["facebook.com", "twitter.com", "whatsapp.com", "t.me", "telegram.me", "/watch/", "/download/"]):
                    continue
                href = self._abs_url(href, domain)
                if href in ep_seen: continue
                ep_seen.add(href)
                
                label = a.get_text(strip=True)
                if not label:
                    num_el = a.select_one("b")
                    if num_el: label = f"الحلقة {num_el.get_text(strip=True)}"
                if not label: label = f"Episode {len(ep_seen)}"
                
                # Try to extract episode number for sorting
                ep_num = 0
                num_match = re.search(r'(\d+)', label)
                if num_match:
                    ep_num = int(num_match.group(1))
                
                ep_items.append((ep_num, label, href))

            # Sort episodes by number if found
            ep_items.sort(key=lambda x: x[0])
            
            for ep_num, label, href in ep_items:
                internal_id = base64.urlsafe_b64encode(href.encode()).decode()
                ep_id = make_content_id("arabseed", "series", internal_id)
                links.append({
                    "quality": "", "url": href, "size": "", "label": label, "type": "EPISODE", "id": ep_id,
                })

            # Poster
            img_el = soup.select_one(".poster__single img") or soup.select_one(".poster img") or soup.select_one("img.wp-post-image")
            poster = (img_el.get("src") or img_el.get("data-src") or "") if img_el else ""
            if poster: 
                poster = self._abs_url(poster, domain)
                poster = f"/api/proxy-image?url={quote(poster)}"

            # Final Sort and Priority: Prioritize 'Arabseed Mubashar'
            def link_priority(l):
                t = l.get('type')
                if t == 'EPISODE': return 100 # Episodes at the end
                lbl = l.get('label', '').lower()
                url = l.get('url', '').lower()
                
                # Check for Mubashar / Arabseed in label
                is_mubashar = "مباشر" in lbl or "mubashar" in lbl or "arabseed" in lbl
                
                if is_mubashar:
                    qual = l.get('quality', '').upper()
                    if "1080" in qual: return 0
                    if "720" in qual: return 1
                    if "480" in qual: return 2
                    return 3
                
                if "/l/" in url and "asd.pics" in url: return 10
                if "internal" in lbl: return 11
                return 20 # Other servers
            
            # Filter: If "Mubashar" links exist, we could optionally filter out OTHERS
            # to keep it as clean as the user wants.
            mubashar_links = [l for l in links if "مباشر" in l.get('label', '').lower() or "mubashar" in l.get('label', '').lower() or "arabseed" in l.get('label', '').lower()]
            if mubashar_links:
                # User wants "arabseed mubashar links only" if they exist
                # But we keep EPISODE types!
                links = mubashar_links + [l for l in links if l.get('type') == 'EPISODE']

            links.sort(key=link_priority)

            return {
                "title": title, "summary": story, "rating": rating, "year": year,
                "poster": poster, "type": content_type, "genres": [], "links": links,
            }
        except Exception as e:
            logger.error(f"[Arabseed] Scrape details failed: {e}")
            return None
