"""
Tests for External Music Ingestion Framework (SoundCloud, Providers, Registry, Jobs)
"""
import os
import pytest
import asyncio
from bot.services.ingestion.base import EntityType, SourceEntity, TrackMetadata
from bot.services.ingestion.registry import ProviderRegistry, provider_registry
from bot.services.ingestion.job_manager import IngestionJobManager, JobStatus
from bot.services.ingestion.providers.soundcloud import (
    SoundCloudProvider,
    _parse_artist_and_title,
    _improve_sc_thumbnail,
)


def test_soundcloud_url_matching():
    sc = SoundCloudProvider()
    assert sc.can_handle("https://soundcloud.com/artist/track-title")
    assert sc.can_handle("https://m.soundcloud.com/artist/track-title")
    assert sc.can_handle("https://on.soundcloud.com/xyz123")
    assert sc.can_handle("https://soundcloud.com/artist/sets/my-playlist")
    assert not sc.can_handle("https://youtube.com/watch?v=12345")
    assert not sc.can_handle("https://open.spotify.com/track/12345")


def test_soundcloud_title_and_artist_parsing():
    # Format "Artist - Title"
    artist, title = _parse_artist_and_title("Daft Punk - One More Time", "Daft Punk Official")
    assert artist == "Daft Punk"
    assert title == "One More Time"

    # Format without separator: fallback to uploader
    artist, title = _parse_artist_and_title("Just A Track", "Skrillex")
    assert artist == "Skrillex"
    assert title == "Just A Track"


def test_soundcloud_thumbnail_upgrade():
    original = "https://i1.sndcdn.com/artworks-000123-large.jpg"
    upgraded = _improve_sc_thumbnail(original)
    assert upgraded == "https://i1.sndcdn.com/artworks-000123-t500x500.jpg"
    assert _improve_sc_thumbnail(None) is None


def test_provider_registry():
    registry = ProviderRegistry()
    assert "soundcloud" in registry.list_providers()
    
    provider = registry.find_provider("https://soundcloud.com/forss/flickermood")
    assert provider is not None
    assert provider.name == "soundcloud"

    unknown = registry.find_provider("https://unknown-music.org/track/1")
    assert unknown is None


@pytest.mark.asyncio
async def test_job_manager_flow():
    mgr = IngestionJobManager()
    job = await mgr.create_job(
        user_id=12345,
        url="https://soundcloud.com/test/track",
        provider_name="soundcloud",
        entity_type="track",
        title="Test Track",
        total_tracks=1,
        author="Test Artist",
    )

    assert job.id is not None
    assert job.user_id == 12345
    assert job.status == JobStatus.PENDING
    assert job.progress_percent == 0

    # Retrieve
    retrieved = mgr.get_job(job.id)
    assert retrieved == job

    # User jobs
    user_jobs = mgr.get_user_jobs(12345)
    assert len(user_jobs) == 1
    assert user_jobs[0].id == job.id

    # Progress calculation
    job.total_tracks = 10
    job.processed_tracks = 5
    assert job.progress_percent == 50

    job.processed_tracks = 10
    job.status = JobStatus.COMPLETED
    assert job.progress_percent == 100

    # Cancellation
    mgr.cancel_job(job.id)
    assert job.status == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_soundcloud_search_mock(monkeypatch):
    sc = SoundCloudProvider()
    
    async def mock_to_thread(func):
        return {
            "entries": [
                {
                    "title": "Test Artist - Cool Song",
                    "uploader": "Test Artist",
                    "webpage_url": "https://soundcloud.com/test/cool-song",
                    "duration": 180,
                    "thumbnail": "https://i1.sndcdn.com/artworks-0001-large.jpg",
                    "id": "112233",
                }
            ]
        }
    
    monkeypatch.setattr(asyncio, "to_thread", mock_to_thread)
    results = await sc.search("Cool Song", limit=1)
    assert len(results) == 1
    assert results[0].artist == "Test Artist"
    assert results[0].title == "Cool Song"
    assert results[0].duration == 180
    assert "t500x500" in results[0].cover_url


@pytest.mark.asyncio
async def test_job_manager_selective_import():
    mgr = IngestionJobManager()
    selected = ["https://soundcloud.com/test/track-1", "https://soundcloud.com/test/track-3"]
    job = await mgr.create_job(
        user_id=999,
        url="https://soundcloud.com/test/sets/playlist",
        provider_name="soundcloud",
        entity_type="playlist",
        title="Test Playlist",
        total_tracks=len(selected),
        selected_urls=selected,
    )

    assert job.selected_urls == selected
    assert job.total_tracks == 2
    d = job.to_dict()
    assert d["selected_urls"] == selected


@pytest.mark.asyncio
async def test_soundcloud_search_thumbnails_list_mock(monkeypatch):
    sc = SoundCloudProvider()

    async def mock_to_thread(func):
        return {
            "entries": [
                {
                    "title": "andy warhol",
                    "uploader": "Kai Angel & 9mice",
                    "webpage_url": "https://soundcloud.com/test/andy-warhol",
                    "duration": 173,
                    "thumbnail": None,
                    "thumbnails": [
                        {"id": "mini", "url": "https://i1.sndcdn.com/artworks-123-mini.jpg"},
                        {"id": "t500x500", "url": "https://i1.sndcdn.com/artworks-123-t500x500.jpg"},
                    ],
                    "id": "999888",
                }
            ]
        }

    monkeypatch.setattr(asyncio, "to_thread", mock_to_thread)
    results = await sc.search("andy warhol", limit=1)
    assert len(results) == 1
    assert results[0].cover_url == "https://i1.sndcdn.com/artworks-123-t500x500.jpg"


