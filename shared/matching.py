"""
TG Player - Text Normalization and Matching Utilities

Consolidated module for all text normalization and fuzzy matching.
Used by: enrichment, album assembly, search, deduplication.
"""
import html
import re
import unicodedata
from typing import Optional, List, Tuple, Set
from functools import lru_cache


# ============== Constants ==============

# Threshold for fuzzy matching (0.0 to 1.0)
ARTIST_MATCH_THRESHOLD = 0.75
TITLE_MATCH_THRESHOLD = 0.70
ALBUM_MATCH_THRESHOLD = 0.70

# Common genre mappings
GENRE_MAPPINGS = {
    "rock": "Rock",
    "pop": "Pop",
    "hip hop": "Hip-Hop",
    "hip-hop": "Hip-Hop",
    "rap": "Hip-Hop",
    "electronic": "Electronic",
    "edm": "Electronic",
    "house": "Electronic",
    "techno": "Electronic",
    "dubstep": "Electronic",
    "dnb": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "jazz": "Jazz",
    "blues": "Blues",
    "classical": "Classical",
    "metal": "Metal",
    "punk": "Punk",
    "r&b": "R&B",
    "rnb": "R&B",
    "soul": "Soul",
    "country": "Country",
    "folk": "Folk",
    "indie": "Indie",
    "alternative": "Alternative",
    "reggae": "Reggae",
    "latin": "Latin",
    "world": "World",
    "ambient": "Ambient",
    "soundtrack": "Soundtrack",
    "k-pop": "K-Pop",
    "kpop": "K-Pop",
    "j-pop": "J-Pop",
    "jpop": "J-Pop",
    "russian": "Russian",
    "russian rap": "Russian Hip-Hop",
    "trap": "Trap",
    "drill": "Drill",
    "cloud rap": "Cloud Rap",
    "witch house": "Witch House",
}


# ============== Basic Normalization ==============

