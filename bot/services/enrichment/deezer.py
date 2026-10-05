"""
TG Player - Deezer API Client

Free API for music metadata enrichment.
No API key required.
"""
import asyncio
import logging
import time
from typing import Optional, Dict, List, Any
import aiohttp

from shared.config import get_proxy_url

from shared.matching import (
    clean_for_search,
    normalize_artist,
    normalize_title,
    fuzzy_match_artist,
    fuzzy_match_title,
    ARTIST_MATCH_THRESHOLD,
    TITLE_MATCH_THRESHOLD,
)

logger = logging.getLogger(__name__)


class DeezerClient:
    """Deezer API client for metadata enrichment"""
    
    BASE_URL = "https://api.deezer.com"
    USER_AGENT = "TGPlayer/2.0 (https://github.com/user/tg_player)"
    
    # Rate limiting: ~50 requests per 5 seconds
    RATE_LIMIT_DELAY = 0.15  # 150ms between requests
    
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
    
    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make API request with rate limiting"""
        await self._rate_limit()
        
        session = await self._get_session()
        url = f"{self.BASE_URL}/{endpoint}"
        
        try:
            proxy = get_proxy_url()
            async with session.get(url, params=params, proxy=proxy) as response:
                if response.status == 200:
                    data = await response.json()
                    # Check for API error
                    if isinstance(data, dict) and data.get("error"):
                        logger.warning(f"Deezer API error: {data['error']}")
                        return None
                    return data
                else:
                    logger.warning(f"Deezer API status {response.status}: {url}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"Deezer API timeout: {url}")
            return None
        except Exception as e:
            logger.error(f"Deezer API error: {e}")
            return None
    
    async def search_track(
        self,
        title: str,
        artist: str,
        duration: Optional[int] = None,
        limit: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Search for a track on Deezer.
        
        Args:
            title: Track title
            artist: Artist name
            duration: Optional track duration in seconds for validation
            limit: Max results to fetch
        
        Returns:
            Best matching track data or None
        """
        # Clean search strings
        clean_title = clean_for_search(title)
        clean_artist = clean_for_search(artist)
        
        if not clean_title:
            return None
        
        data = None
        
        # If title has remix / version info, try searching for the full remix string first
        if "(" in title or "[" in title:
            # e.g., "Promises (Skrillex & Nero Remix)" -> query with remix terms
            raw_query = f"{clean_artist} {title}".strip()
            data = await self._request("search", {"q": raw_query, "limit": limit})
        
        # Build standard search query if remix search had no results
        if not data or not data.get("data"):
            if clean_artist:
                query = f'track:"{clean_title}" artist:"{clean_artist}"'
            else:
                query = f'track:"{clean_title}"'
            data = await self._request("search", {"q": query, "limit": limit})
        
        if not data or not data.get("data"):
            # Try simpler search
            query = f"{clean_artist} {clean_title}" if clean_artist else clean_title
            data = await self._request("search", {"q": query, "limit": limit})
        
        if not data or not data.get("data"):
            return None
        
        # Find best match
        best_match = None
        best_score = 0.0
        
        for track in data["data"]:
            deezer_title = track.get("title", "")
            deezer_artist = track.get("artist", {}).get("name", "")
            deezer_duration = track.get("duration", 0)
            
            # Calculate match scores
            artist_score = fuzzy_match_artist(artist, deezer_artist) if artist else 0.5
            
            # STRICT: Skip if artist match is below threshold (prevents matching 'Nero' to 'Nero Isekai')
            if artist and artist_score < ARTIST_MATCH_THRESHOLD:
                continue
                
            title_score = fuzzy_match_title(title, deezer_title)
            
            # Skip if title match is too low
            if title_score < TITLE_MATCH_THRESHOLD:
                continue
            
            # Combined score (weighted)
            combined = (title_score * 0.6) + (artist_score * 0.4)
            
            # Duration validation if duration is available
            if duration and duration > 0 and deezer_duration > 0:
                dur_diff = abs(duration - deezer_duration)
                if dur_diff > 45:
                    # Very large duration discrepancy (> 45s) — skip candidate
                    continue
                elif dur_diff > 25:
                    # Moderate discrepancy — penalize score
                    combined -= 0.15
            
            # Prefer tracks from studio albums over singles/compilations
            album_type = track.get("album", {}).get("type", "")
            if album_type == "album" and combined >= 0.7:
                combined += 0.05
            
            if combined > best_score:
                best_score = combined
                best_match = track
        
        # Verify match quality (threshold 0.70)
        if best_match and best_score >= 0.70:
            return best_match
        
        return None
    
    async def get_album(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Get album details by ID"""
        return await self._request(f"album/{album_id}")
    
    async def get_album_tracks(self, album_id: int) -> List[Dict[str, Any]]:
        """Get all tracks from an album"""
        data = await self._request(f"album/{album_id}/tracks", {"limit": 100})
        
        if data and data.get("data"):
            return data["data"]
        return []
    
    async def get_artist(self, artist_id: int) -> Optional[Dict[str, Any]]:
        """Get artist details by ID"""
        return await self._request(f"artist/{artist_id}")
    
    async def get_artist_top_albums(
        self,
        artist_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get artist's top albums"""
        data = await self._request(
            f"artist/{artist_id}/albums",
            {"limit": limit}
        )
        
        if data and data.get("data"):
            return data["data"]
        return []

    async def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search Deezer for artist info (for artist avatar).
        Returns: dict with name, picture_url, deezer_id or None.
        """
        if not artist_name:
            return None

        clean_name = clean_for_search(artist_name)
        data = await self._request("search/artist", {"q": clean_name, "limit": 5})

        if not data or not data.get("data"):
            return None

        artists = data["data"]

        # Find best matching artist using shared fuzzy matching
        for artist in artists:
            deezer_name = artist.get("name", "")
            if fuzzy_match_artist(artist_name, deezer_name) >= ARTIST_MATCH_THRESHOLD:
                picture = (
                    artist.get("picture_xl")
                    or artist.get("picture_big")
                    or artist.get("picture_medium")
                    or artist.get("picture")
                )
                return {
                    "name": deezer_name,
                    "picture_url": picture,
                    "deezer_id": artist.get("id"),
                }

        # Fallback to first result
        first = artists[0]
        picture = (
            first.get("picture_xl")
            or first.get("picture_big")
            or first.get("picture_medium")
            or first.get("picture")
        )
        return {
            "name": first.get("name"),
            "picture_url": picture,
            "deezer_id": first.get("id"),
        }


# Global instance
deezer_client = DeezerClient()
