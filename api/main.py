"""
TG Player API v2 - Entry Point

Uses new modular architecture with separated routers.
"""
import os
# Prevent SIGSEGV on Python 3.11 in container environments with incompatible precompiled C-extensions
os.environ.setdefault("PROPCACHE_NO_EXTENSIONS", "1")
os.environ.setdefault("YARL_NO_EXTENSIONS", "1")
os.environ.setdefault("MULTIDICT_NO_EXTENSIONS", "1")
os.environ.setdefault("FROZENLIST_NO_EXTENSIONS", "1")
os.environ.setdefault("AIOHTTP_NO_EXTENSIONS", "1")

import time
import re
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from shared.config import get_settings
from shared.bot_factory import create_bot
from shared.database import init_db, close_db

from bot.services.channels import init_channel_service, get_channel_service, start_channel_service, stop_channel_service

from api.routers import auth
from api.routers.library import router as library_router
from api.routers.tracks import router as tracks_router
from api.routers.albums import router as albums_router
from api.routers.artists import router as artists_router
from api.routers.playlists import router as playlists_router
from api.routers.images import router as images_router
from api.routers.player import router as player_router
from api.utils.bot_helpers import close_bot as close_image_bot, close_http_session
from api.routers.social import router as social_router
from api.routers.tags import router as tags_router
from api.routers.ingestion import router as ingestion_router
from api.services.version import get_version_info


logger = logging.getLogger(__name__)
settings = get_settings()

# Global bot instance for API
api_bot: Bot = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Memory-safe in-memory rate limiter with periodic cleanup"""
    
    def __init__(self, app, requests_per_minute: int = 600):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = {}
        self._last_cleanup = time.time()
    
    def _cleanup_stale_ips(self, now: float):
        """Purge empty and expired IP entries to keep memory footprint bounded"""
        minute_ago = now - 60
        stale_keys = [
            ip for ip, timestamps in self.requests.items()
            if not timestamps or timestamps[-1] <= minute_ago
        ]
        for ip in stale_keys:
            del self.requests[ip]
        self._last_cleanup = now
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for audio streaming
        if "/api/player/audio/" in request.url.path:
            return await call_next(request)
        
        forwarded = request.headers.get("X-Forwarded-For")
        real_ip = request.client.host if request.client else "unknown"
        
        # Only trust X-Forwarded-For if request comes from a local/private proxy
        # (e.g. nginx on same machine or docker network)
        if forwarded and real_ip in ("127.0.0.1", "::1", "localhost"):
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = real_ip
        
        now = time.time()
        minute_ago = now - 60
        
        # Periodic cleanup every 60s or if map exceeds 2000 entries
        if now - self._last_cleanup > 60 or len(self.requests) > 2000:
            self._cleanup_stale_ips(now)
        
        # Filter current IP's timestamps
        client_timestamps = [
            t for t in self.requests.get(client_ip, []) if t > minute_ago
        ]
        
        if len(client_timestamps) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."}
            )
        
        client_timestamps.append(now)
        self.requests[client_ip] = client_timestamps
        
        return await call_next(request)


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Add cache-control headers to GET requests for cacheable resources"""
    
    # Cache durations for different endpoints (in seconds)
    CACHE_RULES = {
        r'/api/tracks/global/stats': 120,      # 2 minutes - statistics
        r'/api/library/stats': 120,            # 2 minutes - statistics  
        r'/api/tracks/genres': 1800,           # 30 minutes - genres
        r'/api/tracks/artists': 1800,          # 30 minutes - artists list
        r'/api/artists/.+/info': 900,          # 15 minutes - artist details
        r'/api/artists/.+/tracks': 900,        # 15 minutes - artist tracks
        r'/api/artists/global': 600,           # 10 minutes - global artists
        r'/api/artists': 600,                  # 10 minutes - artists
        r'/api/albums/global': 600,            # 10 minutes - global albums
        r'/api/albums': 600,                   # 10 minutes - albums
        r'/api/playlists': 300,                # 5 minutes - playlists
        r'/api/tracks': 180,                   # 3 minutes - tracks
    }
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Only add cache headers to successful GET requests
        if request.method == "GET" and response.status_code == 200:
            path = request.url.path
            
            # Skip cache headers for streaming and images
            if "/api/player/audio/" in path or "/api/images/" in path:
                return response

            # Skip cache headers if client explicitly requested fresh data
            if (
                request.query_params.get("_t")
                or request.query_params.get("_cb")
                or request.headers.get("cache-control") == "no-cache"
            ):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                return response
            
            # Find matching cache rule
            max_age = None
            for pattern, duration in self.CACHE_RULES.items():
                if re.match(pattern, path):
                    max_age = duration
                    break
            
            if max_age:
                # Add cache-control header
                # - private: only client can cache (not CDN/proxy)
                # - max-age: how long to cache
                # - must-revalidate: check with server when stale
                response.headers["Cache-Control"] = f"private, max-age={max_age}, must-revalidate"
                response.headers["X-Cache-TTL"] = str(max_age)
        
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    global api_bot
    
    await init_db()
    
    # Initialize bot for channel service
    api_bot = create_bot()
    try:
        me = await api_bot.get_me()
        if me.username:
            settings.bot_username = me.username
        if me.first_name:
            settings.bot_name = me.first_name
        logger.info(f"🤖 Connected API bot: {settings.display_name} (@{me.username}, ID: {me.id})")
    except Exception as e:
        logger.warning(f"Could not fetch bot user info in API lifespan: {e}")
        
    init_channel_service(api_bot)
    
    # Start channel service queue worker for auto-forwarding liked tracks
    await start_channel_service()
    
    yield
    
    # Cleanup
    await stop_channel_service()
    if api_bot:
        await api_bot.session.close()
    await close_image_bot()
    await close_http_session()
    await close_db()


