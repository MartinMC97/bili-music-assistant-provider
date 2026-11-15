"""Translation-key consistency between the provider code and ``strings.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from music_assistant.constants import CONF_ENTRY_UNOFFICIAL_PROVIDER
from music_assistant_models.enums import ConfigEntryType

from bilibili import catalog, constants, setup_flow

from .test_provider import make_provider

if TYPE_CHECKING:
    from bilibili.client import BilibiliClient

REPO_ROOT = Path(__file__).resolve().parents[3]
STRINGS_PATH = REPO_ROOT / "provider" / "bilibili" / "strings.json"
MANIFEST_PATH = REPO_ROOT / "provider" / "bilibili" / "manifest.json"


def load_strings() -> dict[str, Any]:
    """Return the provider's source strings."""
    return cast("dict[str, Any]", json.loads(STRINGS_PATH.read_text(encoding="utf-8")))


async def test_config_entries_are_localized() -> None:
    """Every provider config entry has label/description/option keys in strings.json."""
    strings = load_strings()["config_entries"]
    provider, _ = await make_provider()

    common_keys = {CONF_ENTRY_UNOFFICIAL_PROVIDER.key}
    for entry in await provider.get_config_entries():
        # Hardcoded label/description must not be used (upstream check_config_entries).
        assert entry.label is None
        assert entry.description is None
        if entry.key in common_keys:
            continue
        spec = strings[entry.key]
        assert spec.get("label"), f"missing label for {entry.key}"
        if entry.type in (
            ConfigEntryType.STRING,
            ConfigEntryType.INTEGER,
            ConfigEntryType.BOOLEAN,
        ):
            assert spec.get("description"), f"missing description for {entry.key}"
        if entry.options:
            options = spec.get("options") or {}
            assert [option.value for option in entry.options] == list(options), (
                f"options for {entry.key} are not localized in the same order"
            )
        # Option titles themselves must come from strings.json, not in-code text.
        for option in entry.options or ():
            assert option.title is None, f"{entry.key}.{option.value} has a hardcoded title"


def test_setup_flow_entries_are_localized() -> None:
    """Setup flow form entries use keys from strings.json, including the segno hint."""
    strings = load_strings()["config_entries"]

    choose = setup_flow._choose_entries()[0]
    assert choose.label is None
    assert choose.description is None
    assert [option.value for option in choose.options or ()] == list(strings["method"]["options"])
    assert all(option.title is None for option in choose.options or ())

    manual = setup_flow._manual_entries()
    assert [entry.key for entry in manual] == ["sessdata", "dedeuserid"]
    assert all(entry.description is None for entry in manual)

    unavailable = setup_flow._manual_entries(qr_unavailable=True)[0]
    assert unavailable.translation_key == setup_flow.CONF_SESSDATA_QR_UNAVAILABLE
    assert setup_flow.SEGNO_REQUIREMENT in (unavailable.translation_params or [])
    assert "{0}" in strings[setup_flow.CONF_SESSDATA_QR_UNAVAILABLE]["description"]


async def test_recommendation_and_browse_labels_are_translatable() -> None:
    """Browse/recommendation folders carry a translation key backed by strings.json."""
    strings = load_strings()["media"]

    for row in catalog.recommendation_rows("bilibili_test"):
        assert row.translation_key
        assert row.name, "English fallback name is required"
        assert strings["recommendations"][row.translation_key]["name"]

    folders = await catalog.browse_items(cast("BilibiliClient", object()), "bilibili_test", "")
    for folder in folders:
        key = getattr(folder, "translation_key", None)
        assert key, f"browse folder {getattr(folder, 'item_id', '?')} has no translation key"
        assert strings["folder"][key]["name"]


def test_manifest_description_matches_strings() -> None:
    """The manifest description is mirrored in strings.json (Lokalise source)."""
    strings = load_strings()
    manifest = cast("dict[str, Any]", json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    assert manifest["description"] == strings["manifest"]["description"]
    assert manifest["name"] == constants.PROVIDER_NAME
