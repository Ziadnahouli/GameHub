"""
Multi-Provider Movie Engine: central orchestrator.
Aggregates Akwam + Arabseed (and future providers), with caching, timeouts, and deduplication.
"""

import re
import time
import logging
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
import urllib3
import time
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from providers import AkwamProvider, ArabseedProvider

logger = logging.getLogger("GameHub_Movies")

# Defaults for blueprint (Layer 4)
DEFAULT_AKWAM_DOMAINS = ["ak.sv", "akwam.net", "ak.net.co", "akwam.cx", "akw.am"]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _normalize_title(s: str) -> str:
    """Normalize for deduplication: lowercase, collapse spaces, remove some punctuation."""
    if not s:
        return ""
    # Remove release years from title explicitly: ( 2025 ), 2025
    s = re.sub(r"\(\s*\d{4}\s*\)", "", s)
    s = re.sub(r"\b\d{4}\b", "", s)
    # Remove generic arabic and english prefixes/suffixes
    s = re.sub(r"^(?:فيلم|مسلسل|برنامج|مسرحية|الانمي|انمي|كرتون|movie|series)\s+", "", s, flags=re.I)
    s = re.sub(r"\s*(?:مترجم|مدبلج|كامل|حصريا|بدون إعلانات|انمي|فيلم)\s*$", "", s, flags=re.I)
    # Strip "الحلقة X" mostly from search results so episodes from Arabseed match Akwam series
    s = re.sub(r"\s*(?:الحلقة|حلقة|ep|episode|\bS\d+E\d+\b)\s*\d+.*$", "", s, flags=re.I)
    s = re.sub(r"[\s\-_]+", " ", s.lower().strip())
    return re.sub(r"[^\w\s]", "", s).strip()


# Episode number from label: "الحلقة 1", "حلقة 1 : مسلسل مولانا", "Episode 1"
_EPISODE_NUM_RE = re.compile(r"(?:الحلقة|حلقة|episode|ep)\s*[:\s]*(\d+)", re.I)

def _extract_episode_number(link: dict) -> str | None:
    """Return episode number key for deduplication, or None for non-episodes."""
    if (link.get("type") or "").strip().upper() != "EPISODE":
        return None
    label = (link.get("label") or "").strip()
    m = _EPISODE_NUM_RE.search(label)
    if m:
        return m.group(1)
    # Fallback: first number in label
    m = re.search(r"\d+", label)
    return m.group(0) if m else None

def _deduplicate_links(links: list) -> list:
    """
    One entry per episode (by episode number); one per quality for watch/download.
    Keeps first occurrence to avoid "الحلقة 1" and "حلقة 1 : مسلسل مولانا" both showing.
    """
    seen_ep = set()
    seen_quality = set()
    out = []
    for link in links:
        link_type = (link.get("type") or "").strip().upper()
        if link_type == "EPISODE":
            key = _extract_episode_number(link)
            if key is None:
                out.append(link)
                continue
            if key in seen_ep:
                continue
            seen_ep.add(key)
            out.append(link)
        else:
            # STREAM_RESOLVE / download: dedupe by (type, quality)
            quality = (link.get("quality") or link.get("label") or "").strip().upper() or link.get("url", "")
            key = (link_type, quality)
            if key in seen_quality:
                continue
            seen_quality.add(key)
            out.append(link)
    return out

def _deduplicate(items: list, key_title="title", key_year="year", key_type="type") -> list:
    """
    Merge results by (title, year, type). First provider = primary id; others = supplement_ids.
    So details can be merged: primary first, supplement only when primary lacks that episode/quality.
    """
    groups = {}  # norm -> list of items (all providers for same content)
    for item in items:
        title = (item.get(key_title) or "").strip()
        year = (item.get(key_year) or "").strip()
        ctype = (item.get(key_type) or "Movie").strip()
        norm = (_normalize_title(title), ctype)
        groups.setdefault(norm, []).append(item)
    out = []
    for norm, group in groups.items():
        first = group[0].copy()
        first["id"] = first.get("id")  # primary
        
        # Keep only ONE supplement ID per alternating provider to avoid N+1 queries 
        # (if a provider returned 30 episodes, 1 is enough to fetch details)
        seen_providers = {first.get("id").split(":")[0]} if first.get("id") else set()
        supplement_ids = []
        for x in group[1:]:
            pid = x.get("id", "").split(":")[0] if x.get("id") else ""
            if pid and pid not in seen_providers:
                seen_providers.add(pid)
                supplement_ids.append(x.get("id"))
                
        if supplement_ids:
            first["supplement_ids"] = supplement_ids
        out.append(first)
    return out