def normalize_unicode(text: str) -> str:
    """
    Normalize unicode characters.
    Converts curly quotes, special chars, etc. to ASCII equivalents.
    """
    if not text:
        return ""
    
    # NFKD normalization
    text = unicodedata.normalize('NFKD', text)
    
    # Common character replacements
    replacements = {
        '€': 'e', '$': 's', '@': 'a',
        ''': "'", ''': "'", '`': "'",
        '"': '"', '"': '"',
        '–': '-', '—': '-',
        '…': '...',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    return text


def remove_parenthetical(text: str) -> str:
    """
    Remove content in parentheses and brackets.
    Handles nested brackets with multiple passes.
    
    Examples:
        "Track (feat. Artist)" -> "Track"
        "Album ((Deluxe Edition))" -> "Album"
    """
    if not text:
        return ""
    
    # Multiple passes for nested brackets
    for _ in range(3):
        new_text = re.sub(r'\s*[\(\[][^\(\)\[\]]*[\)\]]', '', text)
        if not new_text.strip():
            # If removing brackets empties the entire string (e.g. "[AMATORY]"), unwrap the brackets
            text = re.sub(r'^\s*[\(\[]\s*(.*?)\s*[\)\]]\s*$', r'\1', text)
            break
        text = new_text
    
    return text.strip()


def remove_featuring(text: str) -> str:
    """
    Remove featuring, prod, vs and everything after.
    
    Examples:
        "Track feat. Artist" -> "Track"
        "Song prod. Producer" -> "Song"
    """
    if not text:
        return ""
    
    patterns = [
        r'\s*(feat\.?|ft\.?|featuring)\s+.*$',
        r'\s*(prod\.?|produced\s+by)\s+.*$',
        r'\s*vs\.?\s+.*$',
    ]
    
    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    return text.strip()


def extract_featured_artists(title: str, main_artist: Optional[str] = None) -> List[str]:
    """
    Extract featured/remix/prod artists from track title.
    
    Examples:
        "X Rated (Space Laces Remix)" -> ["Space Laces"]
        "Track feat. Artist1 & Artist2" -> ["Artist1", "Artist2"]
        "Song (Prod. Producer)" -> ["Producer"]
        "Battle vs. Other" -> ["Other"]
        "Track (excision feat. black kray)" -> ["excision", "black kray"]
    
    Returns:
        List of additional artist names
    """
    if not title:
        return []
    
    artists = []
    
    # Words that look like artist names before "Mix/Remix" but are actually version descriptors
    # "Original Mix" = standard/original version (common in electronic music)
    # "Radio Mix/Edit" = shortened radio version
    # "Extended Mix" = longer club version
    # "Club Mix" = club-oriented version
    # "Dub Mix" = dub version (not artist "Dub")
    # "Instrumental Mix" = version without vocals
    # "Vocal Mix" = version with vocals
    # "Acoustic Mix" = acoustic version
    NON_ARTIST_MIX_PREFIXES = {
        'original', 'radio', 'extended', 'club', 'dub', 'instrumental', 
        'vocal', 'acoustic', 'main', 'album', 'single', 'short', 'long',
        'clean', 'dirty', 'explicit', 'censored', 'uncensored'
    }
    
    # Pattern for remix: "(Artist Remix)" or "(Artist's Remix)" or "[Artist Remix]"
    remix_patterns = [
        r'[\(\[]([^\)\]]+?)(?:\'s)?\s+(?:Remix|Rmx|Mix|Edit|Bootleg|Rework|Flip|VIP)[\)\]]',
        r'\(Remix\s+by\s+([^\)]+)\)',
        r'\[Remix\s+by\s+([^\]]+)\]',
    ]
    for pattern in remix_patterns:
        matches = re.findall(pattern, title, flags=re.IGNORECASE)
        for match in matches:
            artist = match.strip()
            # Skip version descriptors that aren't artist names
            if artist and len(artist) > 1 and artist.lower() not in NON_ARTIST_MIX_PREFIXES:
                artists.append(artist)
    
    # Pattern for feat.: "feat. Artist" or "ft. Artist" - outside parentheses
    feat_match = re.search(r'(?:feat\.?|ft\.?|featuring)\s+([^\(\)\[\]]+?)(?:\s*[\(\[]|$)', title, flags=re.IGNORECASE)
    if feat_match:
        feat_part = feat_match.group(1).strip()
        # Split by & , and
        feat_artists = re.split(r'\s*(?:&|,|\band\b)\s*', feat_part)
        for fa in feat_artists:
            fa = fa.strip()
            if fa and len(fa) > 1:
                artists.append(fa)
    
    # Pattern for feat. inside parentheses: "(Artist1 feat. Artist2)" or "(feat. Artist)"
    # This handles mashups like "Track (excision feat. black kray)"
    paren_feat_patterns = [
        # "(Artist1 feat. Artist2)" - extract both parts
        r'[\(\[]([^\)\]]+?)\s+(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]',
        # "(feat. Artist)" - only extract featured artist
        r'[\(\[](?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]',
    ]
    for pattern in paren_feat_patterns:
        matches = re.findall(pattern, title, flags=re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                # Pattern with two groups (Artist1 feat. Artist2)
                for part in match:
                    part = part.strip()
                    if part and len(part) > 1:
                        # Split by & , and
                        sub_artists = re.split(r'\s*(?:&|,|\band\b)\s*', part)
                        for sa in sub_artists:
                            sa = sa.strip()
                            if sa and len(sa) > 1:
                                artists.append(sa)
            else:
                # Single group
                part = match.strip()
                if part and len(part) > 1:
                    # Split by & , and
                    sub_artists = re.split(r'\s*(?:&|,|\band\b)\s*', part)
                    for sa in sub_artists:
                        sa = sa.strip()
                        if sa and len(sa) > 1:
                            artists.append(sa)
    
    # Pattern for prod: "prod. Producer" or "(Prod. by Producer)" or "Prod.Producer" (no space)
    prod_patterns = [
        # With space: "prod. Producer" or "produced by Producer"
        r'(?:prod\.?|produced\s+by)\s+([^\(\)\[\]]+?)(?:\s*[\(\[]|$)',
        # Inside parentheses with space: "(Prod. by Producer)" or "(prod Producer)"
        r'[\(\[](?:prod\.?|produced\s+by)\s+([^\)\]]+)[\)\]]',
        # Without space after dot: "Prod.Producer" - common in user-tagged files
        r'\bprod\.([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_\s&]+?)(?:\s*[\(\[\|\)]|$)',
        # Inside parentheses without space: "(Prod.Producer)"
        r'[\(\[]prod\.([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_\s&]+?)[\)\]]',
    ]
    for pattern in prod_patterns:
        matches = re.findall(pattern, title, flags=re.IGNORECASE)
        for match in matches:
            artist = match.strip()
            if artist and len(artist) > 1:
                artists.append(artist)
    
    # Pattern for vs: "vs. Artist" or "vs Artist"
    vs_match = re.search(r'\bvs\.?\s+([^\(\)\[\]]+?)(?:\s*[\(\[]|$)', title, flags=re.IGNORECASE)
    if vs_match:
        artist = vs_match.group(1).strip()
        if artist and len(artist) > 1:
            artists.append(artist)
    
    # Clean up: remove duplicates and main artist
    main_artist_lower = main_artist.lower() if main_artist else None
    unique = []
    seen = set()
    for a in artists:
        a_lower = a.lower()
        if a_lower not in seen and (not main_artist_lower or a_lower != main_artist_lower):
            seen.add(a_lower)
            unique.append(a)
    
    return unique


# ============== Artist Normalization ==============

@lru_cache(maxsize=1000)
def normalize_artist(artist: str) -> str:
    """
    Normalize artist name for matching and grouping.
    
    - Takes first artist from collaborations
    - Removes feat./prod. suffixes
    - Removes "и др." (Russian for "and others")
    - Normalizes case and special chars
    
    Examples:
        "A$AP Rocky" -> "asap rocky"
        "Drake, Future" -> "drake"
        "Artist feat. Other" -> "artist"
        "BLADEE и др." -> "bladee"
    """
    if not artist:
        return ""
    
    artist = normalize_unicode(artist).lower()
    artist = remove_parenthetical(artist)
    artist = remove_featuring(artist)
    
    # Remove Russian "и др." / "и другие" (and others)
    artist = re.sub(r'\s+и\s+др\.?$', '', artist, flags=re.IGNORECASE)
    artist = re.sub(r'\s+и\s+други[ех]?$', '', artist, flags=re.IGNORECASE)
    
    # Take first artist from collaborations
    # Separators: comma, ampersand, plus, x, and, with
    artist = re.split(r'\s*[,&+]\s*|\s+(?:x|and|with)\s+', artist, flags=re.IGNORECASE)[0]
    
    # Stylistic letter replacements (KoЯn -> Korn, VØJ -> VOJ, A$AP -> ASAP)
    artist = artist.replace('Я', 'r').replace('я', 'r')
    artist = artist.replace('Ø', 'o').replace('ø', 'o')
    artist = artist.replace('$', 's')
    
    # Remove remaining special characters, keeping raw symbols if stripping empties it (e.g. '#####')
    clean = re.sub(r'[^\w\s]', '', artist)
    if clean.strip():
        artist = clean
    
    # Normalize whitespace
    artist = ' '.join(artist.split())
    
    return artist.strip()


def normalize_artist_display(artist: str) -> str:
    """
    Normalize artist for display (keeps case, first artist only).
    
    Examples:
        "Drake, Future" -> "Drake"
        "Artist feat. Other" -> "Artist"
    """
    if not artist:
        return ""
    
    artist = remove_parenthetical(artist)
    artist = remove_featuring(artist)
    artist = re.split(r'\s*[,&+]\s*|\s+(?:x|and|with)\s+', artist, flags=re.IGNORECASE)[0]
    
    return artist.strip()


# ============== Title Normalization ==============

@lru_cache(maxsize=1000)
def normalize_title(title: str) -> str:
    """
    Normalize track title for matching.
    
    - Removes parenthetical content (feat., remix, etc.)
    - Normalizes unicode and case
    - Removes special characters
    
    Examples:
        "Track (Remix)" -> "track"
        "Song ft. Artist" -> "song"
    """
    if not title:
        return ""
    
    title = normalize_unicode(title).lower()
    title = remove_parenthetical(title)
    title = remove_featuring(title)
    
    # Remove apostrophes completely for matching
    title = title.replace("'", "")
    
    # Remove special characters, keep letters/numbers/spaces
    title = re.sub(r'[^\w\s]', '', title)
    
    # Normalize whitespace
    title = ' '.join(title.split())
    
    return title.strip()


# ============== Album Normalization ==============

# Important suffixes that make albums distinct
ALBUM_IMPORTANT_SUFFIXES = [
    r'\(deluxe[^)]*\)',
    r'\(remaster(?:ed)?[^)]*\)',
    r'\(expanded[^)]*\)',
    r'\(anniversary[^)]*\)',
    r'\(bonus[^)]*\)',
    r'\[deluxe[^\]]*\]',
    r'\[remaster(?:ed)?[^\]]*\]',
]


@lru_cache(maxsize=500)
def normalize_album(album: str) -> str:
    """
    Normalize album name for matching/grouping.
    Preserves important suffixes like (Deluxe), (Remastered).
    
    Examples:
        "D&G" -> "dg"
        "Album (Deluxe)" -> "album deluxe"
        " ICEDANCER " -> "icedancer"
    """
    if not album:
        return ""
    
    album = normalize_unicode(album).lower().strip()
    
    # Extract important suffixes BEFORE cleaning
    suffixes = []
    for pattern in ALBUM_IMPORTANT_SUFFIXES:
        match = re.search(pattern, album, re.IGNORECASE)
        if match:
            suffix = re.sub(r'[\(\)\[\]]', '', match.group()).strip()
            suffix = suffix.split()[0]  # "deluxe edition" -> "deluxe"
            suffixes.append(suffix)
    
    # Remove ALL parenthetical content
    album = remove_parenthetical(album)
    
    # Replace & with nothing (D&G = DG)
    album = album.replace('&', '')
    album = album.replace('$', 's')
    
    # Remove special characters
    album = re.sub(r'[^\w\s]', '', album)
    
    # Normalize whitespace
    album = ' '.join(album.split())
    
    # Add back important suffixes
    if suffixes:
        album = album + ' ' + ' '.join(sorted(set(suffixes)))
    
    return album.strip()


def is_bogus_album_name(album: Optional[str]) -> bool:
    """
    Check if an album string is a pseudo-album representing user likes or playlist.
    Examples: 'meth (Likes)', 'Liked Songs', 'SoundCloud Likes', 'Spotify Likes'.
    """
    if not album:
        return False
    norm = album.strip().lower()
    if norm in (
        "likes",
        "liked songs",
        "favorites",
        "soundcloud likes",
        "spotify likes",
        "youtube likes",
        "любимые треки",
        "понравившиеся",
        "избранное",
    ):
        return True
    if norm.endswith("(likes)") or norm.endswith("[likes]") or norm.endswith("(liked)") or norm.endswith("[liked]"):
        return True
    if norm.startswith("likes (") or norm.startswith("liked ("):
        return True
    if "(likes)" in norm or "[likes]" in norm or "(liked)" in norm:
        return True
    return False


def sanitize_album_name(album: Optional[str]) -> Optional[str]:
    """Sanitize album name. Returns None if bogus/pseudo-album."""
    if not album:
        return None
    cleaned = album.strip()
    if is_bogus_album_name(cleaned):
        return None
    return cleaned


# ============== Search Query Cleaning ==============

def clean_for_search(text: str) -> str:
    """
    Clean text for external API search queries.
    More aggressive than normalization - removes noise for better API results.
    
    Examples:
        "Track (feat. Artist) [Remaster]" -> "Track"
        "guardianAngels((NO2))" -> "guardian Angels"
    """
    if not text:
        return ""
    
    text = normalize_unicode(text)
    
    # Add spaces before capitals in camelCase
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    
    # Remove parenthetical content
    text = remove_parenthetical(text)
    
    # Remove prod./feat.
    text = remove_featuring(text)
    
    # Remove trailing special characters
    text = re.sub(r'[\s\-_\.,;:]+$', '', text)
    
    return text.strip()


# ============== Fuzzy Matching ==============

def jaccard_similarity(set1: set, set2: set) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0
    
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    
    return intersection / union if union > 0 else 0.0


VERSION_KEYWORDS = [
    'remix', 'rmx', 'vip', 'edit', 'bootleg', 'rework', 'flip',
    'acoustic', 'instrumental', 'orchestral', 'live', 'slowed',
    'reverb', 'sped up', 'speed up', 'dub', 'club', 'radio',
    'extended', 'version', 'ver', 'cover', 'mix', 'mashup'
]


def extract_version_info(title: str) -> Optional[str]:
    """
    Extract version or remix descriptor from track title.
    
    Examples:
        "Demonic Curse (CHIBS Remix)" -> "chibs remix"
        "Promises (Skrillex & Nero Remix)" -> "skrillex nero remix"
        "Track - VIP" -> "vip"
        "Song (feat. Artist)" -> None
        "Track" -> None
    """
    if not title:
        return None
    
    # 1. Check parenthetical / bracketed content
    paren_matches = re.findall(r'[\(\[]([^\(\)\[\]]+)[\)\]]', title)
    for m in paren_matches:
        m_lower = m.lower().strip()
        # Skip pure featuring / prod
        if re.match(r'^(?:feat\.?|ft\.?|featuring|prod\.?|produced\s+by)\b', m_lower):
            continue
        if any(re.search(rf'\b{kw}\b', m_lower) for kw in VERSION_KEYWORDS):
            v = re.sub(r'[^\w\s]', '', m_lower).strip()
            return ' '.join(v.split())
            
    # 2. Check after hyphen: ' - VIP' or ' - Chibs Remix'
    hyphen_match = re.search(r'\s+-\s+([^-(]+)$', title)
    if hyphen_match:
        cand = hyphen_match.group(1).lower().strip()
        if any(re.search(rf'\b{kw}\b', cand) for kw in VERSION_KEYWORDS):
            v = re.sub(r'[^\w\s]', '', cand).strip()
            return ' '.join(v.split())
            
    return None


def versions_conflict(v1: Optional[str], v2: Optional[str]) -> bool:
    """
    Check if two version/remix descriptors conflict (i.e. refer to different versions).
    Returns True only if BOTH are present and distinctly incompatible.
    """
    if not v1 or not v2:
        return False
    if v1 == v2:
        return False
    
    # Normalize synonyms (rmx -> remix)
    norm_v1 = re.sub(r'\brmx\b', 'remix', v1)
    norm_v2 = re.sub(r'\brmx\b', 'remix', v2)
    if norm_v1 == norm_v2:
        return False

    # Check if both belong to the same non-remix performance category (e.g. live, acoustic, instrumental)
    for cat in ('live', 'acoustic', 'instrumental', 'slowed', 'reverb', 'sped up'):
        if cat in norm_v1 and cat in norm_v2:
            return False

    generic_keywords = {'remix', 'mix', 'edit', 'version', 'ver', 'live', 'acoustic', 'instrumental', 'vip', 'cover', 'flip'}
    words1 = set(norm_v1.split())
    words2 = set(norm_v2.split())

    # Category conflict check (e.g., 'live' vs 'remix', 'acoustic' vs 'vip')
    major_categories = {'live', 'acoustic', 'instrumental', 'remix', 'cover', 'vip', 'slowed'}
    cat1 = words1 & major_categories
    cat2 = words2 & major_categories
    if cat1 and cat2 and not (cat1 & cat2):
        return True

    # If one is just a generic modifier (e.g. 'remix') and the other is a specific one (e.g. 'skrillex remix')
    if (words1 <= generic_keywords and words2 & generic_keywords) or (words2 <= generic_keywords and words1 & generic_keywords):
        return False
    
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, norm_v1, norm_v2).ratio()
    if ratio >= 0.82:
        return False
    
    return True


def fuzzy_match_title(title1: str, title2: str) -> float:
    """
    Calculate similarity between two titles (0.0 to 1.0).
    Uses normalized comparison, sequence distance and word-based matching.
    
    Returns:
        Similarity score (0.0 to 1.0)
    """
    from difflib import SequenceMatcher
    
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)
    
    if not norm1 or not norm2:
        return 0.0
    
    v1 = extract_version_info(title1)
    v2 = extract_version_info(title2)
    
    # If both titles specify conflicting versions/remixes,
    # they are distinct audio works and must not match!
    if v1 and v2 and versions_conflict(v1, v2):
        return 0.25
    
    # Exact match after normalization
    if norm1 == norm2:
        # If one has a remix/version and the other is a base track without version,
        # allow strong match for search purposes, but slightly lower than exact match (0.95)
        if (v1 is None) != (v2 is None):
            return 0.95
        return 1.0
    
    # Compare without spaces
    compact1 = norm1.replace(" ", "")
    compact2 = norm2.replace(" ", "")
    
    if compact1 == compact2:
        if (v1 is None) != (v2 is None):
            return 0.95
        return 1.0
    
    # Word sets
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    if words1 == words2:
        if (v1 is None) != (v2 is None):
            return 0.95
        return 1.0
    
    # Character-level SequenceMatcher (handles typos, slight spelling differences)
    seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()
    compact_seq = SequenceMatcher(None, compact1, compact2).ratio()
    best_seq = max(seq_ratio, compact_seq)
    
    # Word-based Jaccard similarity
    jaccard = jaccard_similarity(words1, words2)
    
    # Word count ratio (penalizes when one title has extra words like 'Empty Promises' vs 'Promises')
    word_count_ratio = min(len(words1), len(words2)) / max(len(words1), len(words2))
    
    # Character length ratio
    len_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2))
    
    # Weighted composite score
    score = (best_seq * 0.4) + (jaccard * 0.4) + (word_count_ratio * 0.2)
    
    # Only boost substring if length ratio is very close (e.g. >= 0.88, minor spelling/prefix difference)
    if (norm1 in norm2 or norm2 in norm1) and len_ratio >= 0.88:
        score = max(score, 0.80 + (0.15 * len_ratio))
    
    if (v1 is None) != (v2 is None):
        score = min(score, 0.90)
        
    return min(1.0, score)



