import requests
import re
import logging
import base64
import json
from typing import List, Dict, Any, Optional
from .base_live_provider import BaseLiveProvider

logger = logging.getLogger("GameHub_Live")

class IPTVOrgProvider(BaseLiveProvider):
    """
    Provider using iptv-org playlists.
    """
    
    ARA_M3U = "https://iptv-org.github.io/iptv/languages/ara.m3u"
    SPORTS_M3U = "https://iptv-org.github.io/iptv/categories/sports.m3u"
    
    # Strict filtering keywords
    FORBIDDEN_KEYWORDS = [
        "xxx", "adult", "sex", "porn", "naked", "erotic", "plus18", "+18", "18+",
        "hardcore", "softcore", "sensual", "hentai"
    ]

    COUNTRY_CODES = {
        "sa": "Saudi Arabia", "eg": "Egypt", "ae": "UAE", "ma": "Morocco",
        "dz": "Algeria", "tn": "Tunisia", "lb": "Lebanon", "jo": "Jordan",
        "ps": "Palestine", "sy": "Syria", "iq": "Iraq", "kw": "Kuwait",
        "qa": "Qatar", "om": "Oman", "bh": "Bahrain", "ye": "Yemen",
        "ly": "Libya", "sd": "Sudan", "mr": "Mauritania", "so": "Somalia",
        "dj": "Djibouti", "km": "Comoros", "tr": "Turkey", "sd": "Sudan",
        "int": "International"
    }

    # Manual stream overrides for problematic channels
    CUSTOM_OVERRIDES = {
        "al jadeed": {
            "url": "http://185.9.2.18/chid_391/mono.m3u8",
            "headers": {"Host": "edge.fastpublish.me"}
        },
        "mtv": "https://hms.pfs.gdn/v1/broadcast/mtv/playlist.m3u8"
    }

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    @property
    def name(self) -> str:
        return "iptv_org"

    def _parse_m3u(self, content: str, default_category: str) -> List[Dict[str, Any]]:
        channels = []
        lines = content.splitlines()
        current_metadata = None
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            if line.startswith("#EXTINF"):
                # Extract metadata
                id_match = re.search(r'tvg-id="([^"]+)"', line)
                logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                group_match = re.search(r'group-title="([^"]+)"', line)
                
                # Logic: Categorize into country except for Sports
                category = default_category
                if default_category != "Sports":
                    if id_match:
                        tvg_id = id_match.group(1).lower()
                        # Extract country code from tvg-id (e.g., AlQamarTV.ae@SD -> ae)
                        segments = tvg_id.split(".")
                        if len(segments) > 1:
                            last_segment = segments[-1]
                            # Remove quality suffixes like @SD, @HD
                            code = last_segment.split("@")[0]
                            if code in self.COUNTRY_CODES:
                                category = self.COUNTRY_CODES[code]
                
                # Fallback to group-title if country not found and not Sports
                if category == default_category and group_match and default_category != "Sports":
                    g_title = group_match.group(1)
                    if g_title.lower() not in ["general", "undefined", "news", "music", "movies", "series"]:
                        category = g_title

                display_name = line.split(",")[-1].strip() if "," in line else "Unknown Channel"
                
                current_metadata = {
                    "name": display_name,
                    "logo": logo_match.group(1) if logo_match else "",
                    "category": category
                }
            elif line.startswith("http") and current_metadata:
                url = line
                
                # Apply custom overrides
                lower_name = current_metadata["name"].lower()
                for override_key, override_data in self.CUSTOM_OVERRIDES.items():
                    if override_key in lower_name:
                        if isinstance(override_data, dict):
                            url = override_data["url"]
                            # If there are custom headers, encode them for the proxy
                            if "headers" in override_data:
                                h_json = json.dumps(override_data["headers"])
                                h_b64 = base64.b64encode(h_json.encode('utf-8')).decode('utf-8')
                                # We'll need to handle the appending of 'h=' carefully 
                                # but since this is before the final id/url assignment, 
                                # we can just store it in the url for now or handle it in resolve
                                url = f"{url}|h={h_b64}"
                        else:
                            url = override_data
                        break

                # Filter out forbidden content
                full_text = (current_metadata["name"] + " " + current_metadata["category"]).lower()
                if any(k in full_text for k in self.FORBIDDEN_KEYWORDS):
                    current_metadata = None
                    continue
                
                channel_id = f"iptv_org:{url}"
                
                channels.append({
                    "id": channel_id,
                    "name": current_metadata["name"],
                    "logo": current_metadata["logo"],
                    "category": current_metadata["category"],
                    "url": url,
                    "provider": self.name
                })
                current_metadata = None
                
        return channels

    def get_channels(self) -> List[Dict[str, Any]]:
        all_channels = []
        
        # 1. Fetch Arabic Channels
        try:
            resp = self._session.get(self.ARA_M3U, timeout=15)
            if resp.status_code == 200:
                all_channels.extend(self._parse_m3u(resp.text, "Arabic"))
        except Exception as e:
            logger.error(f"[IPTV-Org] Failed to fetch Arabic M3U: {e}")

        # 2. Fetch Sports Channels
        try:
            resp = self._session.get(self.SPORTS_M3U, timeout=15)
            if resp.status_code == 200:
                # We only want to add sports if they are relevant or not already present
                # Actually, let's just add them all and let the manager deduplicate or categorize
                all_channels.extend(self._parse_m3u(resp.text, "Sports"))
        except Exception as e:
            logger.error(f"[IPTV-Org] Failed to fetch Sports M3U: {e}")
            
        return all_channels

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        # channel_id is "iptv_org:url" or "iptv_org:url|h=..."
        if not channel_id.startswith("iptv_org:"):
            return None
        
        raw_url = channel_id.split(":", 1)[1]
        
        # If we have encoded headers, they are in the format "url|h=..."
        if "|" in raw_url:
            base_url, h_param = raw_url.split("|", 1)
            # The app.py live_proxy expects url as a query param and h as another
            # But the player (script.js) constructs the proxy URL using only the stream_url
            # So we should return a URL that and includes those params
            # Actually, the best way is to let the frontend handle the proxying or 
            # return a URL that already "is" the proxied URL or contains the params
            
            # If we return "url?h=...", the frontend's openLivePlayer will do:
            # /api/live/proxy?url=RESOLVED_URL
            # So RESOLVED_URL became "url?h=..." which is fine.
            return base_url.replace("|", "?") + "?" + h_param if "?" not in base_url else base_url + "&" + h_param
            
        return raw_url
