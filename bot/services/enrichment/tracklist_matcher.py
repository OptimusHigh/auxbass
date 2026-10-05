"""
TG Player - Album Tracklist Matcher

Находит альбом для трека путём поиска в треклистах известных альбомов артиста.
Используется как fallback когда Deezer/Last.fm не могут найти альбом напрямую.
"""
import asyncio
import logging
from typing import Optional, Dict, List, Any
import aiohttp

from shared.config import get_settings, get_proxy_url
from shared.matching import (
    normalize_title,
    normalize_artist,
    fuzzy_match_title,
    TITLE_MATCH_THRESHOLD,
)

logger = logging.getLogger(__name__)


class AlbumTracklistMatcher:
    """
    Находит альбом для трека путём поиска в треклистах.
    
    Стратегия:
    1. Получить список альбомов артиста из Last.fm
    2. Для каждого альбома загрузить треклист
    3. Проверить, есть ли наш трек в треклисте (fuzzy match)
    4. Вернуть найденный альбом с обложкой
    """
    
    LASTFM_API = "https://ws.audioscrobbler.com/2.0/"
    USER_AGENT = "TGPlayer/2.0"
    RATE_LIMIT_DELAY = 0.25
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0.0
        self._cache: Dict[str, Dict] = {}  # Cache album tracklists
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.USER_AGENT},
                trust_env=True,
            )
        return self._session
    
    async def _rate_limit(self):
        import time
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _lastfm_request(self, method: str, params: Dict) -> Optional[Dict]:
        """Make Last.fm API request"""
        settings = get_settings()
        if not settings.lastfm_api_key:
            return None
        
        await self._rate_limit()
        session = await self._get_session()
        
        request_params = {
            "method": method,
            "api_key": settings.lastfm_api_key,
            "format": "json",
            **params
        }
        
        try:
            proxy = get_proxy_url()
            async with session.get(self.LASTFM_API, params=request_params, proxy=proxy) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("error"):
                    return None
                return data
        except Exception as e:
            logger.debug(f"Last.fm request failed: {e}")
            return None
    
    async def get_artist_albums(self, artist: str, limit: int = 20) -> List[Dict]:
        """Получить список альбомов артиста из Last.fm"""
        data = await self._lastfm_request("artist.gettopalbums", {
            "artist": artist,
            "limit": limit,
        })
        
        if not data:
            return []
        
        albums = data.get("topalbums", {}).get("album", [])
        if isinstance(albums, dict):
            albums = [albums]
        
        result = []
        for album in albums:
            name = album.get("name", "")
            
            # Skip compilations and singles
            if not name or name.lower() in ["(null)", "none", ""]:
                continue
            
            # Get largest image
            images = album.get("image", [])
            if isinstance(images, dict):
                images = [images]
            cover_url = None
            for img in reversed(images):
                if img.get("#text"):
                    cover_url = img["#text"]
                    break
            
            result.append({
                "name": name,
                "artist": album.get("artist", {}).get("name", artist),
                "cover_url": cover_url,
            })
        
        return result
    
    async def get_album_tracklist(self, artist: str, album: str) -> List[Dict]:
        """Получить треклист альбома из Last.fm"""
        cache_key = f"{normalize_artist(artist)}|{normalize_title(album)}"
        
        if cache_key in self._cache:
            return self._cache[cache_key].get("tracks", [])
        
        data = await self._lastfm_request("album.getinfo", {
            "artist": artist,
            "album": album,
        })
        
        if not data:
            return []
        
        album_data = data.get("album", {})
        
        # Get cover
        images = album_data.get("image", [])
        if isinstance(images, dict):
            images = [images]
        cover_url = None
        for img in reversed(images):
            if img.get("#text"):
                cover_url = img["#text"]
                break
        
        # Get tracks
        tracks_data = album_data.get("tracks", {}).get("track", [])
        if isinstance(tracks_data, dict):
            tracks_data = [tracks_data]
        
        tracks = []
        for i, t in enumerate(tracks_data, 1):
            name = t.get("name", "")
            if name:
                raw_duration = t.get("duration")
                tracks.append({
                    "name": name,
                    "normalized": normalize_title(name),
                    "track_number": i,
                    "duration": int(raw_duration) if raw_duration is not None else 0,
                })
        
        # Cache result
        self._cache[cache_key] = {
            "name": album_data.get("name", album),
            "artist": album_data.get("artist", artist),
            "cover_url": cover_url,
            "tracks": tracks,
        }
        
        return tracks
    
    async def find_album_for_track(
        self,
        track_title: str,
        artist: str,
        match_threshold: float = 0.75
    ) -> Optional[Dict[str, Any]]:
        """
        Найти альбом для трека путём поиска в треклистах альбомов артиста.
        
        Args:
            track_title: Название трека
            artist: Имя артиста
            match_threshold: Порог совпадения (0.0 - 1.0)
        
        Returns:
            Dict с album_name, cover_url, track_number или None
        """
        if not track_title or not artist:
            return None
        
        track_norm = normalize_title(track_title)
        
        # Получить альбомы артиста
        albums = await self.get_artist_albums(artist)
        
        if not albums:
            logger.debug(f"No albums found for artist: {artist}")
            return None
        
        logger.debug(f"Checking {len(albums)} albums for track: {track_title}")
        
        best_match = None
        best_score = 0.0
        
        for album_info in albums:
            album_name = album_info["name"]
            
            # Загрузить треклист
            tracklist = await self.get_album_tracklist(artist, album_name)
            
            if not tracklist:
                continue
            
            # Искать наш трек в треклисте
            for track in tracklist:
                # Сначала точное совпадение
                if track_norm == track["normalized"]:
                    cache_key = f"{normalize_artist(artist)}|{normalize_title(album_name)}"
                    cached = self._cache.get(cache_key, {})
                    
                    logger.info(f"Found exact match: '{track_title}' in album '{album_name}'")
                    return {
                        "album_name": cached.get("name", album_name),
                        "cover_url": cached.get("cover_url") or album_info.get("cover_url"),
                        "track_number": track["track_number"],
                        "source": "tracklist_match",
                    }
                
                # Fuzzy matching
                score = fuzzy_match_title(track_title, track["name"])
                if score > best_score and score >= match_threshold:
                    best_score = score
                    cache_key = f"{normalize_artist(artist)}|{normalize_title(album_name)}"
                    cached = self._cache.get(cache_key, {})
                    
                    best_match = {
                        "album_name": cached.get("name", album_name),
                        "cover_url": cached.get("cover_url") or album_info.get("cover_url"),
                        "track_number": track["track_number"],
                        "match_score": score,
                        "source": "tracklist_fuzzy",
                    }
        
        if best_match:
            logger.info(
                f"Found fuzzy match ({best_match['match_score']:.2f}): "
                f"'{track_title}' in album '{best_match['album_name']}'"
            )
        
        return best_match


# Global instance
album_tracklist_matcher = AlbumTracklistMatcher()