def fuzzy_match_artist(artist1: str, artist2: str) -> float:
    """
    Calculate similarity between two artist names (0.0 to 1.0).
    Strict matching: does NOT match unrelated artists that happen to share a substring
    (e.g., 'Nero' vs 'Nero Isekai', 'Air' vs 'Airbourne').
    """
    from difflib import SequenceMatcher
    
    norm1 = normalize_artist(artist1)
    norm2 = normalize_artist(artist2)
    
    if not norm1 or not norm2:
        return 0.0

    # Multi-artist collaboration check (e.g. "Messinian & Adroa" vs "Adroa and Messinian",
    # or "Пьеха Стас, Григорий Лепс" vs "Григорий Лепс")
    raw1_clean = remove_featuring(artist1)
    raw2_clean = remove_featuring(artist2)
    sub1 = {re.sub(r'[^\w\s]', '', s.lower()).strip() for s in re.split(r'\s*[,&+×/]\s*|\s+(?:x|and|with)\s+', raw1_clean, flags=re.IGNORECASE) if s.strip()}
    sub2 = {re.sub(r'[^\w\s]', '', s.lower()).strip() for s in re.split(r'\s*[,&+×/]\s*|\s+(?:x|and|with)\s+', raw2_clean, flags=re.IGNORECASE) if s.strip()}
    if sub1 and sub2:
        if sub1 == sub2:
            return 1.0
        # If one is a complete subset of multi-artists (e.g. collab track vs main artist)
        if (len(sub1) > 1 or len(sub2) > 1) and (sub1 <= sub2 or sub2 <= sub1):
            return 0.85
    
    # Exact match
    if norm1 == norm2:
        return 1.0
    
    # Without spaces
    compact1 = norm1.replace(" ", "")
    compact2 = norm2.replace(" ", "")
    
    if compact1 == compact2:
        return 1.0
    
    # Word sets
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    if words1 == words2:
        return 1.0
    
    # Character-level SequenceMatcher
    seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()
    compact_seq = SequenceMatcher(None, compact1, compact2).ratio()
    best_seq = max(seq_ratio, compact_seq)
    
    # Token Jaccard
    jaccard = jaccard_similarity(words1, words2)
    
    len_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2))
    
    # Only boost substring if length ratio is very high (>= 0.85) e.g. "The Artist" vs "Artist"
    if (norm1 in norm2 or norm2 in norm1) and len_ratio >= 0.85:
        return max(best_seq, 0.75 + (0.15 * len_ratio))
    
    # Blend sequence similarity and token Jaccard
    return (best_seq * 0.7) + (jaccard * 0.3)