app = FastAPI(
    title="TG Player API v2",
    description="API for TG Player Mini App",
    version="2.0.0",
    lifespan=lifespan,
)

# Rate limiting
app.add_middleware(RateLimitMiddleware, requests_per_minute=600)

# Cache control
app.add_middleware(CacheControlMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.webapp_url,         # Production webapp URL
        settings.api_url,            # Same-origin API requests
        "http://localhost:5173",     # Vite dev server
        "http://localhost:8000",     # Local API dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Content-Length", "Accept-Ranges", "X-Cache-TTL"],
)


# Routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(library_router, prefix="/api/library", tags=["Library"])
app.include_router(tracks_router, prefix="/api/tracks", tags=["Tracks"])
app.include_router(albums_router, prefix="/api/albums", tags=["Albums"])
app.include_router(artists_router, prefix="/api/artists", tags=["Artists"])
app.include_router(playlists_router, prefix="/api/playlists", tags=["Playlists"])
app.include_router(images_router, prefix="/api", tags=["Images"])
app.include_router(player_router, prefix="/api/player", tags=["Player"])
app.include_router(social_router, prefix="/api/social", tags=["Social"])
app.include_router(social_router, prefix="/api", tags=["Social-Legacy"])
app.include_router(tags_router, prefix="/api/tracks", tags=["Tags"])
app.include_router(ingestion_router)


# ============== Static Files & SPA Fallback ==============
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# Path to covers, avatars and webapp dist
COVERS_DIR = Path(__file__).parent.parent / "data" / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/covers", StaticFiles(directory=COVERS_DIR), name="covers")

AVATARS_DIR = Path(__file__).parent.parent / "data" / "avatars"
AVATARS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/avatars", StaticFiles(directory=AVATARS_DIR), name="avatars")

WEBAPP_DIST = Path(__file__).parent.parent / "webapp" / "dist"

