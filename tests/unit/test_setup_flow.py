"""Unit tests for :mod:`bilibili.setup_flow` (fake session/client, no network)."""

from __future__ import annotations

import asyncio
import base64
import builtins
import logging
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from bilibili import setup_flow
from bilibili.auth import BilibiliCredential
from bilibili.client import BiliApiError
from music_assistant.models.setup_flow import SetupFlowError, StepExpiredError
from music_assistant_models.enums import ConfigEntryType

FAKE_SESSDATA = "fake-sessdata-value"
FAKE_JCT = "fake-bili-jct"
FAKE_MID = "12345"
FAKE_QR_URL = "https://passport.bilibili.com/qrcode/fake?qrcode_key=fake-key"


class FakeSession:
    """Minimal stand-in for ``SetupSession`` recording every interaction."""

    def __init__(self, forms: list[dict[str, Any]], *, deadline: float | None = None) -> None:
        self.mass = SimpleNamespace(http_session=None)
        self.logger = logging.getLogger("test.setup_flow")
        self._forms = list(forms)
        self._deadline = deadline
        self.forms: list[tuple[str, list[Any]]] = []
        self.progress: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    async def form(
        self,
        entries: list[Any],
        step_id: str = "user",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.forms.append((step_id, list(entries)))
        return self._forms.pop(0)

    async def progress_until(
        self,
        awaitable: Any,
        step_id: str,
        text: str | None = None,
        image: str | None = None,
        expires_in: float | None = None,
    ) -> Any:
        self.progress.append(
            {"step_id": step_id, "text": text, "image": image, "expires_in": expires_in}
        )
        if self._deadline is None:
            return await awaitable
        try:
            async with asyncio.timeout(self._deadline):
                return await awaitable
        except TimeoutError as err:
            raise StepExpiredError from err

    async def finish(self, values: dict[str, Any]) -> dict[str, str]:
        self.finished.append(values)
        return {}


class FakeClient:
    """Stand-in for :class:`bilibili.client.BilibiliClient` (QR endpoints only)."""

    def __init__(
        self,
        *,
        qr: dict[str, str] | None = None,
        polls: list[tuple[int, dict[str, str]]] | None = None,
        spi: dict[str, str] | None = None,
        generate_error: Exception | None = None,
        poll_error: Exception | None = None,
        spi_error: Exception | None = None,
    ) -> None:
        self.qr = qr if qr is not None else {"url": FAKE_QR_URL, "qrcode_key": "fake-key"}
        self.polls = list(polls or [])
        self.spi_result = spi if spi is not None else {"b_3": "fake-buvid3", "b_4": "fake-buvid4"}
        self.generate_error = generate_error
        self.poll_error = poll_error
        self.spi_error = spi_error
        self.init_kwargs: dict[str, Any] = {}
        self.generate_calls = 0
        self.poll_calls = 0
        self.spi_calls = 0
        self.closed = False

    async def qrcode_generate(self) -> dict[str, str]:
        self.generate_calls += 1
        if self.generate_error is not None:
            raise self.generate_error
        return self.qr

    async def qrcode_poll(self, qrcode_key: str) -> tuple[int, dict[str, str]]:
        self.poll_calls += 1
        if self.poll_error is not None:
            raise self.poll_error
        assert self.polls, "qrcode_poll called more often than the test provided"
        return self.polls.pop(0)

    async def spi(self) -> dict[str, str]:
        self.spi_calls += 1
        if self.spi_error is not None:
            raise self.spi_error
        return self.spi_result

    async def close(self) -> None:
        self.closed = True


def install_client(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FakeClient:
    """Make the flow construct *client* instead of a real BilibiliClient."""

    def factory(**kwargs: Any) -> FakeClient:
        client.init_kwargs = kwargs
        return client

    monkeypatch.setattr(setup_flow, "BilibiliClient", factory)
    return client


def stored_credential(session: FakeSession) -> BilibiliCredential:
    """Deserialize the credential_json of the (single) finish call."""
    assert len(session.finished) == 1
    return BilibiliCredential.from_json(session.finished[0]["credential_json"])


def test_flow_constants_match_the_contract() -> None:
    """The documented QR window/poll delay/status codes are not silently changed."""
    assert setup_flow.QR_EXPIRES_IN == 180
    assert setup_flow.QR_POLL_INTERVAL == 2.0
    assert setup_flow.QR_STATUS_SUCCESS == 0
    assert setup_flow.QR_STATUS_EXPIRED == 86038
    assert {setup_flow.QR_STATUS_WAITING_SCAN, setup_flow.QR_STATUS_WAITING_CONFIRM} == {
        86101,
        86090,
    }


async def test_anonymous_clears_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing anonymous stores nothing and never creates a client."""
    session = FakeSession([{setup_flow.CONF_METHOD: "anonymous"}])
    client = install_client(monkeypatch, FakeClient())

    await setup_flow.run_setup(session)

    step_id, entries = session.forms[0]
    assert step_id == "choose"
    assert entries[0].key == setup_flow.CONF_METHOD
    assert entries[0].default_value == "qr"
    assert [option.value for option in entries[0].options] == ["qr", "manual", "anonymous"]
    assert session.finished == [{"credential_json": ""}]
    assert session.progress == []
    assert client.generate_calls == 0


async def test_manual_form_builds_credential_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual entry stores a BilibiliCredential JSON with SESSDATA and DedeUserID."""
    session = FakeSession(
        [
            {setup_flow.CONF_METHOD: "manual"},
            {
                setup_flow.CONF_SESSDATA: f"  {FAKE_SESSDATA}  ",
                setup_flow.CONF_DEDEUSERID: FAKE_MID,
            },
        ]
    )
    install_client(monkeypatch, FakeClient())

    await setup_flow.run_setup(session)

    assert [step_id for step_id, _ in session.forms] == ["choose", "manual"]
    sessdata_entry, dedeuserid_entry = session.forms[1][1]
    assert sessdata_entry.type is ConfigEntryType.SECURE_STRING
    assert sessdata_entry.required is True
    assert dedeuserid_entry.type is ConfigEntryType.STRING
    assert dedeuserid_entry.required is False
    credential = stored_credential(session)
    assert credential.sessdata == FAKE_SESSDATA
    assert credential.dedeuserid == FAKE_MID
    assert credential.has_login is True
    assert credential.saved_at > 0
    assert session.progress == []


async def test_manual_rejects_empty_sessdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only SESSDATA never produces a usable credential."""
    session = FakeSession([{setup_flow.CONF_METHOD: "manual"}, {setup_flow.CONF_SESSDATA: "   "}])
    install_client(monkeypatch, FakeClient())

    with pytest.raises(SetupFlowError):
        await setup_flow.run_setup(session)
    assert session.finished == []


async def test_qr_success_stores_cookies_and_buvid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmed QR scan stores the cookies plus the spi buvid fingerprint."""
    monkeypatch.setattr(setup_flow, "QR_POLL_INTERVAL", 0)
    session = FakeSession([{setup_flow.CONF_METHOD: "qr"}])
    client = install_client(
        monkeypatch,
        FakeClient(
            polls=[
                (setup_flow.QR_STATUS_WAITING_SCAN, {}),
                (setup_flow.QR_STATUS_WAITING_CONFIRM, {}),
                (
                    setup_flow.QR_STATUS_SUCCESS,
                    {"SESSDATA": FAKE_SESSDATA, "bili_jct": FAKE_JCT, "DedeUserID": FAKE_MID},
                ),
            ],
            spi={"b_3": "fake-buvid3", "b_4": "fake-buvid4"},
        ),
    )

    await setup_flow.run_setup(session)

    # the QR client is created without any credential (anonymous fingerprint request)
    assert client.init_kwargs["credential"].has_login is False
    assert client.init_kwargs["mass"] is session.mass
    assert client.generate_calls == 1
    assert client.poll_calls == 3
    assert client.spi_calls == 1
    assert client.closed is True

    progress = session.progress[0]
    assert progress["step_id"] == "scan_qr"
    assert progress["expires_in"] == 180
    assert progress["image"].startswith("data:image/png;base64,")
    png = base64.b64decode(progress["image"].split(",", 1)[1])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    credential = stored_credential(session)
    assert credential.sessdata == FAKE_SESSDATA
    assert credential.bili_jct == FAKE_JCT
    assert credential.dedeuserid == FAKE_MID
    assert credential.buvid3 == "fake-buvid3"
    assert credential.buvid4 == "fake-buvid4"
    assert credential.has_login is True


async def test_qr_expired_raises_step_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bilibili's 86038 code ends the flow as timed out."""
    monkeypatch.setattr(setup_flow, "QR_POLL_INTERVAL", 0)
    session = FakeSession([{setup_flow.CONF_METHOD: "qr"}])
    client = install_client(
        monkeypatch,
        FakeClient(polls=[(setup_flow.QR_STATUS_EXPIRED, {})]),
    )

    with pytest.raises(StepExpiredError):
        await setup_flow.run_setup(session)

    assert session.finished == []
    assert client.closed is True
    assert client.spi_calls == 0


async def test_qr_timeout_raises_step_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """The progress-step deadline cancels the polling loop with StepExpiredError."""
    monkeypatch.setattr(setup_flow, "QR_POLL_INTERVAL", 0.01)
    session = FakeSession(
        [{setup_flow.CONF_METHOD: "qr"}],
        deadline=0.05,
    )
    client = install_client(
        monkeypatch,
        FakeClient(polls=[(setup_flow.QR_STATUS_WAITING_SCAN, {})] * 1000),
    )

    with pytest.raises(StepExpiredError):
        await setup_flow.run_setup(session)

    assert session.finished == []
    assert client.closed is True


async def test_missing_segno_falls_back_to_manual_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without segno the flow asks for SESSDATA instead of raising."""
    monkeypatch.setattr(setup_flow, "_import_segno", lambda: None)
    session = FakeSession(
        [
            {setup_flow.CONF_METHOD: "qr"},
            {setup_flow.CONF_SESSDATA: FAKE_SESSDATA, setup_flow.CONF_DEDEUSERID: FAKE_MID},
        ]
    )
    client = install_client(monkeypatch, FakeClient())

    await setup_flow.run_setup(session)

    assert [step_id for step_id, _ in session.forms] == ["choose", "manual"]
    sessdata_entry = session.forms[1][1][0]
    assert setup_flow.SEGNO_REQUIREMENT in (sessdata_entry.description or "")
    assert "install" in (sessdata_entry.description or "").lower()
    assert session.progress == []
    assert client.generate_calls == 0
    assert stored_credential(session).sessdata == FAKE_SESSDATA


def test_import_segno_returns_none_when_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The segno import is lazy and a missing package is not an error."""
    real_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "segno":
            raise ImportError("No module named 'segno'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    assert setup_flow._import_segno() is None


async def test_qr_api_error_becomes_setup_flow_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected QR request is translated into SetupFlowError and the client closed."""
    session = FakeSession([{setup_flow.CONF_METHOD: "qr"}])
    client = install_client(
        monkeypatch,
        FakeClient(
            generate_error=BiliApiError(-400, "请求错误", "/x/passport-login/web/qrcode/generate")
        ),
    )

    with pytest.raises(SetupFlowError):
        await setup_flow.run_setup(session)

    assert session.finished == []
    assert client.closed is True


async def test_qr_network_error_becomes_setup_flow_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network failure while polling is translated into SetupFlowError."""
    monkeypatch.setattr(setup_flow, "QR_POLL_INTERVAL", 0)
    session = FakeSession([{setup_flow.CONF_METHOD: "qr"}])
    client = install_client(
        monkeypatch,
        FakeClient(poll_error=aiohttp.ClientConnectionError("connection reset")),
    )

    with pytest.raises(SetupFlowError):
        await setup_flow.run_setup(session)

    assert session.finished == []
    assert client.closed is True


async def test_qr_login_survives_spi_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing spi call must not discard an otherwise successful login."""
    monkeypatch.setattr(setup_flow, "QR_POLL_INTERVAL", 0)
    session = FakeSession([{setup_flow.CONF_METHOD: "qr"}])
    install_client(
        monkeypatch,
        FakeClient(
            polls=[
                (
                    setup_flow.QR_STATUS_SUCCESS,
                    {"SESSDATA": FAKE_SESSDATA, "DedeUserID": FAKE_MID},
                )
            ],
            spi_error=BiliApiError(-1, "busy", "/x/frontend/finger/spi"),
        ),
    )

    await setup_flow.run_setup(session)

    credential = stored_credential(session)
    assert credential.has_login is True
    assert credential.buvid3 == ""
    assert credential.buvid4 == ""
