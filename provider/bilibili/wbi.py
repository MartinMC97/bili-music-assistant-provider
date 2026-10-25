"""WBI request signing for the Bilibili web API.

Bilibili protects a number of endpoints (search, space videos, ...) with a
signature derived from the ``wbi_img`` keys returned by ``/x/web-interface/nav``.
The algorithm is a fixed permutation of ``img_key + sub_key`` followed by an
MD5 over the sorted, URL-encoded query string.

This module is pure Python and has no Music Assistant imports so it can be
tested (and reused) in isolation.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Final
from urllib.parse import quote

# Fixed permutation table used by Bilibili's web client.
# Source: the ``mixinKeyEncTab`` array shipped with the web player JS.
MIXIN_KEY_ENC_TAB: Final[list[int]] = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

# Characters stripped from every value before URL encoding, as done by Bilibili.
_STRIP_TABLE: Final = str.maketrans("", "", "!'()*")

_MIXIN_KEY_LENGTH: Final = 32


def mixin_key(img_key: str, sub_key: str) -> str:
    """Return the 32 character mixin key derived from the WBI image keys."""
    original = f"{img_key}{sub_key}"
    # The table only references the first 64 characters; pad defensively so a
    # truncated/corrupt nav response cannot raise IndexError.
    if len(original) < 64:
        original = original.ljust(64, "0")
    return "".join(original[index] for index in MIXIN_KEY_ENC_TAB)[:_MIXIN_KEY_LENGTH]


def _encode_value(value: object) -> str:
    """URL encode a single query value the way Bilibili signs it."""
    text = "" if value is None else str(value)
    return quote(text.translate(_STRIP_TABLE), safe="")


def sign_params(
    params: Mapping[str, str | int],
    img_key: str,
    sub_key: str,
    *,
    wts: int | None = None,
) -> str:
    """Return the full signed query string including ``wts`` and ``w_rid``.

    The caller must send the returned string verbatim (do not re-encode it).
    """
    timestamp = int(time.time()) if wts is None else int(wts)
    signed = {str(key): value for key, value in params.items()}
    signed.pop("wts", None)
    signed["wts"] = timestamp

    query = "&".join(
        f"{quote(key, safe='')}={_encode_value(signed[key])}" for key in sorted(signed)
    )
    digest = hashlib.md5((query + mixin_key(img_key, sub_key)).encode()).hexdigest()
    return f"{query}&w_rid={digest}"
