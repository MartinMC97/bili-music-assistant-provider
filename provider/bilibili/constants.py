"""Constants for the Bilibili music provider."""

from __future__ import annotations

from typing import Final

PROVIDER_DOMAIN: Final = "bilibili"
PROVIDER_NAME: Final = "Bilibili"

BILI_API: Final = "https://api.bilibili.com"
BILI_PASSPORT: Final = "https://passport.bilibili.com"
BILI_REFERER: Final = "https://www.bilibili.com"

# A desktop UA is required by several endpoints and by the audio CDN.
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# config keys
# ---------------------------------------------------------------------------
CONF_QUALITY: Final = "quality"
CONF_MIN_DURATION: Final = "min_duration"
CONF_MAX_DURATION: Final = "max_duration"
CONF_SEARCH_LIMIT: Final = "search_limit"
CONF_VERIFY_STREAM_URL: Final = "verify_stream_url"
CONF_SESSDATA_MANUAL: Final = "sessdata_manual"
CONF_CREDENTIAL_JSON: Final = "credential_json"

QUALITY_AUTO: Final = "auto"
QUALITY_FLAC: Final = "flac"
QUALITY_DOLBY: Final = "dolby"
QUALITY_AAC192: Final = "aac192"
QUALITY_AAC132: Final = "aac132"
QUALITY_AAC64: Final = "aac64"

QUALITY_OPTIONS: Final = (
    QUALITY_AUTO,
    QUALITY_FLAC,
    QUALITY_DOLBY,
    QUALITY_AAC192,
    QUALITY_AAC132,
    QUALITY_AAC64,
)

# Audio ids returned by /x/player/playurl?fnval=16:
#   30216 64k AAC, 30232 132k AAC, 30280 192k AAC,
#   30250 Dolby, 30251 Hi-Res FLAC.
AUDIO_ID_RANK: Final = {
    30251: 50,  # FLAC
    30250: 40,  # Dolby
    30280: 30,
    30232: 20,
    30216: 10,
}

QUALITY_MAX_RANK: Final = {
    QUALITY_AUTO: 100,
    QUALITY_FLAC: 50,
    QUALITY_DOLBY: 40,
    QUALITY_AAC192: 30,
    QUALITY_AAC132: 20,
    QUALITY_AAC64: 10,
}

DEFAULT_MIN_DURATION: Final = 60
DEFAULT_MAX_DURATION: Final = 900
DEFAULT_SEARCH_LIMIT: Final = 20

# ---------------------------------------------------------------------------
# caches / throttling
# ---------------------------------------------------------------------------
WBI_CACHE_TTL: Final = 6 * 3600
VIEW_CACHE_TTL: Final = 24 * 3600
SEARCH_CACHE_TTL: Final = 600
RANKING_CACHE_TTL: Final = 3600
FAVORITES_CACHE_TTL: Final = 300

API_RATE_LIMIT: Final = 3
API_RATE_PERIOD: Final = 1.0
SEARCH_RATE_LIMIT: Final = 1
SEARCH_RATE_PERIOD: Final = 1.0

STREAM_EXPIRATION_MIN: Final = 300
STREAM_EXPIRATION_MAX: Final = 7200
STREAM_EXPIRATION_MARGIN: Final = 60

PROBE_TIMEOUT: Final = 1.5
PROBE_BYTES: Final = 1024
CDN_HEALTH_TTL: Final = 600

# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
# A single -headers argument, CRLF separated as ffmpeg expects.
FFMPEG_HEADERS: Final = [
    "-headers",
    (f"Referer: {BILI_REFERER}\r\nOrigin: {BILI_REFERER}\r\nUser-Agent: {USER_AGENT}\r\n"),
]

# ---------------------------------------------------------------------------
# title noise
# ---------------------------------------------------------------------------
# Applied after HTML tags are stripped. Kept deliberately small: removing too
# much hurts lyrics matching and search relevance.
NOISE_PATTERNS: Final = (
    r"【[^】]{0,40}(?:无损|高音质|hi-?res|4k|8k|修复|官方|完整版|纯享|动态歌词|歌词|字幕)[^】]{0,40}】",
    r"[（(]\s*(?:官方\s*)?(?:mv|music\s*video|lyrics?|audio|live|cover|remix|ver\.?|version)\s*[)）]",
    r"官方\s*(?:mv|music\s*video|音频|完整版)",
    r"高音质|无损音质|hi-?res|动态歌词|歌词版|完整版|纯享版|纯享|超清|高清修复|重制版",
    r"中日字幕|双语字幕|中文字幕|字幕",
    r"试听|伴奏|钢琴版|翻唱|翻自|改编|原唱|纯音乐",
)

# Titles longer than this after cleaning are almost certainly not song titles.
MAX_CLEAN_TITLE_LENGTH: Final = 120