def fuzzy_match_album(album1: str, album2: str) -> float:
    """
    Calculate similarity between two album names (0.0 to 1.0).
    Uses normalize_album for album-specific logic (Deluxe/Remastered preserved).
    Combines word-based Jaccard with SequenceMatcher for robustness.
    """
    from difflib import SequenceMatcher
    
    norm1 = normalize_album(album1)
    norm2 = normalize_album(album2)
    
    if not norm1 or not norm2:
        return 0.0
    
    # Exact match
    if norm1 == norm2:
        return 1.0
    
    # Without spaces
    compact1 = norm1.replace(" ", "")
    compact2 = norm2.replace(" ", "")
    
    if compact1 == compact2:
        return 1.0
    
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    if words1 == words2:
        return 1.0
    
    seq_ratio = SequenceMatcher(None, norm1, norm2).ratio()
    compact_seq = SequenceMatcher(None, compact1, compact2).ratio()
    best_seq = max(seq_ratio, compact_seq)
    
    jaccard = jaccard_similarity(words1, words2)
    
    len_ratio = min(len(compact1), len(compact2)) / max(len(compact1), len(compact2))
    
    # One contains the other with high length ratio (e.g. "Album" vs "Album Deluxe")
    if (compact1 in compact2 or compact2 in compact1) and len_ratio >= 0.65:
        return max(best_seq, 0.70 + (0.20 * len_ratio))
    
    return (best_seq * 0.6) + (jaccard * 0.4)


