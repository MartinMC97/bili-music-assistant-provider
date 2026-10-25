"""Bilibili credential handling (pure Python, no MA imports)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from typing import Any, Final

_COOKIE_KEYS: Final = (
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "sid",
    "buvid3",
    "buvid4",
)


@dataclass(frozen=True, slots=True)
class BilibiliCredential:
    """The subset of Bilibili cookies needed for read-only, authenticated access."""

    sessdata: str = ""
    bili_jct: str = ""
    dedeuserid: str = ""
    buvid3: str = ""
    buvid4: str = ""
    saved_at: int = 0

    @property
    def has_login(self) -> bool:
        """Return True when the credential can identify a user."""
        return bool(self.sessdata and self.dedeuserid)

    @property
    def mid(self) -> int:
        """Return the numeric user id, or 0 when unknown."""
        try:
            return int(self.dedeuserid)
        except (TypeError, ValueError):
            return 0

    def cookie_header(self) -> str:
        """Build the Cookie header value, omitting empty fields."""
        pairs: list[str] = []
        if self.sessdata:
            pairs.append(f"SESSDATA={self.sessdata}")
        if self.bili_jct:
            pairs.append(f"bili_jct={self.bili_jct}")
        if self.dedeuserid:
            pairs.append(f"DedeUserID={self.dedeuserid}")
        if self.buvid3:
            pairs.append(f"buvid3={self.buvid3}")
        if self.buvid4:
            pairs.append(f"buvid4={self.buvid4}")
        return "; ".join(pairs)

    def to_json(self) -> str:
        """Serialize for provider setup_data."""
        return json.dumps(
            {
                "sessdata": self.sessdata,
                "bili_jct": self.bili_jct,
                "dedeuserid": self.dedeuserid,
                "buvid3": self.buvid3,
                "buvid4": self.buvid4,
                "saved_at": self.saved_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> BilibiliCredential:
        """Deserialize a stored credential; malformed input yields an empty one."""
        if not raw:
            return cls()
        try:
            data: dict[str, Any] = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            sessdata=str(data.get("sessdata") or ""),
            bili_jct=str(data.get("bili_jct") or ""),
            dedeuserid=str(data.get("dedeuserid") or ""),
            buvid3=str(data.get("buvid3") or ""),
            buvid4=str(data.get("buvid4") or ""),
            saved_at=int(data.get("saved_at") or 0),
        )

    @classmethod
    def from_cookies(cls, cookies: dict[str, str] | Any) -> BilibiliCredential:
        """Build a credential from a cookie mapping (case-insensitive names)."""
        normalized: dict[str, str] = {}
        items = cookies.items() if hasattr(cookies, "items") else []
        for key, value in items:
            normalized[str(key).lower()] = str(value)
        return cls(
            sessdata=normalized.get("sessdata", ""),
            bili_jct=normalized.get("bili_jct", ""),
            dedeuserid=normalized.get("dedeuserid", ""),
            buvid3=normalized.get("buvid3", ""),
            buvid4=normalized.get("buvid4", ""),
            saved_at=int(time.time()),
        )

    def merged(self, other: BilibiliCredential) -> BilibiliCredential:
        """Return a copy where non-empty fields of *other* win."""
        values: dict[str, str] = {}
        for field_name in ("sessdata", "bili_jct", "dedeuserid", "buvid3", "buvid4"):
            values[field_name] = getattr(other, field_name) or getattr(self, field_name)
        values["saved_at"] = int(time.time())
        return replace(self, **values)

    def with_buvid(self, buvid3: str, buvid4: str) -> BilibiliCredential:
        """Return a copy carrying the anonymous device fingerprint cookies."""
        return replace(
            self,
            buvid3=self.buvid3 or buvid3,
            buvid4=self.buvid4 or buvid4,
            saved_at=int(time.time()),
        )
