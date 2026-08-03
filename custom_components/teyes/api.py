"""Client for the Teyes Online cloud API.

The web portal wraps every call in a fixed envelope, reverse-engineered from
its bundle:

    request   POST multipart/form-data, one field `data`:
              base64( RSA_PKCS1v15( public key, JSON(params) ) )
    response  {"res": "A|B|C"} where
              A = base64( RSA( public key, AES key ) )
              B = base64( RSA( public key, AES iv ) )
              C = base64( AES-CBC-PKCS7( JSON payload ) )

Both keys ship inside the bundle, so A and B are decryptable with the private
half and C then yields a `{code, msg, time, data}` envelope. Note that `code`
runs the opposite way to the usual convention: 1 is success, 0 carries the
reason for the failure in `msg`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

from .const import CLIENT_API, DATA_API, TEYES_PRIVATE_KEY, TEYES_PUBLIC_KEY

_LOGGER = logging.getLogger(__name__)

# PKCS#1 v1.5 leaves keysize - 11 bytes per block; the keys are 2048-bit.
_RSA_BLOCK = 256 - 11
_TIMEOUT = aiohttp.ClientTimeout(total=30)

# The envelope's `code` is 1 when the call succeeded and 0 when it did not,
# with the reason in `msg` — the opposite of the usual convention.
_CODE_OK = (1, "1")

# Words the cloud uses when it is the session, not the request, that is wrong.
# The API answers in the account's language, so match both it and Chinese.
_AUTH_HINTS = (
    "token",
    "login",
    "log in",
    "expire",
    "unauthor",
    "not exist",
    "登录",
    "令牌",
    "过期",
    "失效",
    "账户不存在",
    "密码错误",
)


class TeyesError(Exception):
    """Base error for the Teyes API."""


class TeyesConnectionError(TeyesError):
    """The cloud could not be reached, or answered something unreadable."""


class TeyesAuthError(TeyesError):
    """The credentials or the session token were rejected."""


class TeyesApiError(TeyesError):
    """The cloud answered, but refused the call."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class TeyesCrypto:
    """The request/response envelope. Key parsing is done once, off-thread."""

    def __init__(self) -> None:
        self._public: Any = None
        self._private: Any = None
        self._lock = asyncio.Lock()

    def _load(self) -> None:
        self._public = load_pem_public_key(TEYES_PUBLIC_KEY.encode())
        self._private = load_pem_private_key(TEYES_PRIVATE_KEY.encode(), password=None)

    async def async_prepare(self) -> None:
        """Parse the embedded keys, which is slow enough to keep off the loop."""
        async with self._lock:
            if self._public is None:
                await asyncio.get_running_loop().run_in_executor(None, self._load)

    def encode(self, params: dict[str, Any]) -> str:
        """Encrypt the request parameters for the `data` form field."""
        raw = json.dumps(params, separators=(",", ":"), ensure_ascii=False).encode()
        # Anything the integration sends fits one block; longer bodies are
        # chunked the way JSEncrypt does it, which is untested against this
        # server but strictly better than truncating.
        out = b"".join(
            self._public.encrypt(raw[i : i + _RSA_BLOCK], rsa_padding.PKCS1v15())
            for i in range(0, max(len(raw), 1), _RSA_BLOCK)
        )
        return base64.b64encode(out).decode()

    def decode(self, res: str) -> Any:
        """Decrypt the `res` field of a response into the JSON it carries."""
        try:
            enc_key, enc_iv, ciphertext = res.split("|")
            key = self._private.decrypt(
                base64.b64decode(enc_key), rsa_padding.PKCS1v15()
            )
            iv = self._private.decrypt(base64.b64decode(enc_iv), rsa_padding.PKCS1v15())
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(base64.b64decode(ciphertext)) + decryptor.finalize()
            unpadder = sym_padding.PKCS7(128).unpadder()
            plain = unpadder.update(padded) + unpadder.finalize()
        except Exception as err:  # noqa: BLE001 - any malformed reply lands here
            raise TeyesConnectionError(f"Could not decrypt the reply: {err}") from err
        try:
            return json.loads(plain.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            raise TeyesConnectionError(f"Reply was not JSON: {err}") from err


class TeyesApiClient:
    """Talks to the Teyes cloud on behalf of one account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
        uid: str | None = None,
        device_id: str | None = None,
        timezone: str = "UTC",
        language: str = "en",
        on_session: Callable[[str, str], None] | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self.token = token
        self.uid = uid
        self.device_id = device_id
        self.timezone = timezone
        self.language = language
        self._on_session = on_session
        self._crypto = TeyesCrypto()
        self._login_lock = asyncio.Lock()
        # Whether the account's password hashes before it is sent. Never
        # observed on the wire, so it is settled on the first successful login
        # and remembered for the rest of the run.
        self._hash_password: bool | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """The HTTP session this client uses, shared with the MQTT socket."""
        return self._session

    @property
    def can_relogin(self) -> bool:
        """Whether a stale token can be replaced without asking the user."""
        return bool(self._email and self._password)

    async def async_call(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        base: str = CLIENT_API,
        authenticated: bool = True,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Make one API call and return its decoded `{code, msg, data}`.

        `device_id` names which car the call is about; it overrides the
        client's own so one account client can serve several cars.
        """
        await self._crypto.async_prepare()
        body: dict[str, Any] = {
            "timezone": self.timezone,
            "lang": self.language,
            **(params or {}),
        }
        if authenticated:
            body.setdefault("uid", self.uid or "")
            body.setdefault("imei", device_id or self.device_id or "")
            body.setdefault("token", self.token or "")

        url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
        form = aiohttp.FormData()
        form.add_field("data", self._crypto.encode(body))
        try:
            async with self._session.post(url, data=form, timeout=_TIMEOUT) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except asyncio.TimeoutError as err:
            raise TeyesConnectionError(f"{url} timed out") from err
        except aiohttp.ClientError as err:
            raise TeyesConnectionError(f"{url} failed: {err}") from err

        try:
            envelope = json.loads(text)
        except ValueError as err:
            raise TeyesConnectionError(
                f"{url} did not answer with JSON: {text[:120]}"
            ) from err
        if not isinstance(envelope, dict) or "res" not in envelope:
            # A few endpoints answer in the clear; pass those straight through.
            if isinstance(envelope, dict):
                return envelope
            raise TeyesConnectionError(f"{url} answered {text[:120]}")
        decoded = self._crypto.decode(envelope["res"])
        if not isinstance(decoded, dict):
            raise TeyesConnectionError(f"{url} answered {str(decoded)[:120]}")
        return decoded

    async def async_request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        base: str = CLIENT_API,
        device_id: str | None = None,
    ) -> Any:
        """Call an endpoint, refresh a stale session once, and return `data`.

        Raises TeyesAuthError when the session cannot be recovered, so Home
        Assistant can start a re-authentication flow.
        """
        result = await self.async_call(path, params, base=base, device_id=device_id)
        code = result.get("code")
        if code in _CODE_OK:
            return result.get("data")

        message = str(result.get("msg") or f"code {code}")
        if not self._looks_like_auth(message):
            raise TeyesApiError(message, code if isinstance(code, int) else None)

        if not self.can_relogin:
            raise TeyesAuthError(message)
        _LOGGER.debug("Session rejected (%s); logging in again", message)
        await self.async_login()
        result = await self.async_call(path, params, base=base, device_id=device_id)
        if result.get("code") in _CODE_OK:
            return result.get("data")
        raise TeyesAuthError(str(result.get("msg") or f"code {result.get('code')}"))

    @staticmethod
    def _looks_like_auth(message: str) -> bool:
        lowered = message.lower()
        return any(hint in lowered for hint in _AUTH_HINTS)

    async def async_login(self) -> dict[str, Any]:
        """Sign in with the account password and keep the session token."""
        if not (self._email and self._password):
            raise TeyesAuthError("No password stored for this account")
        async with self._login_lock:
            attempts = (
                [self._hash_password]
                if self._hash_password is not None
                # The portal may send the password as-is or as its md5; which
                # one this build wants was never seen on the wire, so try both.
                else [False, True]
            )
            last_message = "Login failed"
            for hashed in attempts:
                secret = (
                    hashlib.md5(self._password.encode()).hexdigest()
                    if hashed
                    else self._password
                )
                result = await self.async_call(
                    "/ucenter/login",
                    {"email": self._email, "password": secret},
                    authenticated=False,
                )
                if result.get("code") in _CODE_OK:
                    self._hash_password = hashed
                    return self._store_session(result.get("data") or {})
                last_message = str(result.get("msg") or f"code {result.get('code')}")
            raise TeyesAuthError(last_message)

    def _store_session(self, data: Any) -> dict[str, Any]:
        """Pick the token and account id out of a login reply."""
        if not isinstance(data, dict):
            raise TeyesAuthError("The login reply carried no account data")
        flat = _flatten(data)
        token = _first(flat, ("ucentertoken", "token", "accesstoken", "sessiontoken"))
        uid = _first(flat, ("uid", "userid", "ucenteruid", "accountid"))
        if not token:
            _LOGGER.debug("Login reply fields: %s", sorted(flat))
            raise TeyesAuthError("The login reply carried no session token")
        self.token = str(token)
        if uid:
            self.uid = str(uid)
        if not self.device_id:
            if device := _first(flat, ("imei", "deviceid", "sn", "carid")):
                self.device_id = str(device)
        if self._on_session and self.token and self.uid:
            self._on_session(self.token, self.uid)
        return data

    async def async_get_car_mq_state(self, device_id: str) -> dict[str, Any]:
        """Read a car's cloud state and the MQTT credentials that go with it.

        Returns `acc`, `isonline`, `iscamera`, `isqj` and a `server` block
        holding the broker address plus this car's `apiname`/`apikey`.
        """
        data = await self.async_request("/user/getCarMqState", device_id=device_id)
        if not isinstance(data, dict):
            raise TeyesApiError("getCarMqState returned no state")
        return data

    async def async_get_cars(self) -> list[dict[str, Any]]:
        """List the cars on this account, newest binding first.

        `selectCar` is scoped by `uid`: with it the reply is just this
        account's cars, and each record carries the device id, a name, the
        model and the last known position — enough to set up without asking
        the user for anything.
        """
        data = await self.async_request("/user/selectCar")
        cars = data if isinstance(data, list) else [data] if data else []
        return [car for car in cars if isinstance(car, dict) and car.get("imei")]

    async def async_check_app_update(self, device_id: str) -> dict[str, Any]:
        """Whether the head unit has an app update waiting.

        Returns `hasUpdate` (0/1) and `latest_version_code`.
        """
        data = await self.async_request(
            "/user/checkCarAppUpdate", device_id=device_id
        )
        return data if isinstance(data, dict) else {}

    # ── trips ─────────────────────────────────────────────────────────────

    async def async_get_trips(
        self, device_id: str, *, page: int = 1, size: int = 20
    ) -> dict[str, Any]:
        """A page of the car's recorded trips (newest first).

        Returns the paginator `{total, per_page, current_page, last_page,
        data:[…]}`; each trip has `id`, `stime`, `etime`, `date`, `zlc`
        (distance) and a `file` of the recorded path.
        """
        data = await self.async_request(
            "/track/list", {"imei": device_id, "page": page, "pagesize": size},
            device_id=device_id,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_trip(self, trip_id: Any) -> dict[str, Any]:
        """One trip in full, including its `path` of points."""
        data = await self.async_request("/track/getData", {"id": trip_id})
        return data if isinstance(data, dict) else {}

    # ── tyre pressure ─────────────────────────────────────────────────────

    async def async_get_tpms(self, device_id: str) -> dict[str, Any]:
        """The four tyres' pressure and temperature.

        Each position (`leftTop`, `rightTop`, `leftBottom`, `rightBottom`)
        carries `press`, `temp`, `alert_flag`, `connect_state` and the unit
        codes. A value of `__` means that tyre has no sensor reporting.
        """
        data = await self.async_request(
            "/tmps/getData", {"imei": device_id}, device_id=device_id
        )
        return data if isinstance(data, dict) else {}

    # ── events / sentry ───────────────────────────────────────────────────

    async def async_get_events(
        self, device_id: str, *, page: int = 1, size: int = 20
    ) -> dict[str, Any]:
        """A page of sentry / alarm events for the car."""
        data = await self.async_request(
            "/user/sentryList", {"imei": device_id, "page": page, "pagesize": size},
            device_id=device_id,
        )
        return data if isinstance(data, dict) else {}

    async def async_get_lives(
        self, device_id: str, direction: str, *, app_version: Any = 0
    ) -> dict[str, Any]:
        """The live-stream play URLs for one of the car's cameras.

        Returns a dict with `hls_play_url`, `flv_play_url`, `rtmp_play_url` and
        `artc_play_url`; the URLs carry a time-limited `auth_key`, so they are
        re-fetched when they age. The car must be streaming (send the start
        command first) for the URLs to actually carry video.
        """
        data = await self.async_request(
            "/lives/getLives",
            {
                "imei": device_id,
                "direction": direction,
                "type": "live",
                "app_version": app_version,
            },
            device_id=device_id,
        )
        lives = data.get("live") if isinstance(data, dict) else None
        if isinstance(lives, list) and lives and isinstance(lives[0], dict):
            return lives[0]
        return {}

    async def async_get_photo(self, photo_id: Any) -> dict[str, Any]:
        """A captured photo's details (image link and where it was taken).

        The `id` comes from an event in `sentryList`; impact events leave a
        photo the car uploaded.
        """
        data = await self.async_request("/user/getPhotoData", {"id": photo_id})
        return data if isinstance(data, dict) else {}

    # ── geofences (electronic fence) ──────────────────────────────────────

    async def async_get_geofences(
        self, device_id: str, *, page: int = 1, size: int = 50
    ) -> dict[str, Any]:
        """List the car's geofences."""
        data = await self.async_request(
            "/user/efList", {"imei": device_id, "page": page, "pagesize": size},
            device_id=device_id,
        )
        return data if isinstance(data, dict) else {}

    async def async_set_geofence(
        self,
        device_id: str,
        *,
        scope_status: str = "hidden",
        scope_position: str | None = None,
        scope_range: float | None = None,
        region_status: str = "hidden",
        region_position: str | None = None,
    ) -> None:
        """Create, update or disable a geofence.

        A circular fence sets `scope_position` (`lat,lng`) with `scope_range`
        (metres) and `scope_status` != "hidden"; a polygon sets
        `region_position` with `region_status`. "hidden" turns a side off.
        """
        params: dict[str, Any] = {
            "imei": device_id,
            "ef_scope_status": scope_status,
            "ef_region_status": region_status,
        }
        if scope_position is not None:
            params["ef_scope_postion"] = scope_position
        if scope_range is not None:
            params["ef_scope_range"] = scope_range
        if region_position is not None:
            params["ef_region_postion"] = region_position
        await self.async_request("/user/efStatus", params, device_id=device_id)

    async def async_delete_geofences(self, device_id: str, ids: str) -> None:
        """Delete one or more geofences by comma-separated id."""
        await self.async_request(
            "/user/deleefList", {"imei": device_id, "no_ids": ids},
            device_id=device_id,
        )

    # ── navigation ────────────────────────────────────────────────────────

    async def async_send_address(
        self, device_id: str, address: str, map_app: str
    ) -> None:
        """Send a destination to the car, to open in the chosen map app.

        `map_app` is one of google, yandex, waze, gis. `address` is a place
        name or `lat,lng`. The head unit opens it when it is online.
        """
        await self.async_request(
            "/user/sendAddress",
            {"imei": device_id, "address": address, "pagename": map_app},
            device_id=device_id,
        )

    # ── SIM / mobile data ─────────────────────────────────────────────────

    async def async_set_stealth(self, car_id: Any, hidden: bool) -> None:
        """Hide or show the car (stealth mode)."""
        await self.async_request(
            "/user/changSmStatus",
            {"id": car_id, "sm_status": "hidden" if hidden else "normal"},
        )

    async def async_close_4g(self, device_id: str, client_id: str) -> None:
        """Ask the cloud to disable the car's mobile data."""
        await self.async_request(
            "/user/close4g",
            {"uid": self.uid, "imei": device_id, "clientid": client_id},
            device_id=device_id,
        )

    async def async_get_data_usage(self, iccid: str) -> dict[str, Any]:
        """The SIM's data-plan usage, from the plain data-plan host.

        Returns the raw `{success, data, errorMessage, payurl, payinfo}`; not
        every SIM is registered there, in which case `success` is "false".
        """
        boundary = "----teyes" + str(id(iccid))
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="iccid"\r\n\r\n'
            f"{iccid}\r\n--{boundary}--\r\n"
        ).encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            async with self._session.post(
                f"{DATA_API}/getDatamb", data=body, headers=headers, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                envelope = json.loads(await resp.text())
        except asyncio.TimeoutError as err:
            raise TeyesConnectionError("data-usage request timed out") from err
        except (aiohttp.ClientError, ValueError) as err:
            raise TeyesConnectionError(f"data-usage request failed: {err}") from err
        data = envelope.get("data") if isinstance(envelope, dict) else None
        return data if isinstance(data, dict) else {}


def _flatten(data: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect every scalar in a nested reply under its lowercased key."""
    out = {} if out is None else out
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                _flatten(value, out)
            elif value not in (None, ""):
                out.setdefault(str(key).lower().replace("_", ""), value)
    elif isinstance(data, list):
        for item in data:
            _flatten(item, out)
    return out


def _first(flat: dict[str, Any], names: tuple[str, ...]) -> Any:
    """Return the first of `names` present in a flattened reply."""
    for name in names:
        if name in flat:
            return flat[name]
    return None