@pytest.mark.asyncio
async def test_soundcloud_user_profile_and_likes_mock(monkeypatch):
    sc = SoundCloudProvider()
    sc._cached_client_id = "test_client_id"

    # Mock resolve_user_profile HTTP call
    class MockResponse:
        def __init__(self, data, status=200):
            self._data = data
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def json(self):
            return self._data

        async def text(self):
            return str(self._data)

    profile_payload = {
        "id": 123456,
        "permalink": "cool_artist",
        "username": "Cool Artist",
        "avatar_url": "https://i1.sndcdn.com/avatars-000-large.jpg",
        "permalink_url": "https://soundcloud.com/cool_artist",
        "likes_count": 42,
        "track_count": 10,
    }

    likes_payload = {
        "collection": [
            {
                "created_at": "2026-01-01T00:00:00Z",
                "track": {
                    "id": 98765,
                    "title": "Banger Track",
                    "user": {"username": "Another Producer", "permalink": "producer"},
                    "permalink_url": "https://soundcloud.com/producer/banger-track",
                    "duration": 180000,
                    "artwork_url": "https://i1.sndcdn.com/artworks-999-large.jpg",
                },
            }
        ],
        "next_href": "https://api-v2.soundcloud.com/users/123456/likes?offset=2",
    }

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, **kwargs):
            if "/resolve" in str(url):
                return MockResponse(profile_payload)
            if "/likes" in str(url):
                return MockResponse(likes_payload)
            return MockResponse({}, status=404)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", MockSession)

    # 1. Test profile resolution
    prof = await sc.resolve_user_profile("cool_artist")
    assert prof["external_id"] == "123456"
    assert prof["username"] == "cool_artist"
    assert prof["display_name"] == "Cool Artist"
    assert prof["likes_count"] == 42
    assert "t500x500" in prof["avatar_url"]

    # 2. Test user likes fetching
    likes, next_cursor = await sc.fetch_user_likes("123456", limit=10)
    assert len(likes) == 1
    assert likes[0].title == "Banger Track"
    assert likes[0].artist == "Another Producer"
    assert likes[0].duration == 180
    assert "t500x500" in likes[0].cover_url
    assert next_cursor == "https://api-v2.soundcloud.com/users/123456/likes?offset=2"


@pytest.mark.asyncio
async def test_soundcloud_drm_protection_handling(monkeypatch):
    sc = SoundCloudProvider()
    meta = TrackMetadata(
        provider_name="soundcloud",
        url="https://soundcloud.com/pendulum/fasten-your-seatbelt-ft-the",
        title="Fasten Your Seatbelt",
        artist="Pendulum",
    )

    class MockYDL:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def download(self, urls):
            import yt_dlp
            raise yt_dlp.utils.DownloadError("\x1b[0;31mERROR:\x1b[0m [soundcloud] 1236088255: This video is DRM protected")
        def extract_info(self, url, download=True):
            import yt_dlp
            raise yt_dlp.utils.DownloadError("\x1b[0;31mERROR:\x1b[0m [soundcloud] 1236088255: This video is DRM protected")

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYDL)

    with pytest.raises(ValueError) as exc_info:
        await sc.download_track(meta, "/tmp/fake_dir")

    assert "защищён DRM" in str(exc_info.value)
    assert "\x1b[" not in str(exc_info.value)


def test_quick_import_request_and_search_response_schemas():
    from api.routers.ingestion import QuickImportRequest, SearchItemResponse

    # Defaults to add_to_library = False
    req = QuickImportRequest(url="https://soundcloud.com/artist/track")
    assert req.add_to_library is False

    req_add = QuickImportRequest(url="https://soundcloud.com/artist/track", add_to_library=True)
    assert req_add.add_to_library is True

    search_item = SearchItemResponse(
        provider="soundcloud",
        url="https://soundcloud.com/artist/track",
        title="Track",
        artist="Artist",
        in_library=True,
        in_channel=False,
        already_in_tg=True,
    )
    assert search_item.in_library is True
    assert search_item.already_in_tg is True


