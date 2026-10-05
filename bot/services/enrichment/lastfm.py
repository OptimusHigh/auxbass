"""
TG Player - Last.fm API Client

Used for:
- Artist images (Deezer doesn't have good artist images)
- Genre/tag information
- Additional metadata fallback
"""
import asyncio
import logging
import time
from typing import Optional, Dict, List, Any
import aiohttp

from shared.config import get_settings, get_proxy_url

logger = logging.getLogger(__name__)


class LastFmClient:
    """Last.fm API client"""
    
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"
    USER_AGENT = "TGPlayer/2.0 (https://github.com/user/tg_player)"
    
    # Rate limiting: be nice to the API
    RATE_LIMIT_DELAY = 0.25  # 250ms between requests
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0.0
        self._api_key: Optional[str] = None
    
    @property
    def api_key(self) -> Optional[str]:
        """Get API key lazily"""
        if self._api_key is None:
            settings = get_settings()
            self._api_key = settings.lastfm_api_key or ""
        return self._api_key if self._api_key else None
    
    @property
    def is_configured(self) -> bool:
        """Check if API key is configured"""
        return bool(self.api_key)
    
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
    
    async def _request(self, method: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make API request with rate limiting"""
        if not self.api_key:
            return None
        
        await self._rate_limit()
        
        session = await self._get_session()
        
        request_params = {
            "method": method,
            "api_key": self.api_key,
            "format": "json",
            **(params or {})
        }
        
        try:
            proxy = get_proxy_url()
            async with session.get(self.BASE_URL, params=request_params, proxy=proxy) as response:
                if response.status == 200:
                    data = await response.json()
                    # Check for API error
                    if data.get("error"):
                        logger.debug(f"Last.fm API error: {data.get('message')}")
                        return None
                    return data
                else:
                    logger.warning(f"Last.fm API status {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning("Last.fm API timeout")
            return None
        except Exception as e:
            logger.error(f"Last.fm API error: {e}")
            return None
    
    async def get_artist_info(self, artist: str) -> Optional[Dict[str, Any]]:
        """
        Get artist info including image.
        
        Returns:
            Dict with 'name', 'image_url', 'tags', 'bio'
        """
        data = await self._request("artist.getinfo", {"artist": artist})
        
        if not data or not data.get("artist"):
            return None
        
        artist_data = data["artist"]
        
        # Extract best image
        image_url = None
        images = artist_data.get("image", [])
        for img in reversed(images):  # Larger images are last
            if img.get("#text"):
                image_url = img["#text"]
                break
        
        # Extract top tags
        tags = []
        tag_data = artist_data.get("tags", {}).get("tag", [])
        if isinstance(tag_data, list):
            tags = [t["name"] for t in tag_data[:5] if t.get("name")]
        elif isinstance(tag_data, dict) and tag_data.get("name"):
            tags = [tag_data["name"]]
        
        return {
            "name": artist_data.get("name"),
            "image_url": image_url,
            "tags": tags,
            "bio": artist_data.get("bio", {}).get("summary"),
            "listeners": int(artist_data.get("stats", {}).get("listeners", 0)),
        }
    
    async def get_track_info(
        self,
        title: str,
        artist: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get track info.
        
        Returns:
            Dict with track metadata
        """
        data = await self._request("track.getinfo", {
            "track": title,
            "artist": artist,
        })
        
        if not data or not data.get("track"):
            return None
        
        track_data = data["track"]
        
        # Extract top tags
        tags = []
        tag_data = track_data.get("toptags", {}).get("tag", [])
        if isinstance(tag_data, list):
            tags = [t["name"] for t in tag_data[:5] if t.get("name")]
        
        return {
            "name": track_data.get("name"),
            "artist": track_data.get("artist", {}).get("name"),
            "album": track_data.get("album", {}).get("title"),
            "duration": int(track_data.get("duration", 0)) // 1000,  # ms to seconds
            "tags": tags,
            "url": track_data.get("url"),
        }
    
    async def get_album_info(
        self,
        album: str,
        artist: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get album info.
        
        Returns:
            Dict with album metadata
        """
        data = await self._request("album.getinfo", {
            "album": album,
            "artist": artist,
        })
        
        if not data or not data.get("album"):
            return None
        
        album_data = data["album"]
        
        # Extract best image
        image_url = None
        images = album_data.get("image", [])
        for img in reversed(images):
            if img.get("#text"):
                image_url = img["#text"]
                break
        
        # Extract tags
        tags = []
        tag_data = album_data.get("tags", {}).get("tag", [])
        if isinstance(tag_data, list):
            tags = [t["name"] for t in tag_data[:5] if t.get("name")]
        
        # Extract tracks
        tracks = []
        tracks_data = album_data.get("tracks", {}).get("track", [])
        if isinstance(tracks_data, dict):
            tracks_data = [tracks_data]
        if isinstance(tracks_data, list):
            for t in tracks_data:
                if t.get("name"):
                    tracks.append({
                        "name": t.get("name"),
                        "duration": t.get("duration"),
                        "url": t.get("url"),
                    })
        
        return {
            "name": album_data.get("name"),
            "artist": album_data.get("artist"),
            "image_url": image_url,
            "tags": tags,
            "tracks": tracks,
            "release_date": album_data.get("wiki", {}).get("published"),
            "url": album_data.get("url"),
        }
    
    async def search_artist(
        self,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Search for artists"""
        data = await self._request("artist.search", {
            "artist": query,
            "limit": limit,
        })
        
        if not data:
            return []
        
        results = data.get("results", {}).get("artistmatches", {}).get("artist", [])
        
        if isinstance(results, dict):
            results = [results]
        
        artists = []
        for a in results:
            # Extract image
            image_url = None
            images = a.get("image", [])
            for img in reversed(images):
                if img.get("#text"):
                    image_url = img["#text"]
                    break
            
            artists.append({
                "name": a.get("name"),
                "image_url": image_url,
                "listeners": int(a.get("listeners", 0)),
            })
        
        return artists

    # ============== Tag Methods ==============
    
    # Blacklist of garbage/spam tags
    GARBAGE_WORDS = {
        "poop", "piss", "shit", "fuck", "govno", "nigga", "bitch", "ass",
        "best", "favorite", "favourite", "love", "amazing", "awesome", "great",
        "seen live", "my ", "i ", "liar", "overrated", "underrated",
        "spotify", "itunes", "youtube", "tiktok", "viral",
    }
    
    def _filter_tags(self, tags: List[Dict], artist_name: str = "") -> List[str]:
        """
        Filter and clean tags from Last.fm.
        
        Args:
            tags: List of tag dicts with 'name' and 'count' keys
            artist_name: Artist name to exclude as a tag
        
        Returns:
            List of cleaned tag names (up to 5)
        """
        result = []
        artist_lower = artist_name.lower().strip() if artist_name else ""
        seen_lower = set()  # Track seen tags (case-insensitive dedup)
        
        for tag in tags:
            name = tag.get("name", "").strip()
            if not name:
                continue
            
            name_lower = name.lower()
            
            # Skip duplicates (case-insensitive)
            if name_lower in seen_lower:
                continue
            
            # Skip garbage tags
            if any(word in name_lower for word in self.GARBAGE_WORDS):
                continue
            
            # Skip too long tags (likely memes/sentences)
            if len(name) > 30:
                continue
            
            # Skip if tag equals artist name
            if artist_lower and name_lower == artist_lower:
                continue
            
            # Skip pure numbers (years like "2020")
            if name.isdigit():
                continue
            
            seen_lower.add(name_lower)
            result.append(name)
            
            if len(result) >= 5:
                break
        
        return result
    
    async def get_artist_top_tags(self, artist: str) -> List[str]:
        """
        Get top tags for an artist.
        
        Returns:
            List of tag names (up to 5, filtered)
        """
        data = await self._request("artist.getTopTags", {"artist": artist})
        
        if not data or "toptags" not in data:
            return []
        
        tags = data["toptags"].get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        
        return self._filter_tags(tags, artist)
    
    async def get_track_top_tags(self, title: str, artist: str) -> List[str]:
        """
        Get top tags for a specific track.
        
        Returns:
            List of tag names (up to 5, filtered)
        """
        data = await self._request("track.getTopTags", {
            "track": title,
            "artist": artist,
        })
        
        if not data or "toptags" not in data:
            return []
        
        tags = data["toptags"].get("tag", [])
        if isinstance(tags, dict):
            tags = [tags]
        
        return self._filter_tags(tags, artist)
    
    async def get_combined_tags(
        self,
        title: str,
        artist: str,
        max_tags: int = 5
    ) -> List[str]:
        """
        Get combined tags from track and artist.
        
        Strategy:
        1. Get track tags (more specific)
        2. Fill remaining slots with artist tags
        3. Deduplicate while preserving order
        
        Args:
            title: Track title
            artist: Artist name
            max_tags: Maximum number of tags to return
        
        Returns:
            List of unique tag names
        """
        result = []
        seen_lower = set()
        
        # 1. Get track-specific tags first (they're more relevant)
        track_tags = await self.get_track_top_tags(title, artist)
        for tag in track_tags:
            if tag.lower() not in seen_lower:
                seen_lower.add(tag.lower())
                result.append(tag)
        
        # 2. Fill with artist tags if we have room
        if len(result) < max_tags:
            artist_tags = await self.get_artist_top_tags(artist)
            for tag in artist_tags:
                if tag.lower() not in seen_lower:
                    seen_lower.add(tag.lower())
                    result.append(tag)
                    if len(result) >= max_tags:
                        break
        
        return result[:max_tags]


# Global instance
lastfm_client = LastFmClient()
