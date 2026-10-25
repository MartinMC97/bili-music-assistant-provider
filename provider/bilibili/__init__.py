"""Bilibili music provider for Music Assistant (skeleton)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.enums import ProviderFeature
from music_assistant_models.errors import ProviderUnavailableError
from music_assistant.models.music_provider import MusicProvider

from .compat import check_compat

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

SUPPORTED_FEATURES = {ProviderFeature.SEARCH}


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize the provider."""
    return BilibiliProvider(mass, manifest, config, SUPPORTED_FEATURES)


class BilibiliProvider(MusicProvider):
    """Bilibili video audio as a Music Assistant music source."""

    async def handle_async_init(self) -> None:
        """Validate the runtime and prepare the provider."""
        if issues := check_compat():
            raise ProviderUnavailableError("; ".join(issues))