def test_spotify_url_matching_and_registry():
    from bot.services.ingestion.providers.spotify import SpotifyProvider
    sp = SpotifyProvider()

    assert sp.can_handle("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert sp.can_handle("https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=123")
    assert sp.can_handle("https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa")
    assert sp.can_handle("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert sp.can_handle("spotify:track:4cOdK2wGLETKBW3PvgPWqT")
    assert sp.can_handle("spotify:album:4m2880jivSbbyEGAKfITCa")
    assert not sp.can_handle("https://soundcloud.com/artist/track")
    assert not sp.can_handle("https://youtube.com/watch?v=123")

    # Registry lookup
    reg = ProviderRegistry()
    found = reg.find_provider("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert found is not None
    assert found.name == "spotify"


@pytest.mark.asyncio
async def test_spotify_resolve_entity_and_tracklist(monkeypatch):
    from bot.services.ingestion.providers.spotify import SpotifyProvider
    sp = SpotifyProvider()

    mock_track_data = {
        "title": "Never Be Like You",
        "name": "Never Be Like You",
        "artists": [{"name": "Flume"}, {"name": "Kai"}],
        "duration": 234000,
        "id": "abc123track",
        "visualIdentity": {
            "image": [{"url": "https://i.scdn.co/image/ab67616d0000b273_hd.jpg", "width": 640}]
        },
        "album": {"name": "Skin"},
    }

    async def mock_fetch_embed(etype, eid):
        return mock_track_data

    monkeypatch.setattr(sp, "_fetch_embed_data", mock_fetch_embed)

    entity = await sp.resolve_entity("https://open.spotify.com/track/abc123track")
    assert entity.entity_type == EntityType.TRACK
    assert entity.title == "Never Be Like You"
    assert entity.author == "Flume, Kai"
    assert entity.cover_url == "https://i.scdn.co/image/ab67616d0000b273_hd.jpg"

    tracks = await sp.fetch_tracklist(entity)
    assert len(tracks) == 1
    assert tracks[0].title == "Never Be Like You"
    assert tracks[0].artist == "Flume, Kai"
    assert tracks[0].duration == 234
    assert tracks[0].album == "Skin"


@pytest.mark.asyncio
async def test_audio_resolver_matching_logic():
    from bot.services.ingestion.audio_resolver import AudioResolver
    resolver = AudioResolver()

    target = TrackMetadata(
        provider_name="spotify",
        url="https://open.spotify.com/track/123",
        title="Fasten Your Seatbelt",
        artist="Pendulum",
        duration=190,
    )

    candidates = [
        # Bad duration candidate (>15s difference)
        {"title": "Pendulum - Fasten Your Seatbelt (Extended Mix)", "duration": 340, "webpage_url": "https://soundcloud.com/test/long"},
        # Completely different song
        {"title": "Another Artist - Completely Different Song", "duration": 190, "webpage_url": "https://soundcloud.com/test/diff"},
        # Good candidate matching title and close duration
        {"title": "Pendulum - Fasten Your Seatbelt", "duration": 191, "webpage_url": "https://soundcloud.com/test/good"},
    ]

    best = resolver._pick_best_candidate(target, candidates, exclude_urls=set())
    assert best is not None
    assert best["webpage_url"] == "https://soundcloud.com/test/good"


def test_audio_resolver_relaxed_duration_fallback():
    from bot.services.ingestion.audio_resolver import AudioResolver

    resolver = AudioResolver()
    target = TrackMetadata(
        provider_name="spotify",
        url="https://open.spotify.com/track/123",
        title="Fasten Your Seatbelt",
        artist="Pendulum",
        duration=190,
    )

    candidates = [
        # Candidate with 25s intro/outro difference (190 vs 215 = 25s diff), matching title
        {"title": "Pendulum - Fasten Your Seatbelt (Official Audio)", "duration": 215, "webpage_url": "https://youtube.com/watch?v=intro"},
        # Candidate with huge difference (>35s)
        {"title": "Pendulum - Fasten Your Seatbelt (Live)", "duration": 300, "webpage_url": "https://youtube.com/watch?v=long"},
    ]

    ranked = resolver._rank_candidates(target, candidates, exclude_urls=set())
    assert len(ranked) == 1
    assert ranked[0]["webpage_url"] == "https://youtube.com/watch?v=intro"


@pytest.mark.asyncio
async def test_soundcloud_drm_fallback_to_audio_resolver(monkeypatch):
    from bot.services.ingestion.providers.soundcloud import SoundCloudProvider
    from bot.services.ingestion.base import DownloadedAudio
    sc = SoundCloudProvider()

    meta = TrackMetadata(
        provider_name="soundcloud",
        url="https://soundcloud.com/pendulum/fasten-your-seatbelt-ft-the",
        title="Fasten Your Seatbelt",
        artist="Pendulum",
        duration=190,
    )

    class MockYDLDRM:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def download(self, urls):
            import yt_dlp
            raise yt_dlp.utils.DownloadError("ERROR: [soundcloud] 1236088255: This video is DRM protected")
        def extract_info(self, url, download=True):
            import yt_dlp
            raise yt_dlp.utils.DownloadError("ERROR: [soundcloud] 1236088255: This video is DRM protected")

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYDLDRM)

    # Mock audio_resolver.resolve_and_download to return valid audio
    called = {}
    async def mock_resolve_and_download(track_meta, temp_dir, exclude_urls=None, progress_hook=None, **kwargs):
        called["resolved"] = True
        called["exclude_urls"] = exclude_urls
        return DownloadedAudio(
            audio_path="/tmp/fake_resolved.mp3",
            metadata=track_meta,
            file_size=5000000,
            mime_type="audio/mpeg",
        )

    from bot.services.ingestion.audio_resolver import audio_resolver
    monkeypatch.setattr(audio_resolver, "resolve_and_download", mock_resolve_and_download)

    audio = await sc.download_track(meta, "/tmp/fake_dir")
    assert called.get("resolved") is True
    assert meta.url in called.get("exclude_urls", set())
    assert audio.audio_path == "/tmp/fake_resolved.mp3"


@pytest.mark.asyncio
async def test_soundcloud_30s_preview_stream_triggers_audio_resolver_when_chunk_only_false(monkeypatch, tmp_path):
    from bot.services.ingestion.providers.soundcloud import SoundCloudProvider
    from bot.services.ingestion.base import DownloadedAudio
    sc = SoundCloudProvider()

    meta = TrackMetadata(
        provider_name="soundcloud",
        url="https://soundcloud.com/greenday/troubled-times",
        title="Troubled Times",
        artist="Green Day",
        duration=184,
        extra={"uploader": "Green Day", "is_soundcloud": True, "is_drm_preview": True},
    )

    class MockYDLPreview:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=True):
            (tmp_path / "audio.mp3").write_bytes(b"\x00" * 476055)
            return {
                "id": "123456",
                "title": "Troubled Times",
                "uploader": "Green Day",
                "duration": 30.0,
                "format_id": "http_mp3_0_0_preview",
                "formats": [{"format_id": "http_mp3_0_0_preview"}],
            }

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", MockYDLPreview)

    called = {}
    async def mock_resolve_and_download(track_meta, temp_dir, exclude_urls=None, progress_hook=None, **kwargs):
        called["resolved"] = True
        called["exclude_urls"] = exclude_urls
        return DownloadedAudio(
            audio_path="/tmp/fake_full_track.mp3",
            metadata=track_meta,
            file_size=5167929,
            mime_type="audio/mpeg",
        )

    from bot.services.ingestion.audio_resolver import audio_resolver
    monkeypatch.setattr(audio_resolver, "resolve_and_download", mock_resolve_and_download)

    audio = await sc.download_track(meta, str(tmp_path), chunk_only=False)
    assert called.get("resolved") is True
    assert meta.url in called.get("exclude_urls", set())
    assert audio.file_size == 5167929


def test_exportify_csv_parsing():
    import csv
    import io

    csv_data = (
        'Track URI,Track Name,Artist URI(s),Artist Name(s),Album URI,Album Name,Album Release Date,Album Image URL,Track Duration (ms),ISRC,Added By,Added At\n'
        'spotify:track:4cOdK2wGLETKBW3PvgPWqT,Never Gonna Give You Up,spotify:artist:0gxyHStUsqpMadRV0Di1Qt,Rick Astley,spotify:album:1kugj9e22r9w,Whenever You Need Somebody,1987-11-12,https://i.scdn.co/image/ab67616d0000b273,213573,GBARL8700072,spotify:user:123,2023-01-01T12:00:00Z\n'
        'spotify:track:12345,Fasten Your Seatbelt - Original Mix,spotify:artist:999,"Pendulum, The Freestylers",spotify:album:888,Hold Your Colour,2005-07-25,https://i.scdn.co/image/cover123,278000,GBAHT0500123,spotify:user:123,2023-01-02T12:00:00Z\n'
    )

    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    assert len(rows) == 2

    r1 = rows[0]
    assert r1["Track Name"] == "Never Gonna Give You Up"
    assert r1["Artist Name(s)"] == "Rick Astley"
    assert int(float(r1["Track Duration (ms)"]) / 1000) == 213
    assert r1["ISRC"] == "GBARL8700072"

    r2 = rows[1]
    assert r2["Track Name"] == "Fasten Your Seatbelt - Original Mix"
    assert r2["Artist Name(s)"] == "Pendulum, The Freestylers"
    assert int(float(r2["Track Duration (ms)"]) / 1000) == 278


def test_robust_norm_title_and_version_stripping():
    from bot.services.ingestion.pipeline import _robust_norm_title

    assert _robust_norm_title("Fasten Your Seatbelt - Original Mix") == "fasten your seatbelt"
    assert _robust_norm_title("Fasten Your Seatbelt (feat. The Freestylers)") == "fasten your seatbelt"
    assert _robust_norm_title("In The End - 2020 Remaster") == "in the end"
    assert _robust_norm_title("Midnight City - Radio Edit") == "midnight city"
    assert _robust_norm_title("Midnight City (Original Mix)") == "midnight city"
    assert _robust_norm_title("Clint Eastwood - Ed Case/Sweetie Irie Re-Fix") == "clint eastwood"
    assert _robust_norm_title("Clint Eastwood (Ed Case/Sweetie Irie Refix)") == "clint eastwood"


@pytest.mark.asyncio
async def test_find_existing_track_tolerant_matching():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models import Base, Track, User
    from bot.services.ingestion.pipeline import _find_existing_track

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        user = User(id=123, username="tester")
        session.add(user)
        await session.flush()

        t1 = Track(
            id=1,
            file_id="fid_1",
            file_unique_id="fuid_1",
            title="Fasten Your Seatbelt (feat. The Freestylers)",
            artist="Pendulum",
            normalized_artist="pendulum",
            duration=278,
            uploader_id=123,
        )
        t2 = Track(
            id=2,
            file_id="fid_2",
            file_unique_id="fuid_2",
            title="Around the World",
            artist="Daft Punk",
            normalized_artist="daft punk",
            duration=429,
            uploader_id=123,
        )
        session.add_all([t1, t2])
        await session.commit()

        # 1. Match with version suffix and collaborator: "Fasten Your Seatbelt - Original Mix"
        found = await _find_existing_track(
            title="Fasten Your Seatbelt - Original Mix",
            artist="Pendulum, The Freestylers",
            duration=278,
            session=session,
        )
        assert found is not None
        assert found.id == 1

        # 2. Match exact Daft Punk with close duration (±1s)
        found2 = await _find_existing_track(
            title="Around the World",
            artist="Daft Punk",
            duration=428,
            session=session,
        )
        assert found2 is not None
        assert found2.id == 2

        # 3. Same artist, completely different track duration/title -> should NOT match!
        found3 = await _find_existing_track(
            title="One More Time",
            artist="Daft Punk",
            duration=320,
            session=session,
        )
        assert found3 is None

    await engine.dispose()


def test_entity_type_tracks_enum():
    from bot.services.ingestion.base import EntityType

    assert EntityType("tracks") == EntityType.TRACKS
    assert EntityType.TRACKS.value == "tracks"


def test_bot_music_url_pattern_matching():
    import re

    pattern = re.compile(r"https?://(?:(?:m|www)\.)?(?:soundcloud\.com|on\.soundcloud\.com|open\.spotify\.com|spotify\.link)/\S+")

    # SoundCloud URLs
    assert pattern.search("https://soundcloud.com/threepux/move-for-me-threepux-flip?si=123")
    assert pattern.search("https://m.soundcloud.com/artist/track-name")
    assert pattern.search("https://on.soundcloud.com/abcdef")
    assert pattern.search("Check this out: https://soundcloud.com/artist/sets/cool-album bro")

    # Spotify URLs
    assert pattern.search("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    assert pattern.search("https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa")
    assert pattern.search("https://spotify.link/xYz123")

    # Non-music URLs
    assert not pattern.search("https://youtube.com/watch?v=123")
    assert not pattern.search("https://google.com")
    assert not pattern.search("hello world")


def test_ingestion_job_upload_tracking():
    from bot.services.ingestion.job_manager import IngestionJob, JobStatus

    job = IngestionJob(
        id="job-123",
        user_id=456,
        url="https://soundcloud.com/artist/track",
        provider_name="soundcloud",
        entity_type="track",
        title="Test Track",
    )
    assert job.uploaded_chat_id is None
    assert job.uploaded_message_id is None

    job.uploaded_chat_id = 456
    job.uploaded_message_id = 789
    assert job.uploaded_chat_id == 456
    assert job.uploaded_message_id == 789


def test_soundcloud_tracks_and_playlists_schemas():
    from api.routers.ingestion import (
        SoundCloudPlaylistItem,
        UserPlaylistsResponse,
        PlaylistTracksResponse,
        UserTracksResponse,
        SoundCloudTrackItem,
        ExternalAccountResponse,
        StartImportRequest
    )

    acc = ExternalAccountResponse(
        provider="soundcloud",
        username="hartracer",
        display_name="hartracer",
        profile_url="https://soundcloud.com/hartracer",
        avatar_url=None,
        likes_count=430,
        tracks_count=33,
        connected=True,
    )

    tr = SoundCloudTrackItem(
        url="https://soundcloud.com/hartracer/track1",
        title="Test Track 1",
        artist="hartracer",
        duration=180,
        in_library=True,
        already_in_tg=True,
        track_id=1,
    )
    user_tracks_resp = UserTracksResponse(
        provider="soundcloud",
        account=acc,
        total_tracks=33,
        items=[tr],
    )
    assert len(user_tracks_resp.items) == 1
    assert user_tracks_resp.items[0].in_library is True

    pl = SoundCloudPlaylistItem(
        id="12345",
        title="Test Playlist",
        permalink_url="https://soundcloud.com/hartracer/sets/test",
        track_count=10,
        duration=1200,
        author="hartracer",
        is_liked=False,
    )
    user_pl_resp = UserPlaylistsResponse(
        provider="soundcloud",
        account=acc,
        total_playlists=1,
        items=[pl],
    )
    assert user_pl_resp.total_playlists == 1
    assert user_pl_resp.items[0].title == "Test Playlist"

    pl_tracks_resp = PlaylistTracksResponse(
        provider="soundcloud",
        playlist=pl,
        total_tracks=1,
        tracks=[tr],
    )
    assert pl_tracks_resp.total_tracks == 1

    req = StartImportRequest(
        url="https://soundcloud.com/hartracer/sets/test",
        create_playlist=True,
        playlist_name="My Synced Playlist"
    )
    assert req.create_playlist is True
    assert req.playlist_name == "My Synced Playlist"


@pytest.mark.asyncio
async def test_user_import_file_model():
    from shared.models import User, UserImportFile
    from shared.database import init_db, get_session
    from sqlalchemy import select

    await init_db()
    async with get_session() as session:
        user = await session.get(User, 999999)
        if not user:
            user = User(id=999999, username="test_import_user")
            session.add(user)
            await session.commit()

        import_file = UserImportFile(
            user_id=999999,
            provider="spotify",
            filename="Liked Songs.csv",
            file_id="tg_doc_file_id_123",
            file_size=45000,
            total_tracks=150,
            channel_id=-100123456789,
            message_id=42,
            summary_json='{"total_tracks": 150, "new_tracks_count": 120}',
        )
        session.add(import_file)
        await session.commit()

        res = await session.scalar(
            select(UserImportFile)
            .where(UserImportFile.user_id == 999999, UserImportFile.provider == "spotify")
            .order_by(UserImportFile.id.desc())
        )
        assert res is not None
        assert res.filename == "Liked Songs.csv"
        assert res.total_tracks == 150
        assert res.file_id == "tg_doc_file_id_123"
        assert res.channel_id == -100123456789


@pytest.mark.asyncio
async def test_playlist_custom_cover_url_creation():
    """Verify that Playlist model correctly accepts custom_cover_url and not cover_url."""
    from shared.models import Playlist, User
    from shared.database import init_db, get_session
    from sqlalchemy import select

    await init_db()
    async with get_session() as session:
        user = await session.get(User, 888888)
        if not user:
            user = User(id=888888, username="test_sc_playlist_user")
            session.add(user)
            await session.commit()

        # Instantiating with custom_cover_url must succeed without TypeError
        pl = Playlist(
            owner_id=888888,
            name="SoundCloud Test Playlist",
            description="Синхронизировано из SoundCloud",
            custom_cover_url="https://i1.sndcdn.com/artworks-123456-t500x500.jpg",
            is_public=False,
        )
        session.add(pl)
        await session.commit()
        await session.refresh(pl)

        assert pl.id is not None
        assert pl.custom_cover_url == "https://i1.sndcdn.com/artworks-123456-t500x500.jpg"
        assert pl.name == "SoundCloud Test Playlist"


def test_youtube_url_matching():
    from bot.services.ingestion.providers.youtube import YouTubeMusicProvider
    yt = YouTubeMusicProvider()
    assert yt.can_handle("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt.can_handle("https://youtu.be/dQw4w9WgXcQ")
    assert yt.can_handle("https://music.youtube.com/playlist?list=PL1234567890")
    assert yt.can_handle("https://www.youtube.com/playlist?list=PL1234567890")
    assert not yt.can_handle("https://soundcloud.com/artist/track")
    assert not yt.can_handle("https://open.spotify.com/track/123")


def test_youtube_artist_and_title_parsing():
    from bot.services.ingestion.providers.youtube import _parse_artist_and_title

    # 1. Topic channel with "Artist - Topic"
    a, t = _parse_artist_and_title("Brain Stew", uploader="Green Day - Topic")
    assert a == "Green Day"
    assert t == "Brain Stew"

    # 2. "Artist - Title" format in title
    a, t = _parse_artist_and_title("Green Day - Basket Case (Official Music Video)", uploader="Reprise Records")
    assert a == "Green Day"
    assert t == "Basket Case"

    # 3. Explicit track and artist fields
    a, t = _parse_artist_and_title("Whatever", track_field="Holiday", artist_field="Green Day")
    assert a == "Green Day"
    assert t == "Holiday"


@pytest.mark.asyncio
async def test_youtube_search_mock(monkeypatch):
    from bot.services.ingestion.providers.youtube import YouTubeMusicProvider
    yt = YouTubeMusicProvider()

    async def mock_to_thread(func):
        return [
            {
                "id": "dQw4w9WgXcQ",
                "title": "Rick Astley - Never Gonna Give You Up",
                "uploader": "Rick Astley",
                "duration": 213,
                "thumbnails": [
                    {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "width": 480, "height": 360}
                ],
            }
        ]

    monkeypatch.setattr(asyncio, "to_thread", mock_to_thread)
    results = await yt.search("Never Gonna Give You Up", limit=1)
    assert len(results) == 1
    assert results[0].artist == "Rick Astley"
    assert results[0].title == "Never Gonna Give You Up"
    assert results[0].duration == 213
    assert results[0].external_id == "dQw4w9WgXcQ"
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in results[0].url


@pytest.mark.asyncio
async def test_audio_resolver_soundcloud_drm_falls_back_to_youtube(monkeypatch, tmp_path):
    """
    Simulates:
    1. SoundCloud search returns a candidate that throws 'This video is DRM protected'
    2. AudioResolver catches this, logs fallback to YouTube Music
    3. YouTube candidate search returns valid stream candidate
    4. AudioResolver downloads the YouTube audio successfully
    """
    from bot.services.ingestion.audio_resolver import AudioResolver
    from bot.services.ingestion.base import TrackMetadata
    resolver = AudioResolver()

    meta = TrackMetadata(
        provider_name="spotify",
        url="https://open.spotify.com/track/spotify123",
        title="Brain Stew",
        artist="Green Day",
        duration=193,
    )

    # Mock SoundCloud candidates search
    async def mock_sc_candidates(query, limit=10):
        return [
            {
                "title": "Green Day - Brain Stew (SoundCloud Go+ DRM)",
                "duration": 193,
                "webpage_url": "https://soundcloud.com/greenday/brain-stew-drm",
            }
        ]

    # Mock YouTube candidates search
    async def mock_yt_candidates(query, limit=6):
        return [
            {
                "id": "yt_video_123",
                "title": "Green Day - Brain Stew (Official Audio)",
                "duration": 193,
                "webpage_url": "https://www.youtube.com/watch?v=yt_video_123",
            }
        ]

    monkeypatch.setattr(resolver, "_search_candidates", mock_sc_candidates)
    monkeypatch.setattr(resolver, "_search_youtube_candidates", mock_yt_candidates)

    # Mock _download_stream: fail if soundcloud url (DRM), succeed if youtube url
    download_calls = []

    async def mock_download_stream(url, temp_dir, progress_hook=None, **kwargs):
        download_calls.append(url)
        if "soundcloud.com" in url:
            raise Exception("ERROR: [soundcloud] 1162789264: This video is DRM protected")
        # Create a dummy audio file in temp_dir
        fake_audio = os.path.join(temp_dir, "resolved_audio.mp3")
        with open(fake_audio, "wb") as f:
            f.write(b"ID3" + b"\x00" * 1024)
        return fake_audio

    monkeypatch.setattr(resolver, "_download_stream", mock_download_stream)

    result = await resolver.resolve_and_download(meta, str(tmp_path))

    # Assert that SoundCloud candidate was tried first
    assert any("soundcloud.com" in c for c in download_calls)
    # Assert that YouTube candidate was tried and succeeded as fallback
    assert any("youtube.com" in c for c in download_calls)
    assert result.audio_path.endswith(".mp3")
    assert result.metadata.title == "Brain Stew"


def test_version_markers_extraction():
    from shared.matching import extract_version_markers

    assert extract_version_markers("Wake Me Up When September Ends") == set()
    assert "live" in extract_version_markers("Wake Me Up When September Ends (Live at Milton Keynes)")
    assert "live" in extract_version_markers("Wake Me Up When September Ends - Live from Bullet in a Bible")
    assert "acoustic" in extract_version_markers("Wake Me Up When September Ends (Acoustic Version)")
    assert "remix" in extract_version_markers("Wake Me Up When September Ends (Steve Aoki Remix)")
    assert "instrumental" in extract_version_markers("Wake Me Up When September Ends (Instrumental)")


def test_audio_resolver_version_alignment_prefers_studio_for_studio_target():
    from bot.services.ingestion.audio_resolver import AudioResolver

    resolver = AudioResolver()
    target = TrackMetadata(
        provider_name="spotify",
        url="https://open.spotify.com/track/wake_me_up",
        title="Wake Me Up When September Ends",
        artist="Green Day",
        duration=285,  # 4:45 studio
    )

    candidates = [
        # Candidate 1: Live version, duration matches closely (4:42 vs 4:45)
        {
            "id": "live_1",
            "title": "Green Day - Wake Me Up When September Ends (Live at Milton Keynes)",
            "duration": 282,
            "uploader": "Green Day",
            "webpage_url": "https://www.youtube.com/watch?v=live_1",
        },
        # Candidate 2: Studio album version from Topic channel (4:45)
        {
            "id": "studio_1",
            "title": "Wake Me Up When September Ends",
            "duration": 285,
            "uploader": "Green Day - Topic",
            "webpage_url": "https://www.youtube.com/watch?v=studio_1",
        },
        # Candidate 3: Official Music Video (7:14, rejected by duration diff > 15s)
        {
            "id": "mv_1",
            "title": "Green Day - Wake Me Up When September Ends (Official Music Video)",
            "duration": 434,
            "uploader": "Green Day",
            "webpage_url": "https://www.youtube.com/watch?v=mv_1",
        },
    ]

    ranked = resolver._rank_candidates(target, candidates, set())
    assert len(ranked) >= 2
    # Studio Topic track MUST be ranked first!
    assert ranked[0]["id"] == "studio_1"
    # Live track should be ranked after, with a huge penalty
    assert ranked[1]["id"] == "live_1"
    assert ranked[1]["_penalty"] > 200.0


def test_audio_resolver_version_alignment_prefers_live_for_live_target():
    from bot.services.ingestion.audio_resolver import AudioResolver

    resolver = AudioResolver()
    # User explicitly wants the Live version!
    target = TrackMetadata(
        provider_name="youtube",
        url="https://www.youtube.com/watch?v=live_target",
        title="Wake Me Up When September Ends (Live)",
        artist="Green Day",
        duration=282,
    )

    candidates = [
        # Candidate 1: Studio album version
        {
            "id": "studio_1",
            "title": "Wake Me Up When September Ends",
            "duration": 285,
            "uploader": "Green Day - Topic",
            "webpage_url": "https://www.youtube.com/watch?v=studio_1",
        },
        # Candidate 2: Live recording
        {
            "id": "live_1",
            "title": "Green Day - Wake Me Up When September Ends (Live at Milton Keynes)",
            "duration": 282,
            "uploader": "Green Day",
            "webpage_url": "https://www.youtube.com/watch?v=live_1",
        },
    ]

    ranked = resolver._rank_candidates(target, candidates, set())
    assert len(ranked) == 2
    # Because target is Live, the Live candidate MUST be preferred!
    assert ranked[0]["id"] == "live_1"


def test_pipeline_score_candidate_distinguishes_live_and_studio():
    from bot.services.ingestion.pipeline import _score_candidate
    from shared.matching import clean_track_metadata, normalize_artist, normalize_title
    from bot.services.ingestion.pipeline import _robust_norm_title
    from shared.models import Track

    # DB track is a Live recording
    live_track = Track(
        id=1,
        title="Wake Me Up When September Ends (Live at Milton Keynes)",
        artist="Green Day",
        normalized_artist="green day",
        duration=282,
    )

    # 1. User wants Studio version -> Must NOT match the live track in DB!
    clean_t, clean_a = clean_track_metadata("Wake Me Up When September Ends", "Green Day")
    score_studio = _score_candidate(
        live_track,
        clean_title=clean_t,
        clean_artist=clean_a,
        norm_title=normalize_title(clean_t),
        norm_artist=normalize_artist(clean_a),
        robust_title=_robust_norm_title(clean_t),
        duration=285,
    )
    assert score_studio == -1, "Studio request must not match live recording in DB"

    # 2. User wants Live version -> Must match!
    clean_live_t, clean_live_a = clean_track_metadata("Wake Me Up When September Ends (Live)", "Green Day")
    score_live = _score_candidate(
        live_track,
        clean_title=clean_live_t,
        clean_artist=clean_live_a,
        norm_title=normalize_title(clean_live_t),
        norm_artist=normalize_artist(clean_live_a),
        robust_title=_robust_norm_title(clean_live_t),
        duration=282,
    )
    assert score_live > 0, "Live request should match live recording in DB"


def test_pipeline_score_candidate_distinguishes_different_remixes():
    from bot.services.ingestion.pipeline import _score_candidate, _robust_norm_title
    from shared.matching import clean_track_metadata, normalize_artist, normalize_title
    from shared.models import Track

    # DB track is Loadstar Remix
    loadstar_track = Track(
        id=511,
        title="Sleepless (Loadstar Remix)",
        artist="Excision",
        normalized_artist="excision",
        duration=254,
    )
    # DB track is Xilent Remix
    xilent_track = Track(
        id=4180,
        title="Sleepless(Xilent Remix)",
        artist="Excision & Savvy",
        normalized_artist="excision",
        duration=308,
    )

    clean_t, clean_a = clean_track_metadata("Sleepless - Xilent Remix", "Excision, Savvy, Xilent")
    norm_t = normalize_title(clean_t)
    norm_a = normalize_artist(clean_a)
    rob_t = _robust_norm_title(clean_t)

    # 1. Target is Xilent Remix -> Must NOT match Loadstar Remix (even if duration is missing/None)
    score_wrong_remix_nodur = _score_candidate(
        loadstar_track,
        clean_title=clean_t,
        clean_artist=clean_a,
        norm_title=norm_t,
        norm_artist=norm_a,
        robust_title=rob_t,
        duration=None,
    )
    assert score_wrong_remix_nodur == -1, "Different remix must be rejected even when duration is None"

    # 2. Target is Xilent Remix with duration -> Must NOT match Loadstar Remix
    score_wrong_remix = _score_candidate(
        loadstar_track,
        clean_title=clean_t,
        clean_artist=clean_a,
        norm_title=norm_t,
        norm_artist=norm_a,
        robust_title=rob_t,
        duration=308,
    )
    assert score_wrong_remix == -1, "Different remix must be rejected"

    # 3. Target is Xilent Remix -> MUST match Xilent Remix in DB
    score_correct_remix = _score_candidate(
        xilent_track,
        clean_title=clean_t,
        clean_artist=clean_a,
        norm_title=norm_t,
        norm_artist=norm_a,
        robust_title=rob_t,
        duration=308,
    )
    assert score_correct_remix >= 80, "Matching remix must be accepted with high score"


def test_are_version_details_compatible():
    from shared.matching import are_version_details_compatible, extract_version_detail

    assert are_version_details_compatible("Sleepless (Loadstar Remix)", "Sleepless - Xilent Remix") is False
    assert are_version_details_compatible("Sleepless(Xilent Remix)", "Sleepless - Xilent Remix") is True
    assert are_version_details_compatible("Sleepless - Xilent Remix", "Sleepless (Xilent Remix)") is True
    assert are_version_details_compatible("Clint Eastwood - Ed Case/Sweetie Irie Re-Fix", "Clint Eastwood (Ed Case/Sweetie Irie Refix)") is True
    assert are_version_details_compatible("Clint Eastwood (Ed Case Remix)", "Clint Eastwood (Phi Life Cypher Version)") is False
    assert are_version_details_compatible("Midnight City - Radio Edit", "Midnight City (Radio Edit)") is True
    assert are_version_details_compatible("Midnight City (Radio Edit)", "Midnight City (Club Mix)") is False
    assert are_version_details_compatible("Around the World", "Around the World") is True



@pytest.mark.asyncio
async def test_upload_playlist_cover_to_telegram_channel():
    """Test that playlist cover is downloaded and sent to user's Telegram channel."""
    from unittest.mock import MagicMock, AsyncMock, patch
    from bot.services.ingestion.pipeline import IngestionPipeline
    from aiogram.types import PhotoSize, Message

    mock_bot = MagicMock()
    mock_photo = MagicMock(spec=PhotoSize)
    mock_photo.file_id = "test_cover_file_id_12345"
    mock_msg = MagicMock(spec=Message)
    mock_msg.photo = [mock_photo]
    mock_bot.send_photo = AsyncMock(return_value=mock_msg)

    pipeline = IngestionPipeline(mock_bot)

    mock_channel = MagicMock()
    mock_channel.channel_id = -1001234567890
    mock_channel.is_active = True

    mock_session = MagicMock()
    mock_session.scalar = AsyncMock(return_value=mock_channel)

    # Mock image download
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "image/jpeg"}
    mock_resp.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0" + b"A" * 100)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_http = MagicMock()
    mock_http.get.return_value = mock_cm

    mock_http_cm = AsyncMock()
    mock_http_cm.__aenter__.return_value = mock_http
    mock_http_cm.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_http_cm):
        result = await pipeline._upload_playlist_cover(
            session=mock_session,
            playlist_id=42,
            user_id=123,
            playlist_name="My Cool Playlist",
            cover_url="https://i1.sndcdn.com/artworks-123-t500x500.jpg",
        )

        assert result == "/api/images/test_cover_file_id_12345"
        mock_bot.send_photo.assert_called_once()
        call_kwargs = mock_bot.send_photo.call_args[1]
        assert call_kwargs["chat_id"] == -1001234567890
        assert "My Cool Playlist" in call_kwargs["caption"]
        assert "#playlist_42" in call_kwargs["caption"]


@pytest.mark.asyncio
async def test_upload_playlist_cover_fallback_to_pm():
    """Test that playlist cover falls back to user PM if channel send fails."""
    from unittest.mock import MagicMock, AsyncMock, patch
    from bot.services.ingestion.pipeline import IngestionPipeline
    from aiogram.types import PhotoSize, Message

    mock_bot = MagicMock()
    mock_photo = MagicMock(spec=PhotoSize)
    mock_photo.file_id = "fallback_pm_file_id"
    mock_msg = MagicMock(spec=Message)
    mock_msg.photo = [mock_photo]

    # First call fails (channel), second call succeeds (PM)
    mock_bot.send_photo = AsyncMock(side_effect=[Exception("Channel forbidden"), mock_msg])

    pipeline = IngestionPipeline(mock_bot)

    mock_channel = MagicMock()
    mock_channel.channel_id = -1001234567890
    mock_channel.is_active = True

    mock_session = MagicMock()
    mock_session.scalar = AsyncMock(return_value=mock_channel)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "image/jpeg"}
    mock_resp.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0" + b"A" * 100)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None

    mock_http = MagicMock()
    mock_http.get.return_value = mock_cm

    mock_http_cm = AsyncMock()
    mock_http_cm.__aenter__.return_value = mock_http
    mock_http_cm.__aexit__.return_value = None

    with patch("aiohttp.ClientSession", return_value=mock_http_cm):
        result = await pipeline._upload_playlist_cover(
            session=mock_session,
            playlist_id=99,
            user_id=777,
            playlist_name="Fallback Playlist",
            cover_url="https://i.scdn.co/image/spotify123",
        )

        assert result == "/api/images/fallback_pm_file_id"
        assert mock_bot.send_photo.call_count == 2
        # Second call must be to user PM
        second_call = mock_bot.send_photo.call_args_list[1][1]
        assert second_call["chat_id"] == 777


