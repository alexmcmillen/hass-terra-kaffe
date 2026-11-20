"""Tests for the API client against a local fake of Keycloak and mercury."""

import pytest
from aiohttp import ClientSession, web

from custom_components.terra_kaffe.api import (
    TerraKaffeAPI,
    TerraKaffeAPIError,
    TerraKaffeAuthError,
    TerraKaffeNotFoundError,
)

from .conftest import NESTED_REPORTED, make_twin


class FakeCloud:
    """Minimal stand-in for auth.terrakaffeservice.com + mercury."""

    def __init__(self):
        self.token_forms = []
        self.requests = []
        self.valid_tokens = {"access-1"}
        self.counter = 1
        self.refresh_ok = True
        self.expires_in = 120

    async def token(self, request):
        form = dict(await request.post())
        self.token_forms.append(form)
        if form.get("client_id") != "tk_android":
            return web.json_response({"error": "invalid_client"}, status=401)
        grant = form.get("grant_type")
        if grant == "password":
            if form.get("password") != "hunter2":
                return web.json_response(
                    {
                        "error": "invalid_grant",
                        "error_description": "Invalid user credentials",
                    },
                    status=401,
                )
        elif grant == "refresh_token":
            if not self.refresh_ok or not form.get("refresh_token", "").startswith(
                "refresh-"
            ):
                return web.json_response({"error": "invalid_grant"}, status=400)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)
        self.counter += 1
        access = f"access-{self.counter}"
        self.valid_tokens = {access}
        return web.json_response(
            {
                "access_token": access,
                "refresh_token": f"refresh-{self.counter}",
                "expires_in": self.expires_in,
            }
        )

    def _authed(self, request):
        return (
            request.headers.get("Authorization", "").removeprefix("Bearer ")
            in self.valid_tokens
        )

    async def api(self, request):
        body = await request.json() if request.can_read_body else None
        self.requests.append((request.method, request.path, body))
        if not self._authed(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        path = request.path
        if path == "/api/devices/":
            return web.json_response(
                {"devices": [{"device_id": "dev123456", "access_level": "owner"}]}
            )
        if path == "/api/devices/dev123456/twin":
            return web.json_response(make_twin(NESTED_REPORTED))
        if path == "/api/devices/dev123456/command":
            return web.json_response(
                {
                    "success": True,
                    "message": "ok",
                    "device_id": "dev123456",
                    "timestamp": "2026-11-19T14:00:00Z",
                }
            )
        if path == "/api/devices/broken/twin":
            return web.Response(status=503, text="upstream down")
        return web.json_response({"error": "Device not found"}, status=404)


@pytest.fixture
async def cloud(aiohttp_server, socket_enabled):
    fake = FakeCloud()
    app = web.Application()
    app.router.add_post("/token", fake.token)
    app.router.add_route("*", "/api/{tail:.*}", fake.api)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        api = TerraKaffeAPI(
            session,
            token_url=str(server.make_url("/token")),
            api_url=str(server.make_url("/api")),
        )
        yield fake, api


async def test_login_sends_expected_form(cloud):
    fake, api = cloud
    await api.async_login("me@example.com", "hunter2")
    assert fake.token_forms[0] == {
        "grant_type": "password",
        "client_id": "tk_android",
        "username": "me@example.com",
        "password": "hunter2",
        "scope": "openid profile offline_access",
    }
    assert api.refresh_token == "refresh-2"


async def test_bad_password_is_auth_error(cloud):
    _, api = cloud
    with pytest.raises(TerraKaffeAuthError, match="Invalid user credentials"):
        await api.async_login("me@example.com", "wrong")


async def test_devices_twin_and_commands(cloud):
    fake, api = cloud
    await api.async_login("me@example.com", "hunter2")
    assert await api.async_get_devices() == [
        {"device_id": "dev123456", "access_level": "owner"}
    ]
    twin = await api.async_get_twin("dev123456")
    assert twin["reported"]["device"]["status"] == 1
    await api.async_update_desired("dev123456", {"device": {"status": 2}})
    await api.async_send_drink_order("dev123456", "a1^5^Latte^$53|e1|f115")
    await api.async_cancel_brew("dev123456")
    assert fake.requests[-3:] == [
        ("PUT", "/api/devices/dev123456/twin", {"desired": {"device": {"status": 2}}}),
        (
            "POST",
            "/api/devices/dev123456/command",
            {"drink_order": "a1^5^Latte^$53|e1|f115"},
        ),
        ("POST", "/api/devices/dev123456/command", {"cancel_brew": True}),
    ]


async def test_401_triggers_refresh_and_retry(cloud):
    fake, api = cloud
    seen = []
    api.on_refresh_token_update = seen.append
    await api.async_login("me@example.com", "hunter2")
    fake.valid_tokens = set()  # server revokes the access token
    assert await api.async_get_devices()
    assert [f["grant_type"] for f in fake.token_forms] == ["password", "refresh_token"]
    assert seen == ["refresh-2", "refresh-3"]


async def test_dead_refresh_token_falls_back_to_password(cloud):
    fake, api = cloud
    api.set_credentials("me@example.com", "hunter2")
    api.set_refresh_token("refresh-stale")
    fake.refresh_ok = False
    assert await api.async_get_devices()
    assert [f["grant_type"] for f in fake.token_forms] == ["refresh_token", "password"]


async def test_no_way_to_authenticate(cloud):
    fake, api = cloud
    api.set_refresh_token("refresh-stale")
    fake.refresh_ok = False
    with pytest.raises(TerraKaffeAuthError):
        await api.async_get_devices()


async def test_expired_token_refreshes_before_request(cloud):
    fake, api = cloud
    fake.expires_in = 1  # shorter than the safety margin
    await api.async_login("me@example.com", "hunter2")
    api._expires_at = 0
    await api.async_get_devices()
    assert [f["grant_type"] for f in fake.token_forms] == ["password", "refresh_token"]
    assert all(r[0] == "GET" for r in fake.requests) and len(fake.requests) == 1


async def test_error_mapping(cloud):
    _, api = cloud
    await api.async_login("me@example.com", "hunter2")
    with pytest.raises(TerraKaffeNotFoundError):
        await api.async_get_twin("nope")
    with pytest.raises(TerraKaffeAPIError, match="503"):
        await api.async_get_twin("broken")


async def test_unreachable_host(socket_enabled):
    async with ClientSession() as session:
        api = TerraKaffeAPI(
            session,
            token_url="http://127.0.0.1:9/token",
            api_url="http://127.0.0.1:9/api",
        )
        with pytest.raises(TerraKaffeAPIError) as err:
            await api.async_login("me@example.com", "hunter2")
        assert not isinstance(err.value, TerraKaffeAuthError)
