"""
Base interface for Live TV providers.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseLiveProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def get_channels(self) -> List[Dict[str, Any]]:
        """
        Return a list of channels.
        Each channel should have: id, name, logo, category, url, provider.
        """
        pass

    @abstractmethod
    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """
        Resolve the final playable HLS URL for a channel.
        """
        pass