def test_calculate_preview_range():
    """Test smart 30s preview range calculation avoiding empty intros."""
    from bot.services.ingestion.base import calculate_preview_range

    # None or 0 duration
    assert calculate_preview_range(None) == (0, 30)
    assert calculate_preview_range(0) == (0, 30)

    # Exactly 30s or shorter
    assert calculate_preview_range(30) == (0, 30)
    assert calculate_preview_range(25) == (0, 30)

    # Short tracks (50s): skips 10s intro
    start, end = calculate_preview_range(50)
    assert end - start == 30
    assert start == 10
    assert end == 40

    # 100s track: starts at 20s
    start, end = calculate_preview_range(100)
    assert end - start == 30
    assert start == 20
    assert end == 50

    # Normal 200s (3m 20s) track: ~25% into track -> 50s..80s
    start, end = calculate_preview_range(200)
    assert end - start == 30
    assert start == 50
    assert end == 80

    # Long 300s (5 min) track: capped at 60s
    start, end = calculate_preview_range(300)
    assert end - start == 30
    assert start == 60
    assert end == 90


def test_quick_import_preview_only_schema_and_track_response():
    """Test that QuickImportRequest supports preview_only and TrackResponse supports is_chunk and source_url."""
    from api.routers.ingestion import QuickImportRequest
    from api.schemas.tracks import TrackResponse
    from datetime import datetime, timezone

    req = QuickImportRequest(
        url="https://soundcloud.com/artist/track",
        preview_only=True,
    )
    assert req.preview_only is True

    req_full = QuickImportRequest(
        url="https://soundcloud.com/artist/track",
    )
    assert req_full.preview_only is False

    now = datetime.now(timezone.utc)
    resp = TrackResponse(
        id=123,
        telegram_file_id="tg_chunk_file_id",
        title="Test Song",
        artist="Test Artist",
        duration=30,
        is_chunk=True,
        source_url="https://soundcloud.com/artist/track",
        added_at=now,
    )
    assert resp.is_chunk is True
    assert resp.source_url == "https://soundcloud.com/artist/track"
    assert resp.duration == 30


def test_score_candidate_chunk_filtering():
    """Test that candidate scoring rejects chunk tracks for library searches but allows for preview lookup."""
    from bot.services.ingestion.pipeline import _score_candidate
    from shared.models import Track

    chunk_track = Track(
        id=1,
        title="Awesome Song",
        artist="DJ Cool",
        duration=30,
        is_chunk=True,
        file_id="chunk_id",
        file_unique_id="chunk_uid",
    )

    # When searching for full track (allow_chunk=False): must be rejected
    score_for_full = _score_candidate(
        t=chunk_track,
        clean_title="Awesome Song",
        clean_artist="DJ Cool",
        norm_title="awesome song",
        norm_artist="dj cool",
        robust_title="awesome song",
        duration=210,
        allow_chunk=False,
    )
    assert score_for_full == -1

    # When searching for preview chunk (allow_chunk=True): should match
    score_for_preview = _score_candidate(
        t=chunk_track,
        clean_title="Awesome Song",
        clean_artist="DJ Cool",
        norm_title="awesome song",
        norm_artist="dj cool",
        robust_title="awesome song",
        duration=210,
        allow_chunk=True,
    )
    assert score_for_preview > 0