def albums_match(album1: str, album2: str, threshold: float = ALBUM_MATCH_THRESHOLD) -> bool:
    """Check if two album names match above threshold."""
    return fuzzy_match_album(album1, album2) >= threshold


def artists_match(artist1: str, artist2: str, threshold: float = ARTIST_MATCH_THRESHOLD) -> bool:
    """Check if two artist names match above threshold."""
    return fuzzy_match_artist(artist1, artist2) >= threshold


def titles_match(title1: str, title2: str, threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    """Check if two titles match above threshold."""
    return fuzzy_match_title(title1, title2) >= threshold


# ============== Genre Normalization ==============

def normalize_genre(genre: str) -> Optional[str]:
    """
    Normalize genre to standard form.
    Returns None if genre is not recognized.
    
    Examples:
        "hip hop" -> "Hip-Hop"
        "rnb" -> "R&B"
    """
    if not genre:
        return None
    
    genre_lower = genre.lower().strip()
    
    # Direct mapping
    if genre_lower in GENRE_MAPPINGS:
        return GENRE_MAPPINGS[genre_lower]
    
    # Check if contains any known genre
    for key, value in GENRE_MAPPINGS.items():
        if key in genre_lower:
            return value
    
    # Return original with title case if not mapped
    return genre.strip().title()


# ============== Input Sanitization ==============

def sanitize_input(value: str, max_length: int = 200) -> str:
    """
    Sanitize user input to prevent SQL injection.
    Use for search queries, filters, and user-provided strings.
    """
    if not value:
        return ""
    
    # Escape SQL wildcards
    value = value.replace('%', r'\%').replace('_', r'\_')
    
    return value[:max_length].strip()


def sanitize_search_query(query: str, max_length: int = 100) -> str:
    """
    Sanitize search input - more aggressive.
    Removes SQL special characters.
    """
    if not query:
        return ""
    
    # Remove SQL special characters
    query = re.sub(r'[;\'"\\%_]', '', query)
    
    return query[:max_length].strip()


# ============== Hashtag Generation ==============

def generate_hashtags(
    artist: Optional[str] = None,
    title: Optional[str] = None,
    album: Optional[str] = None,
    genre: Optional[str] = None,
    extra_tags: Optional[List[str]] = None
) -> List[str]:
    """
    Generate hashtags for a track.
    Used for user channel messages.
    
    Returns:
        List of hashtags without # prefix
    """
    tags = []
    
    def artist_to_tag(name: str) -> Optional[str]:
        """Convert single artist name to hashtag."""
        name = name.strip()
        if not name:
            return None
        # Replace ! with I (common stylization like Deadmau5 -> Deadmau5)
        tag = name.replace('!', 'I')
        # Remove spaces and special chars, keep only word characters
        tag = re.sub(r'[^\w]', '', tag)
        return tag if tag and len(tag) > 1 else None
    
    if artist:
        # Split multiple artists and create separate hashtags for each
        from shared.utils import split_artists
        artists = split_artists(artist)
        for single_artist in artists:
            tag = artist_to_tag(single_artist)
            if tag:
                tags.append(tag)
    
    # Extract featured artists from title (remixers, feat., prod., vs.)
    if title:
        featured = extract_featured_artists(title, main_artist=artist)
        for fa in featured:
            tag = artist_to_tag(fa)
            if tag:
                tags.append(tag)
    
    if album:
        # Use full album name without spaces/special chars
        normalized = normalize_album(album)
        if normalized:
            tag = re.sub(r'[^\w]', '', normalized)
            # Skip generic/short tags
            if tag and len(tag) > 3 and tag.lower() not in ('single', 'album', 'the', 'vol'):
                tags.append(tag)
    
    if genre:
        normalized = normalize_genre(genre)
        if normalized:
            tag = re.sub(r'[^\w]', '', normalized.lower())
            if tag and len(tag) > 2:
                tags.append(tag)
    
    if extra_tags:
        for t in extra_tags:
            tag = re.sub(r'[^\w]', '', t.lower())
            if tag and len(tag) > 1:
                tags.append(tag)
    
    # Remove duplicates, preserve order
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    
    return unique_tags


def format_hashtags(tags: List[str]) -> str:
    """Format list of tags as hashtag string."""
    return ' '.join(f'#{html.escape(tag)}' for tag in tags)


def extract_artists_from_filename(filename: str) -> List[str]:
    """
    Extract artist names from filename.
    Common patterns:
        "Artist - Title.mp3" -> ["Artist"]
        "Artist - Title (feat. Other).mp3" -> ["Artist", "Other"]
        "Title (Artist Remix).mp3" -> ["Artist"]
        "ganjamonsta - Mashup (excision feat. black kray).mp3" -> ["excision", "black kray"]
    
    Returns:
        List of extracted artist names
    """
    if not filename:
        return []
    
    artists = []
    
    # Remove file extension
    name = re.sub(r'\.(mp3|flac|wav|ogg|m4a|aac|opus)$', '', filename, flags=re.IGNORECASE)
    
    # Pattern 1: "Artist - Title" format
    if ' - ' in name:
        parts = name.split(' - ', 1)
        artist_part = parts[0].strip()
        title_part = parts[1].strip() if len(parts) > 1 else ''
        
        # Split artist part by common separators
        artist_parts = re.split(r'\s*(?:,|&|\+|\bx\b|\band\b)\s*', artist_part, flags=re.IGNORECASE)
        for ap in artist_parts:
            ap = ap.strip()
            if ap and len(ap) > 1:
                artists.append(ap)
        
        # Also extract featured artists from title part
        extracted = extract_featured_artists(title_part)
        artists.extend(extracted)
    else:
        # No "Artist - Title" format, try to extract from parentheses
        extracted = extract_featured_artists(name)
        artists.extend(extracted)
    
    # Clean up: remove duplicates
    unique = []
    seen = set()
    for a in artists:
        a_lower = a.lower()
        if a_lower not in seen:
            seen.add(a_lower)
            unique.append(a)
    
    return unique


def get_all_track_artists(
    artist: Optional[str] = None, 
    title: Optional[str] = None, 
    file_name: Optional[str] = None
) -> List[str]:
    """
    Get all artists from a track by checking all available fields:
    - artist field
    - title field (featuring, remix, prod)
    - file_name (Artist - Title pattern)
    
    Returns:
        List of all unique artist names found in the track
    """
    artists = []
    
    # 1. From artist field
    if artist:
        # Split by common separators
        parts = re.split(r'\s*(?:,|&|\+|\bx\b|\band\b|\bwith\b|feat\.?|ft\.?|featuring|vs\.?)\s*', artist, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if part and len(part) > 1:
                artists.append(part)
    
    # 2. From title field
    if title:
        extracted = extract_featured_artists(title)
        artists.extend(extracted)
    
    # 3. From file_name (for tracks without metadata)
    if file_name and not artist:  # Only use filename if no artist metadata
        extracted = extract_artists_from_filename(file_name)
        artists.extend(extracted)
    
    # Clean up: remove duplicates
    unique = []
    seen = set()
    for a in artists:
        a_lower = a.lower()
        if a_lower not in seen:
            seen.add(a_lower)
            unique.append(a)
    
    return unique


# Patterns to detect track version classifications (live, acoustic, remix, instrumental, etc.)
VERSION_MARKER_PATTERNS = {
    "live": re.compile(
        r'\b(live|concert|tour|unplugged|bullet in a bible|live at|live from|live in|bbc session|radio 1 session|tiny desk|audiotree)\b',
        re.IGNORECASE,
    ),
    "acoustic": re.compile(
        r'\b(acoustic|stripped|unplugged)\b',
        re.IGNORECASE,
    ),
    "instrumental": re.compile(
        r'\b(instrumental|karaoke|backing track|minus)\b',
        re.IGNORECASE,
    ),
    "remix": re.compile(
        r'\b(remix|refix|re-fix|dub|vip|bootleg|rework|flip)\b|\b(?<!original\s)(?<!album\s)mix\b',
        re.IGNORECASE,
    ),
    "cover": re.compile(
        r'\b(cover|tribute)\b',
        re.IGNORECASE,
    ),
    "orchestral": re.compile(
        r'\b(orchestral|symphonic|orchestra)\b',
        re.IGNORECASE,
    ),
    "speed": re.compile(
        r'\b(sped up|speed up|slowed|nightcore|daycore|chopped and screwed)\b',
        re.IGNORECASE,
    ),
}


def extract_version_markers(text: Optional[str]) -> Set[str]:
    """Extract version categories (live, acoustic, instrumental, remix, etc.) from track title or tags.
    
    Returns a set of normalized marker strings, or an empty set if the track is a studio original.
    Note: 'Original Mix' and 'Album Version' explicitly denote original studio recordings.
    """
    if not text:
        return set()
    clean_text = re.sub(r'\b(original|album)\s+(mix|version)\b', '', text, flags=re.IGNORECASE)
    markers = set()
    for marker_name, pattern in VERSION_MARKER_PATTERNS.items():
        if pattern.search(clean_text):
            markers.add(marker_name)
    return markers


# Generic words inside version descriptors that do not uniquely identify the remixer/version
GENERIC_VERSION_WORDS = {
    'remix', 'refix', 'mix', 'edit', 'version', 'dub', 'bootleg', 'rework',
    'flip', 'vip', 'remaster', 'deluxe', 'live', 'acoustic', 'instrumental',
    'cover', 'orchestral', 'sped', 'speed', 'up', 'slowed', 'nightcore',
    'daycore', 'session', 'official', 'audio', 'video'
}

VERSION_KEYWORDS_PATTERN = re.compile(
    r'\b(remix|refix|re-fix|mix|edit|version|dub|bootleg|rework|flip|vip|remaster|deluxe|live|acoustic|instrumental|cover|orchestral|sped\s+up|speed\s+up|slowed|nightcore|daycore)\b',
    re.IGNORECASE
)


def extract_version_detail(title: Optional[str]) -> str:
    """
    Extract specific normalized version or remix substring from a track title.

    Examples:
        "Sleepless (Loadstar Remix)" -> "loadstar remix"
        "Sleepless - Xilent Remix" -> "xilent remix"
        "Clint Eastwood (Ed Case/Sweetie Irie Refix)" -> "ed case sweetie irie refix"
        "Around the World" -> ""
    """
    if not title:
        return ""
    # 1. Check parenthetical / bracketed content
    for m in re.finditer(r'[\(\[]([^\(\)\[\]]+)[\)\]]', title):
        content = m.group(1).strip()
        if VERSION_KEYWORDS_PATTERN.search(content):
            c = re.sub(r're-fix', 'refix', content, flags=re.IGNORECASE)
            c = re.sub(r'[^\w\s]', ' ', c).lower()
            return ' '.join(c.split())
    # 2. Check trailing dash/hyphen
    m = re.search(r'\s+[-–—]\s*(.+)$', title)
    if m:
        content = m.group(1).strip()
        if VERSION_KEYWORDS_PATTERN.search(content):
            c = re.sub(r're-fix', 'refix', content, flags=re.IGNORECASE)
            c = re.sub(r'[^\w\s]', ' ', c).lower()
            return ' '.join(c.split())
    return ""


def get_version_qualifier(detail: str) -> str:
    """Extract qualifier words (e.g. remixer name, venue) excluding generic words."""
    words = [w for w in detail.split() if w not in GENERIC_VERSION_WORDS]
    return ' '.join(words)


def are_version_details_compatible(title1: Optional[str], title2: Optional[str]) -> bool:
    """
    Check if version specifics (remix name, live venue, etc.) between two titles are compatible.

    Returns True if:
    - Neither track has a version (both are studio tracks).
    - One is denoted as 'original mix' / 'album version' and the other is a studio track.
    - Both have matching version details or qualifiers (e.g. 'Xilent Remix' vs 'Xilent Remix').
    - One version is a generic descriptor (e.g. 'Live') and the other is specific ('Live at Wembley').

    Returns False if:
    - One track is a remix/live/edit and the other is not.
    - Both have distinct specific qualifiers (e.g. 'Loadstar Remix' vs 'Xilent Remix').
    """
    v1 = extract_version_detail(title1)
    v2 = extract_version_detail(title2)
    if not v1 and not v2:
        return True

    orig_phrases = {'original mix', 'original version', 'album version', 'album mix'}
    if (v1 in orig_phrases and not v2) or (v2 in orig_phrases and not v1):
        return True

    if bool(v1) != bool(v2):
        return False

    if v1 == v2:
        return True

    q1 = get_version_qualifier(v1)
    q2 = get_version_qualifier(v2)
    if not q1 or not q2:
        return True

    if q1 == q2 or q1 in q2 or q2 in q1:
        return True

    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, q1, q2).ratio()
    return ratio >= 0.75


# Patterns to strip out promo channels, websites, bitrates, and rip tags
METADATA_JUNK_PATTERNS = [
    # Telegram channels and usernames: @something, t.me/something
    r'(?:https?://)?(?:www\.)?t\.me/[\w_+/-]+',
    r'(?:https?://)?(?:www\.)?vk\.com/[\w_+/-]+',
    r'(?:https?://)?(?:www\.)?[\w-]+\.(?:com|ru|net|org|pro|me|club|cc|io|fm|ai)/[\w_+-]*',
    r'@[\w_]{3,}',
    
    # Audio quality and rip stamps
    r'[\(\[]\s*(?:320\s*kbps|128\s*kbps|256\s*kbps|320k|128k|256k|flac|mp3|lossless|hq|hd|web|rip|cbr|vbr)\s*[\)\]]',
    r'\b(?:320|128|256)\s*kbps\b',
    r'\b(?:320|128|256)k\b',
    
    # Common rip/promo phrases
    r'[\(\[]\s*(?:official\s+audio|official\s+video|official\s+music\s+video|official|audio|lyric\s+video|lyrics|клип|премьера)\s*[\)\]]',
    r'[\(\[]\s*(?:zaycev\.net|promodj\.com|pesni\.fm|sefon|mp3party|muzmo|drivemusic)\s*[\)\]]',
]


def clean_track_metadata(
    raw_title: Optional[str],
    raw_artist: Optional[str],
    file_name: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Clean promotional junk, channel handles, bitrates, and extract clean artist & title.
    
    Returns:
        (cleaned_title, cleaned_artist)
    """
    title = (raw_title or "").strip()
    artist = (raw_artist or "").strip()

    # If title is empty but file_name exists, try to extract from file_name
    if not title and file_name:
        base = re.sub(r'\.[a-zA-Z0-9]{2,5}$', '', file_name).strip()
        title = base

    # Filter out placeholder artists or channel links as artists
    junk_artists = {'unknown', 'telegram', 'audio', 'music', 'sound', 'track', 'channel', 'неизвестен', 'неизвестный'}
    if artist.lower() in junk_artists or re.match(r'^@[\w_]+$', artist) or 't.me/' in artist:
        artist = ""

    # Check if title has "Artist - Title" format
    if " - " in title:
        parts = title.split(" - ", 1)
        left = parts[0].strip()
        right = parts[1].strip()
        if not artist or artist.lower() in left.lower() or left.lower() in artist.lower():
            artist = left
            title = right
    elif " — " in title:
        parts = title.split(" — ", 1)
        left = parts[0].strip()
        right = parts[1].strip()
        if not artist or artist.lower() in left.lower() or left.lower() in artist.lower():
            artist = left
            title = right

    # Clean junk patterns from both title and artist
    for pat in METADATA_JUNK_PATTERNS:
        title = re.sub(pat, '', title, flags=re.IGNORECASE)
        artist = re.sub(pat, '', artist, flags=re.IGNORECASE)

    # Clean double spaces, dangling brackets, trailing punctuation
    def clean_punct(s: str) -> str:
        s = re.sub(r'\s+', ' ', s)
        s = re.sub(r'[\(\[]\s*[\)\]]', '', s)
        s = s.strip(" \t\n\r-_~|/\\")
        return s.strip()

    title = clean_punct(title)
    artist = clean_punct(artist)

    # Fallbacks if everything got stripped
    if not title:
        title = raw_title or "Unknown Track"
    if not artist:
        artist = raw_artist or "Unknown Artist"

    return title, artist
