"""
TG Player - Cover Search & Suggestions Service

Searches external high-resolution music databases (Apple Music / iTunes, Deezer)
for candidate track and album cover artworks.
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional
import aiohttp

from shared.config import get_proxy_url

logger = logging.getLogger(__name__)

USER_AGENT = "TGPlayer/2.0 (https://github.com/user/tg_player)"


async def _fetch_deezer_covers(query: str, session: aiohttp.ClientSession, limit: int = 8) -> List[Dict[str, Any]]:
    """Search Deezer for track/album covers."""
    url = "https://api.deezer.com/search"
    params = {"q": query, "limit": limit}
    proxy = get_proxy_url()
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=7), proxy=proxy) as resp:
            if resp.status != 200:
                logger.warning(f"Deezer search returned status {resp.status}")
                return []
            data = await resp.json()
            items = data.get("data", [])
            results = []
            seen_covers = set()
            for item in items:
                album = item.get("album") or {}
                cover = album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium")
                if not cover or cover in seen_covers:
                    continue
                seen_covers.add(cover)
                album_id = album.get("id") or item.get("id")
                results.append({
                    "id": f"deezer_{album_id}",
                    "cover_url": cover,
                    "thumbnail_url": album.get("cover_medium") or cover,
                    "source": "deezer",
                    "title": item.get("title") or item.get("title_short"),
                    "artist": (item.get("artist") or {}).get("name"),
                    "album": album.get("title"),
                    "year": None,
                    "width": 1000,
                    "height": 1000,
                })
            return results
    except Exception as e:
        logger.warning(f"Deezer cover search error for query '{query}': {e}")
        return []


async def _fetch_itunes_covers(query: str, session: aiohttp.ClientSession, limit: int = 8) -> List[Dict[str, Any]]:
    """Search Apple Music / iTunes for high-resolution artworks."""
    url = "https://itunes.apple.com/search"
    params = {"term": query, "entity": "song", "limit": limit}
    proxy = get_proxy_url()
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=7), proxy=proxy) as resp:
            if resp.status != 200:
                logger.warning(f"iTunes search returned status {resp.status}")
                return []
            data = await resp.json(content_type=None)
            items = data.get("results", [])
            results = []
            seen_covers = set()
            for item in items:
                raw_art = item.get("artworkUrl100")
                if not raw_art:
                    continue
                # Apple Music CDN serves original high-res square artworks by replacing 100x100bb with 1000x1000bb
                cover = raw_art.replace("100x100bb", "1000x1000bb")
                thumb = raw_art.replace("100x100bb", "300x300bb")
                if cover in seen_covers:
                    continue
                seen_covers.add(cover)
                track_id = item.get("trackId") or item.get("collectionId")
                rel_date = item.get("releaseDate")
                year = rel_date[:4] if rel_date and len(rel_date) >= 4 else None
                results.append({
                    "id": f"itunes_{track_id}",
                    "cover_url": cover,
                    "thumbnail_url": thumb,
                    "source": "itunes",
                    "title": item.get("trackName"),
                    "artist": item.get("artistName"),
                    "album": item.get("collectionName"),
                    "year": year,
                    "width": 1000,
                    "height": 1000,
                })
            return results
    except Exception as e:
        logger.warning(f"iTunes cover search error for query '{query}': {e}")
        return []


async def search_cover_suggestions(query: str, limit_per_source: int = 8) -> List[Dict[str, Any]]:
    """
    Search for track cover suggestions across Deezer and Apple Music concurrently.
    Returns deduplicated list with source badges and album metadata.
    """
    clean_query = query.strip() if query else ""
    if not clean_query:
        return []

    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        deezer_task = _fetch_deezer_covers(clean_query, session, limit=limit_per_source)
        itunes_task = _fetch_itunes_covers(clean_query, session, limit=limit_per_source)
        
        results = await asyncio.gather(deezer_task, itunes_task, return_exceptions=True)

        deezer_items = results[0] if isinstance(results[0], list) else []
        itunes_items = results[1] if isinstance(results[1], list) else []

        combined: List[Dict[str, Any]] = []
        seen_urls = set()

        # Interleave items from both sources for diversity
        max_len = max(len(deezer_items), len(itunes_items))
        for i in range(max_len):
            # Apple Music first (often pristine original artwork)
            if i < len(itunes_items):
                item = itunes_items[i]
                if item["cover_url"] not in seen_urls:
                    seen_urls.add(item["cover_url"])
                    combined.append(item)
            # Deezer
            if i < len(deezer_items):
                item = deezer_items[i]
                if item["cover_url"] not in seen_urls:
                    seen_urls.add(item["cover_url"])
                    combined.append(item)

        return combined
