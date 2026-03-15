"""
Multi-Provider Movie Engine.
"""

from .base_provider import BaseProvider, make_content_id, parse_content_id
from .akwam import AkwamProvider
from .wecima import WeCimaProvider
from .arabseed import ArabseedProvider
from .faselhd import FaselHDProvider

__all__ = [
    "BaseProvider",
    "make_content_id",
    "parse_content_id",
    "AkwamProvider",
    "WeCimaProvider",
    "ArabseedProvider",
    "FaselHDProvider",
]