class ImmutableStaticFiles(StaticFiles):
    """StaticFiles with long-term immutable Cache-Control for hashed Vite assets"""
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Serve static assets (js, css, images, etc.)
if WEBAPP_DIST.exists():
    if (WEBAPP_DIST / "assets").exists():
        app.mount("/assets", ImmutableStaticFiles(directory=WEBAPP_DIST / "assets"), name="assets")
    if (WEBAPP_DIST / "icons").exists():
        app.mount("/icons", StaticFiles(directory=WEBAPP_DIST / "icons"), name="icons")
    
    # Serve other static files from dist root
    @app.get("/manifest.json")
    async def manifest():
        manifest_path = WEBAPP_DIST / "manifest.json"
        if manifest_path.exists():
            return FileResponse(
                manifest_path, 
                media_type="application/manifest+json",
                headers={"Cache-Control": "no-cache"}
            )
        return JSONResponse(status_code=404, content={"error": "not found"})
    
    @app.get("/sw.js")
    async def service_worker():
        sw_path = WEBAPP_DIST / "sw.js"
        if sw_path.exists():
            return FileResponse(
                sw_path, 
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Service-Worker-Allowed": "/"
                }
            )
        return JSONResponse(status_code=404, content={"error": "not found"})
    
    @app.get("/favicon.ico")
    async def favicon():
        favicon_path = WEBAPP_DIST / "favicon.ico"
        if favicon_path.exists():
            return FileResponse(favicon_path)
        icon_path = WEBAPP_DIST / "icons" / "icon-96x96.png"
        if icon_path.exists():
            return FileResponse(icon_path, media_type="image/png")
        return JSONResponse(status_code=404, content={"error": "not found"})
    
    @app.get("/telegram-web-app.js")
    async def telegram_web_app():
        js_path = WEBAPP_DIST / "telegram-web-app.js"
        if js_path.exists():
            return FileResponse(
                js_path,
                media_type="application/javascript",
                headers={"Cache-Control": "public, max-age=604800"}
            )
        return JSONResponse(status_code=404, content={"error": "not found"})


@app.get("/api/health")
async def health():
    maintenance_file = Path(__file__).parent.parent / ".maintenance"
    is_maintenance = (
        os.environ.get("MAINTENANCE_MODE", "false").strip().lower() in ("true", "1", "yes")
        or maintenance_file.exists()
    )
    bot_online = getattr(app.state, "bot_online", True)
    v_info = get_version_info()
    
    return {
        "status": "maintenance" if is_maintenance else "healthy",
        "bot_online": bot_online,
        "maintenance": is_maintenance,
        "version": v_info["version"],
        "build_id": v_info["build_id"],
        "git_commit": v_info["git_commit"],
        "dist_hash": v_info["dist_hash"],
        "server_start_time": v_info["server_start_time"]
    }


@app.get("/api/version")
@app.get("/version.json")
async def version_endpoint():
    v_info = get_version_info()
    return JSONResponse(
        content=v_info,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# SPA Fallback - must be LAST route
# Catches all non-API routes and serves index.html
@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serve index.html for all non-API routes (SPA client-side routing)"""
    from fastapi import HTTPException
    
    # Don't serve index.html for API routes
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")
    
    # Immediately reject scanner probes for PHP, CGI, config files, etc.
    lower_path = full_path.lower()
    blocked_patterns = (
        ".php", ".env", ".git", ".asp", ".aspx", ".jsp", ".action",
        "wp-admin", "wp-login", "wp-content", "xmlrpc", "phpunit",
        "actuator", "config.json", "setup.cgi"
    )
    if any(pattern in lower_path for pattern in blocked_patterns):
        raise HTTPException(status_code=404, detail="Not found")
    
    # If a static file exists in WEBAPP_DIST root, serve it directly
    file_path = (WEBAPP_DIST / full_path).resolve()
    try:
        if WEBAPP_DIST.resolve() in file_path.parents and file_path.is_file():
            return FileResponse(file_path)
    except Exception:
        pass

    index_path = WEBAPP_DIST / "index.html"
    if index_path.exists():
        # Read into memory to avoid BaseHTTPMiddleware conflict with FileResponse
        from fastapi.responses import Response
        content = index_path.read_bytes()
        return Response(
            content=content,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    
    return {"error": "webapp not built", "hint": "run: cd webapp && npm run build"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