class MovieManager:
    """
    Central request router and aggregator. Runs all providers in parallel with timeouts,
    caches results, and deduplicates by title/year/type.
    """

    PROVIDER_TIMEOUT_SECONDS = 30  # Arabseed/WeCima may need enter-site + retry (multiple requests)

    def __init__(self):
        self.akwam_domains = list(DEFAULT_AKWAM_DOMAINS)
        self.headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://google.com/",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        self._cache_ttl_seconds = 5 * 60
        self._home_cache = {"timestamp": 0, "data": {}}
        self._trending_cache = {"timestamp": 0, "data": []}
        self._search_cache = {}

        self._executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="movies_")
        self._build_providers()

    def _build_providers(self):
        """Build provider list from current config (domains, user-agent)."""
        ua = self.headers.get("User-Agent") or DEFAULT_USER_AGENT
        self.providers = [
            AkwamProvider(domains=self.akwam_domains, user_agent=ua),
            ArabseedProvider(user_agent=ua),
        ]

    def apply_blueprint(self, blueprint):
        """
        Apply cloud blueprint (Layer 4). Updates domains and User-Agent, then rebuilds providers.
        """
        if not blueprint or not isinstance(blueprint, dict):
            return
        movies_cfg = blueprint.get("movies") or {}
        domains = movies_cfg.get("akwam_domains")
        if isinstance(domains, list):
            self.akwam_domains = domains if domains else []
        ua = movies_cfg.get("user_agent")
        if isinstance(ua, str) and ua.strip():
            self.headers["User-Agent"] = ua.strip()
        self.session.headers.update(self.headers)
        self._build_providers()

    def _get_cached_trending(self):
        now = time.time()
        if (now - self._trending_cache.get("timestamp", 0)) <= self._cache_ttl_seconds:
            return self._trending_cache.get("data") or []
        return []

    def _set_cached_trending(self, data):
        self._trending_cache = {"timestamp": time.time(), "data": data or []}

    def _get_cached_search(self, query):
        if not query:
            return None
        key = query.strip().lower()
        entry = self._search_cache.get(key)
        if not entry:
            return None
        if (time.time() - entry.get("timestamp", 0)) > self._cache_ttl_seconds:
            return None
        return entry.get("data")

    def _set_cached_search(self, query, data):
        if not query:
            return
        self._search_cache[query.strip().lower()] = {
            "timestamp": time.time(),
            "data": data or [],
        }

    def _run_providers_parallel(self, method_name: str, *args, **kwargs) -> list:
        """
        Call method_name on all providers in parallel with timeout. Returns combined list.
        """
        combined = []
        futures = {}
        for p in self.providers:
            method = getattr(p, method_name, None)
            if not callable(method):
                continue
            try:
                future = self._executor.submit(
                    lambda m, *a, **kw: m(*a, **kw),
                    method,
                    *args,
                    **kwargs,
                )
                futures[future] = p.name
            except Exception as e:
                logger.warning(f"[Movies] Submit {method_name} for {p.name}: {e}")

        for future in futures:
            provider_name = futures[future]
            try:
                result = future.result(timeout=self.PROVIDER_TIMEOUT_SECONDS)
                if isinstance(result, list):
                    combined.extend(result)
                elif result and isinstance(result, dict):
                    combined.append(result)
            except FuturesTimeoutError:
                logger.warning(f"[Movies] Provider {provider_name} timed out ({method_name})")
            except Exception as e:
                logger.warning(f"[Movies] Provider {provider_name} failed: {e}")
        return combined

    def get_trending(self):
        cached = self._get_cached_trending()
        if cached:
            return cached
            
        combined = self._run_providers_parallel("get_trending")
        merged = _deduplicate(combined)
        
        self._set_cached_trending(merged)
        return merged

    def get_home_categorized(self):
        """
        Broad-fetch strategy: Fetch main feeds and sort into sections via keywords.
        This is more reliable than hitting individual category paths which often fail.
        """
        now = time.time()
        if (now - self._home_cache.get("timestamp", 0)) <= 300: # 5 min cache
            return self._home_cache.get("data")

        # Basic sections we want to populate
        sections = {
            "Arabic Movies": [],
            "Foreign Movies": [],
            "Arabic Series": [],
            "Foreign Series": [],
            "Animation & Anime": []
        }

        # Step 1: Broad Fetch - Get all trending/latest content
        # Expand fetch paths to ensure more variety and direct hits on categories
        paths = [
            "/movies", "/series", 
            "/category/movies/arabic-movies/", 
            "/category/movies/foreign-movies/", 
            "/category/animation-movies-series/",
            "/category/anime/",
            "/search?q=&category=40"
        ]
        all_items = []
        for path in paths:
            all_items.extend(self._run_providers_parallel("get_category_content", path))
        
        # Also include generic trending/homepage content
        all_items.extend(self.get_trending())

        # Step 2: Keywords and Sorting Logic - Handled by _categorize_item

        # Step 3: Distribution
        for item in all_items:
            label = self._categorize_item(item)
            if label in sections:
                sections[label].append(item)

        # Step 4: Final Deduplication and Truncate
        results = {}
        for label, items in sections.items():
            deduped = _deduplicate(items)
            results[label] = deduped[:48]

        self._home_cache = {"timestamp": now, "data": results}
        return results

    def search(self, query):
        if not (query or "").strip():
            return []
        cached = self._get_cached_search(query)
        if cached is not None:
            return cached
            
        combined = self._run_providers_parallel("search", query)
        merged = _deduplicate(combined)
        
        # Add category to search results for frontend filtering
        for item in merged:
            item["category"] = self._categorize_item(item)
            
        self._set_cached_search(query, merged)
        return merged

    def _categorize_item(self, item):
        title = (item.get("title") or "").lower()
        itype = (item.get("type") or "Movie").lower()
        
        # Refined keyword markers
        is_foreign = any(x in title for x in ["مترجم", "مدبلج", "ajnabi", "foreign", "subbed", "dubbed", "هندي", "تركي", "اجنبي", "english", "bollywood", "hollywood", "كورى", "ياباني", "صيني", "مدبلج"])
        
        # Script check: If title is purely Latin/English char-based, it's likely foreign
        # (Matches common latin chars, ignores numbers/punctuation)
        is_pure_latin = not any('\u0600' <= c <= '\u06FF' for c in title)
        if is_pure_latin:
            is_foreign = True

        # Animation detection: MUST BE FIRST and very aggressive
        is_animation = any(x in title for x in ["انمي", "anime", "animat", "كرتون", "cartoon", "سبايستون", "سبيستون", "حلقات انمي", "افلام انمي"])
        if is_animation:
            return "Animation & Anime"

        # Determination logic
        is_series = "series" in itype or any(x in title for x in ["مسلسل", "episode", "حلقة", "موسم", "season", "برنامج", "توك شو"])
        
        # Arabic Movie specific detection
        has_arabic = any('\u0600' <= c <= '\u06FF' for c in title)
        is_dubbed_or_subbed = any(x in title for x in ["مترجم", "مدبلج", "subbed", "dubbed", "ajnabi", "foreign", "هندي", "تركي", "اجنبي", "english", "bollywood", "hollywood", "كورى", "ياباني", "صيني"])
        
        if has_arabic and not is_dubbed_or_subbed and not is_pure_latin:
            # Native Arabic production
            return "Arabic Series" if is_series else "Arabic Movies"
            
        if is_foreign or is_pure_latin or is_dubbed_or_subbed:
            return "Foreign Series" if is_series else "Foreign Movies"
        
        # Default fallback
        if has_arabic:
            return "Arabic Series" if is_series else "Arabic Movies"
        return "Foreign Series" if is_series else "Foreign Movies"

    def get_details(self, content_id, supplement_ids=None):
        if not content_id:
            return None
        # Support legacy id format "akwam|base64"
        if content_id.startswith("akwam|"):
            from providers import make_content_id
            import base64
            try:
                url = base64.urlsafe_b64decode(content_id.replace("akwam|", "")).decode()
                ctype = "series" if any(x in url.lower() for x in ["series", "season", "episode"]) else "movie"
                internal_id = content_id.replace("akwam|", "")
                content_id = make_content_id("akwam", ctype, internal_id)
            except Exception:
                return None
        primary = self._get_details_from_provider(content_id)
        if not primary:
            return None
        primary["links"] = _deduplicate_links(primary.get("links") or [])
        supplement_ids = supplement_ids or []
        if not supplement_ids:
            return primary
        # Build sets of what primary already has (episode numbers, qualities)
        primary_ep = set()
        primary_quality = set()
        for link in primary.get("links") or []:
            if (link.get("type") or "").strip().upper() == "EPISODE":
                k = _extract_episode_number(link)
                if k:
                    primary_ep.add(k)
            else:
                q = (link.get("quality") or link.get("label") or "").strip().upper() or link.get("url", "")
                if q:
                    primary_quality.add(q)
        merged_links = list(primary.get("links") or [])
        for sid in supplement_ids:
            if not sid or sid == content_id:
                continue
            supp = self._get_details_from_provider(sid)
            if not supp or not supp.get("links"):
                continue
            for link in supp.get("links") or []:
                link_type = (link.get("type") or "").strip().upper()
                if link_type == "EPISODE":
                    key = _extract_episode_number(link)
                    if key and key not in primary_ep:
                        primary_ep.add(key)
                        merged_links.append(link)
                else:
                    q = (link.get("quality") or link.get("label") or "").strip().upper() or link.get("url", "")
                    if q and q not in primary_quality:
                        primary_quality.add(q)
                        merged_links.append(link)
        primary["links"] = _deduplicate_links(merged_links)
        return primary

    def _get_details_from_provider(self, content_id):
        """Fetch details from the provider that owns this content_id."""
        provider_name = content_id.split(":", 1)[0] if ":" in content_id else None
        for p in self.providers:
            if p.name == provider_name:
                try:
                    return p.get_details(content_id)
                except Exception as e:
                    logger.error(f"[Movies] get_details failed for {content_id}: {e}")
                    return None
        return None

    def resolve_stream(self, url: str):
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        for p in self.providers:
            # Check if domain matches netloc OR if URL starts with domain (for magnets/schemes)
            provider_domains = getattr(p, "_domains", [])
            if any(d in domain for d in provider_domains) or any(url.startswith(d) for d in provider_domains):
                if hasattr(p, "resolve_stream"):
                    try:
                        return p.resolve_stream(url)
                    except Exception as e:
                        logger.warning(f"[Movies] Provider {p.name} resolve failed: {e}")
        
        return None, None


movie_manager = MovieManager()
