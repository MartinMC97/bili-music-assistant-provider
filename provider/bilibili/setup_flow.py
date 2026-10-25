"""Interactive setup flow for the Bilibili music provider.

The flow is one plain coroutine authored against
:class:`music_assistant.models.setup_flow.SetupSession`. It asks for a login
method and then either finishes anonymously, collects a pasted SESSDATA cookie,
or shows a QR code the user scans in the Bilibili app.

``segno`` (the pure-Python QR renderer) is an optional dependency and is
imported lazily: when it is missing the flow degrades to the manual form
instead of failing. Nothing here writes logs or exceptions containing the
collected cookies.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from io import BytesIO
from types import ModuleType
from typing import TYPE_CHECKING, Final

import aiohttp
from music_assistant.models.setup_flow import SetupFlowError, StepExpiredError
from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
from music_assistant_models.enums import ConfigEntryType
from music_assistant_models.errors import ResourceTemporarilyUnavailable

from . import constants
from .auth import BilibiliCredential
from .client import BiliApiError, BilibiliClient

if TYPE_CHECKING:
    from collections.abc import Mapping

    from music_assistant.models.setup_flow import SetupSession

LOGGER: Final = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# flow-local keys (shown in a form, never persisted as provider config)
# ---------------------------------------------------------------------------
CONF_METHOD: Final = "method"
CONF_SESSDATA: Final = "sessdata"
CONF_DEDEUSERID: Final = "dedeuserid"

METHOD_QR: Final = "qr"
METHOD_MANUAL: Final = "manual"
METHOD_ANONYMOUS: Final = "anonymous"

# ---------------------------------------------------------------------------
# QR login
# ---------------------------------------------------------------------------
# scan window (seconds) a shown QR code is valid for; enforced by progress_until
QR_EXPIRES_IN: Final = 180.0
# delay (seconds) between two poll attempts
QR_POLL_INTERVAL: Final = 2.0
# inner data.code values returned by the QR poll endpoint
QR_STATUS_SUCCESS: Final = 0
QR_STATUS_EXPIRED: Final = 86038
QR_STATUS_WAITING_SCAN: Final = 86101
QR_STATUS_WAITING_CONFIRM: Final = 86090

# pin documented in docs/implementation-plan.md (optional dependency)
SEGNO_REQUIREMENT: Final = "segno==1.6.6"

_SESSDATA_DESCRIPTION: Final = (
    "SESSDATA cookie of a logged-in bilibili.com session "
    "(browser DevTools -> Application -> Cookies)."
)
_DEDEUSERID_DESCRIPTION: Final = (
    "Optional DedeUserID cookie from the same session; without it the login cannot be "
    "identified and account features such as favourites stay unavailable."
)


def _segno_install_hint() -> str:
    """Return the user-facing hint shown when the optional QR renderer is missing."""
    return (
        "QR code login is unavailable because the optional segno package is not "
        f"installed. Install it with `pip install {SEGNO_REQUIREMENT}` (inside the "
        "Music Assistant environment) and run setup again, or paste a SESSDATA "
        "cookie below."
    )


def _import_segno() -> ModuleType | None:
    """Import the optional ``segno`` package, returning None when it is missing."""
    try:
        import segno
    except ImportError:
        return None
    return segno


def _choose_entries() -> list[ConfigEntry]:
    """Build the login-method form entries (default: QR code)."""
    return [
        ConfigEntry(
            key=CONF_METHOD,
            type=ConfigEntryType.STRING,
            required=True,
            default_value=METHOD_QR,
            value=METHOD_QR,
            expanded_options=True,
            options=[
                ConfigValueOption(METHOD_QR, title="QR code (recommended)"),
                ConfigValueOption(METHOD_MANUAL, title="Paste SESSDATA cookie"),
                ConfigValueOption(METHOD_ANONYMOUS, title="Continue anonymously"),
            ],
        )
    ]


def _manual_entries(*, installation_hint: str | None = None) -> list[ConfigEntry]:
    """Build the manual cookie form entries."""
    sessdata_description = _SESSDATA_DESCRIPTION
    if installation_hint:
        sessdata_description = f"{installation_hint} {sessdata_description}"
    return [
        ConfigEntry(
            key=CONF_SESSDATA,
            type=ConfigEntryType.SECURE_STRING,
            required=True,
            description=sessdata_description,
        ),
        ConfigEntry(
            key=CONF_DEDEUSERID,
            type=ConfigEntryType.STRING,
            required=False,
            description=_DEDEUSERID_DESCRIPTION,
        ),
    ]


async def run_setup(session: SetupSession) -> None:
    """
    Run the Bilibili setup flow: anonymous, manual cookie, or QR code login.

    :param session: The setup session driving the flow.
    """
    submitted = await session.form(_choose_entries(), step_id="choose")
    method = str(submitted.get(CONF_METHOD) or METHOD_QR).strip().lower()
    LOGGER.debug("Bilibili setup flow started with method %s", method)

    if method == METHOD_ANONYMOUS:
        # Explicitly clear a previous credential: a reconfigure flow merges the
        # submitted values over the stored setup_data.
        await session.finish({constants.CONF_CREDENTIAL_JSON: ""})
        return

    if method == METHOD_MANUAL:
        await _finish_manual_login(session)
        return

    segno = _import_segno()
    if segno is None:
        # optional dependency: degrade to the manual form instead of breaking setup
        LOGGER.warning("segno is not installed, falling back to manual SESSDATA entry")
        await _finish_manual_login(session, installation_hint=_segno_install_hint())
        return

    await _finish_qr_login(session, segno)


async def _finish_manual_login(
    session: SetupSession,
    *,
    installation_hint: str | None = None,
) -> None:
    """
    Collect SESSDATA/DedeUserID, build the credential and finish the flow.

    :param session: The setup session driving the flow.
    :param installation_hint: Optional note prefixed to the SESSDATA description
        (used when QR login is unavailable).
    """
    submitted = await session.form(
        _manual_entries(installation_hint=installation_hint),
        step_id="manual",
    )
    sessdata = str(submitted.get(CONF_SESSDATA) or "").strip()
    if not sessdata:
        raise SetupFlowError("A SESSDATA cookie is required to sign in to Bilibili")
    credential = BilibiliCredential(
        sessdata=sessdata,
        dedeuserid=str(submitted.get(CONF_DEDEUSERID) or "").strip(),
        saved_at=int(time.time()),
    )
    await session.finish({constants.CONF_CREDENTIAL_JSON: credential.to_json()})


async def _finish_qr_login(session: SetupSession, segno: ModuleType) -> None:
    """
    Show a QR code, wait for the confirmed scan and finish with the credential.

    :param session: The setup session driving the flow.
    :param segno: The imported segno module used to render the QR code.
    """
    client = BilibiliClient(mass=session.mass, credential=BilibiliCredential())
    try:
        generated = await client.qrcode_generate()
        qr_url = str(generated.get("url") or "")
        qr_key = str(generated.get("qrcode_key") or "")
        if not qr_url or not qr_key:
            raise SetupFlowError("Bilibili did not return a usable login QR code")
        try:
            qr_image = _render_qr_data_uri(segno, qr_url)
        except Exception as err:  # third-party renderer failures
            raise SetupFlowError("Could not render the Bilibili login QR code") from err
        credential = await session.progress_until(
            _poll_qr_login(client, qr_key),
            step_id="scan_qr",
            image=qr_image,
            expires_in=QR_EXPIRES_IN,
        )
        await session.finish({constants.CONF_CREDENTIAL_JSON: credential.to_json()})
    except BiliApiError as err:
        raise SetupFlowError(f"Bilibili login failed: {err.message} (code {err.code})") from err
    except (ResourceTemporarilyUnavailable, TimeoutError, OSError, aiohttp.ClientError) as err:
        raise SetupFlowError("Could not reach Bilibili to complete the login") from err
    finally:
        await client.close()


def _render_qr_data_uri(segno: ModuleType, content: str) -> str:
    """
    Render *content* as a PNG QR code and return it as a ``data:`` URI.

    :param segno: The imported segno module.
    :param content: The URL encoded in the QR code.
    """
    buffer = BytesIO()
    segno.make(content).save(buffer, kind="png", scale=6, border=2)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def _poll_qr_login(
    client: BilibiliClient,
    qrcode_key: str,
) -> BilibiliCredential:
    """
    Poll the QR login endpoint until the scan is confirmed.

    Raises StepExpiredError when Bilibili reports the QR code expired, so the
    flow ends as timed out; the progress step deadline does the same on timeout.

    :param client: API client bound to this flow.
    :param qrcode_key: The poll token from qrcode_generate.
    """
    while True:
        code, cookies = await client.qrcode_poll(qrcode_key)
        if code == QR_STATUS_SUCCESS:
            return await _credential_from_cookies(client, cookies)
        if code == QR_STATUS_EXPIRED:
            raise StepExpiredError("The Bilibili login QR code expired")
        if code not in (QR_STATUS_WAITING_SCAN, QR_STATUS_WAITING_CONFIRM):
            # unknown state: keep polling, the progress step deadline bounds the wait
            LOGGER.debug("Unexpected Bilibili QR login status %s, still polling", code)
        await asyncio.sleep(QR_POLL_INTERVAL)


async def _credential_from_cookies(
    client: BilibiliClient,
    cookies: Mapping[str, str],
) -> BilibiliCredential:
    """
    Build the credential from the login cookies and fill in the buvid fingerprint.

    :param client: API client bound to this flow.
    :param cookies: Cookies returned by the successful poll.
    """
    credential = BilibiliCredential.from_cookies(cookies)
    if not credential.has_login:
        raise SetupFlowError(
            "Bilibili confirmed the login but returned no usable SESSDATA/DedeUserID cookie"
        )
    try:
        fingerprint = await client.spi()
    except (
        BiliApiError,
        ResourceTemporarilyUnavailable,
        TimeoutError,
        OSError,
        aiohttp.ClientError,
    ) as err:
        # the login itself succeeded; buvid only improves how requests are accepted
        LOGGER.warning("Could not fetch Bilibili buvid cookies after login: %s", err)
        return credential
    return credential.with_buvid(
        str(fingerprint.get("b_3") or ""),
        str(fingerprint.get("b_4") or ""),
    )
