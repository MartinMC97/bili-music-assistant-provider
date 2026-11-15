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
            "music-assistant-models is missing StreamDetails.extra_input_args, "
            "so ffmpeg cannot receive the Referer/User-Agent headers. "
            f"Music Assistant >= {MIN_MA_VERSION} is required "
            f"(models >= {MIN_MODELS_VERSION})."
        )

    return issues
