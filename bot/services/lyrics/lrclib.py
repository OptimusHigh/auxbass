"""
TG Player - LRCLIB API Client

Free, open API for synchronized (LRC) and plain lyrics.
No API key required.
https://lrclib.net/docs
"""
import asyncio
import logging
import time
from typing import Optional, Dict, List, Any
import aiohttp

from shared.config import get_proxy_url

from shared.matching import (
    remove_parenthetical,
)

logger = logging.getLogger(__name__)


class LRCLIBClient:
    """LRCLIB API client for lyrics lookup"""
    
    BASE_URL = "https://lrclib.net/api"
    USER_AGENT = "TGPlayer/2.0 (https://github.com/OptimusHigh/auxbass)"
    
    # Rate limiting
    RATE_LIMIT_DELAY = 0.2  # 200ms between requests
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0.0
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.USER_AGENT},
                trust_env=True,
            )
        return self._session
    
    async def _rate_limit(self):
        """Enforce rate limiting"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()
    
    async def close(self):
        """Close HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def get_lyrics(
        self,
        title: str,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        duration: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch lyrics for a track.
        
        Tries exact match first (/api/get), then falls back to cleaned search (/api/search).
        
        Returns dict with:
            - plain_lyrics: str or None
            - synced_lyrics: str (LRC format) or None
            - is_synced: bool
            - is_instrumental: bool
            - source: "lrclib"
        """
        if not title:
            return None
        
        # 1. Try exact match with given metadata
        params = {"track_name": title}
        if artist:
            params["artist_name"] = artist
        if album:
            params["album_name"] = album
        if duration and duration > 0:
            params["duration"] = int(duration)
        
        res = await self._request("get", params=params)
        if res and (res.get("plainLyrics") or res.get("syncedLyrics") or res.get("instrumental")):
            return self._format_result(res)
        
        # 2. Try search with cleaned title and artist if exact match didn't find anything
        cleaned_title = remove_parenthetical(title).strip()
        cleaned_artist = remove_parenthetical(artist or "").strip()
        
        if (cleaned_title and cleaned_title != title) or (cleaned_artist and cleaned_artist != artist):
            search_params = {}
            if cleaned_title:
                search_params["track_name"] = cleaned_title
            if cleaned_artist:
                search_params["artist_name"] = cleaned_artist
            
            res = await self._request("get", params=search_params)
            if res and (res.get("plainLyrics") or res.get("syncedLyrics") or res.get("instrumental")):
                return self._format_result(res)
        
        # 3. Fallback to /api/search
        query = f"{artist or ''} {cleaned_title or title}".strip()
        if query:
            search_results = await self.search(query)
            if search_results and len(search_results) > 0:
                best = search_results[0]
                if best.get("plainLyrics") or best.get("syncedLyrics") or best.get("instrumental"):
                    return self._format_result(best)
        
        return None
    
    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Search lyrics by generic query"""
        if not query:
            return []
        
        params = {"q": query}
        res = await self._request("search", params=params)
        if isinstance(res, list):
            return res
        return []
    
    def _format_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LRCLIB response to standard dict"""
        synced = data.get("syncedLyrics")
        plain = data.get("plainLyrics")
        is_inst = bool(data.get("instrumental", False))
        
        return {
            "plain_lyrics": plain,
            "synced_lyrics": synced,
            "is_synced": bool(synced and len(synced.strip()) > 0),
            "is_instrumental": is_inst,
            "source": "lrclib",
        }
    
    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Make API request with rate limiting and error handling"""
        await self._rate_limit()
        
        session = await self._get_session()
        url = f"{self.BASE_URL}/{endpoint}"
        
        try:
            proxy = get_proxy_url()
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    return None
                else:
                    logger.warning(f"LRCLIB API request failed ({response.status}) for {endpoint}: {params}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"LRCLIB API request timed out for {endpoint}")
            return None
        except Exception as e:
            logger.error(f"LRCLIB API error: {e}")
            return None


lrclib_client = LRCLIBClient()
