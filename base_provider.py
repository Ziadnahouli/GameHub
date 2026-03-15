"""
Base interface for movie/series streaming providers.
All providers are stateless: no caching, no UI logic, no threading.
Output is standardized so the router can aggregate and deduplicate.
"""

from abc import ABC, abstractmethod


def make_content_id(provider_name: str, content_type: str, internal_id: str) -> str:
    """
    Build standardized content ID: <provider>:<content_type>:<provider_internal_id>
    e.g. akwam:movie:12345, arabseed:series:breaking-bad
    """
    content_type = (content_type or "movie").lower()
    if content_type not in ("movie", "series"):
        content_type = "movie"
    return f"{provider_name}:{content_type}:{internal_id}"


def parse_content_id(content_id: str):
    """
    Parse standardized ID. Returns (provider_name, content_type, internal_id) or (None, None, None).
    """
    if not content_id or ":" not in content_id:
        return None, None, None
    parts = content_id.split(":", 2)
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


class BaseProvider(ABC):
    """
    Stateless provider: pure fetcher, no caching or threading.
    Optional get_trending(): returns [] by default for providers that lack trending or rate-limit.
    """

    @property
    def name(self) -> str:
        """Provider identifier used in content IDs (e.g. 'akwam', 'arabseed')."""
        raise NotImplementedError

    def search(self, query: str) -> list:
        """
        Search for content. Returns list of:
        {"id": "<provider>:<type>:<internal_id>", "title", "poster", "type": "Movie"|"Series", "year"}
        """
        raise NotImplementedError

    def get_trending(self) -> list:
        """
        Optional. Returns same shape as search() or [] if not supported.
        """
        return []

    def get_category_content(self, category_path: str) -> list:
        """
        Optional. Returns content from a specific category path (e.g. /movies/, /series/).
        """
        return []

    def get_details(self, content_id: str) -> dict | None:
        """
        Fetch full details for a content_id owned by this provider.
        content_id must match make_content_id(provider_name, ...).
        Returns:
          {"title", "summary", "rating", "year", "links": [{"quality", "url", "size"}], ...}
        or None if not found / error.
        """
        raise NotImplementedError
