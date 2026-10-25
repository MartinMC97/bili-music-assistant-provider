"""Runtime compatibility checks against the Music Assistant provider API."""

from __future__ import annotations

from dataclasses import fields

from music_assistant_models.streamdetails import StreamDetails

MIN_MA_VERSION = "2.10.7"
MIN_MODELS_VERSION = "1.1.205"


def check_compat() -> list[str]:
    """Return a list of human-readable incompatibilities (empty means OK)."""
    issues: list[str] = []

    if "extra_input_args" not in {field.name for field in fields(StreamDetails)}:
        issues.append(
            "music-assistant-models 缺少 StreamDetails.extra_input_args，"
            "无法向 ffmpeg 注入 Referer/User-Agent。"
            f"需要 MA >= {MIN_MA_VERSION}（models >= {MIN_MODELS_VERSION}）。"
        )

    return issues
