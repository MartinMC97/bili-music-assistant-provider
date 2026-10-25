"""Tests for the pure WBI signing helpers (no network)."""

from __future__ import annotations

import re

import pytest
from bilibili import wbi

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"
GOLDEN_PARAMS = {
    "search_type": "video",
    "keyword": "周杰伦 晴天",
    "page": 1,
    "page_size": 20,
}
GOLDEN_WTS = 1700000000
GOLDEN_MIXIN_KEY = "ea1db124af3c7062474693fa704f4ff8"
GOLDEN_QUERY = (
    "keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6%20%E6%99%B4%E5%A4%A9&page=1&page_size=20"
    "&search_type=video&wts=1700000000"
)
GOLDEN_SIGNED = f"{GOLDEN_QUERY}&w_rid=afa4ee1349451cece35c24b7f0e6be04"


def test_mixin_key_enc_tab_matches_bilibili_table() -> None:
    """The permutation table is the one shipped by Bilibili's web player."""
    table = wbi.MIXIN_KEY_ENC_TAB
    assert len(table) == 64
    assert sorted(table) == list(range(64))
    assert table[:8] == [46, 47, 18, 2, 53, 8, 23, 32]
    assert table[-8:] == [57, 62, 11, 36, 20, 34, 44, 52]


def test_mixin_key_golden_vector() -> None:
    """The golden img_key/sub_key pair yields the documented mixin key."""
    result = wbi.mixin_key(IMG_KEY, SUB_KEY)
    assert result == GOLDEN_MIXIN_KEY
    assert len(result) == 32


def test_sign_params_golden_vector() -> None:
    """The task's golden vector must match byte for byte."""
    signed = wbi.sign_params(GOLDEN_PARAMS, IMG_KEY, SUB_KEY, wts=GOLDEN_WTS)
    assert signed == GOLDEN_SIGNED


def test_sign_params_wts_is_added_and_overridden() -> None:
    """A caller supplied ``wts`` key is replaced by the explicit timestamp."""
    signed = wbi.sign_params(
        {"b": "2", "a": "1", "wts": 1},
        IMG_KEY,
        SUB_KEY,
        wts=GOLDEN_WTS,
    )
    query, w_rid = signed.rsplit("&w_rid=", 1)
    assert query == f"a=1&b=2&wts={GOLDEN_WTS}"
    assert re.fullmatch(r"[0-9a-f]{32}", w_rid)


def test_sign_params_sorts_keys() -> None:
    """Parameter insertion order must not affect the signature."""
    params = {"z": "last", "a": "first", "m": "middle"}
    signed = wbi.sign_params(params, IMG_KEY, SUB_KEY, wts=GOLDEN_WTS)
    assert signed.startswith(f"a=first&m=middle&wts={GOLDEN_WTS}&z=last&w_rid=")

    reordered = wbi.sign_params(
        {"m": "middle", "z": "last", "a": "first"},
        IMG_KEY,
        SUB_KEY,
        wts=GOLDEN_WTS,
    )
    assert signed == reordered


def test_sign_params_strips_special_characters_from_values() -> None:
    """Values lose ``!'()*`` before URL encoding, as Bilibili does."""
    signed = wbi.sign_params(
        {"keyword": "a!b'c(d)e*f"},
        IMG_KEY,
        SUB_KEY,
        wts=GOLDEN_WTS,
    )
    assert "keyword=abcdef&" in signed


def test_sign_params_quotes_reserved_characters_once() -> None:
    """Reserved characters are percent encoded exactly once."""
    signed = wbi.sign_params(
        {"q": "a&b=c+d/?#"},
        IMG_KEY,
        SUB_KEY,
        wts=GOLDEN_WTS,
    )
    assert "q=a%26b%3Dc%2Bd%2F%3F%23&" in signed
    assert "%25" not in signed.split("&w_rid=", 1)[0]


def test_sign_params_auto_wts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit wts the current Unix timestamp is used."""
    monkeypatch.setattr(wbi.time, "time", lambda: 1700000000.9)
    signed = wbi.sign_params(GOLDEN_PARAMS, IMG_KEY, SUB_KEY)
    assert "wts=1700000000" in signed
    assert signed.count("wts=") == 1


def test_sign_params_handles_short_keys() -> None:
    """A truncated nav response must not crash the signer."""
    signed = wbi.sign_params({"a": "1"}, "short", "key", wts=GOLDEN_WTS)
    assert re.search(r"&w_rid=[0-9a-f]{32}$", signed)
