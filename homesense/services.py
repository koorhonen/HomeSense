import io
import json
import os
import socket
import ssl
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

from .rules import Command

ROOT = Path(__file__).resolve().parent.parent
MAX_SECONDS = 8
MAX_BYTES = 16_000 * 2 * MAX_SECONDS + 4096


class ServiceError(Exception):
    def __init__(self, message: str, stage: str = "", transcript: str = "", *, http_status: int | None = None):
        super().__init__(message)
        self.stage = stage
        self.transcript = transcript
        self.http_status = http_status


def load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def validate_audio(data: bytes):
    if len(data) > MAX_BYTES:
        raise ServiceError("The recording is too large.")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            frames = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                raise ServiceError("Audio must be mono, 16 kHz, 16-bit WAV.")
            if not 3200 <= frames <= 16000 * MAX_SECONDS:
                raise ServiceError("Record between 0.2 and 8 seconds of speech.")
            if len(audio.readframes(frames)) != frames * 2:
                raise ServiceError("The recording is truncated.")
    except (wave.Error, EOFError) as exc:
        raise ServiceError("Invalid WAV recording.") from exc


def request_json(url, *, payload=None, token="", api_key="", timeout=15):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["x-goog-api-key"] = api_key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        # Do not route service requests through environment-configured HTTP proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            data = response.read(1_048_577)
            if len(data) > 1_048_576:
                raise ServiceError("The server response is too large.")
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise ServiceError(f"The server returned HTTP {status}.", http_status=status) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        if isinstance(reason, PermissionError):
            raise ServiceError("Server network access is blocked. Start HomeSense using start.cmd from a normal Windows session.") from None
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise ServiceError("TLS verification failed. Check the system clock and trusted certificates.") from None
        if isinstance(reason, socket.gaierror):
            raise ServiceError("Server name resolution failed. Check the internet connection and DNS settings.") from None
        raise ServiceError("The server could not be reached or the request timed out.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ServiceError("The server did not return valid JSON.") from exc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None




class HomeAssistantGateway:
    def __init__(self):
        self.dry_run = False
        self.ha_url = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/")
        self.token = os.getenv("HA_TOKEN", "")
        parsed = urllib.parse.urlsplit(self.ha_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not self.token):
            raise ValueError("Set valid HA_URL and HA_TOKEN values before enabling real device control.")

    def execute(self, command: Command) -> dict:
        result = {"entity_id": command.entity_id, "service": command.service, "name": command.name}
        try:
            request_json(
                f"{self.ha_url}/api/services/{command.domain}/{command.service}",
                payload={"entity_id": command.entity_id}, token=self.token,
            )
        except ServiceError as exc:
            # The action may have executed even if its response was lost.
            raise ServiceError("The send result could not be confirmed. Check the device state; the command will not be retried automatically.") from exc
        expected = "on" if command.service == "turn_on" else "off"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = request_json(f"{self.ha_url}/api/states/{command.entity_id}", token=self.token, timeout=2)
                if isinstance(state, dict) and state.get("state") == expected:
                    return {**result, "state": expected, "status": "confirmed", "message": f"Home Assistant reports state {expected}."}
            except ServiceError:
                break
            time.sleep(0.3)
        return {**result, "status": "unconfirmed", "message": "The command was sent, but the target state could not be confirmed."}


class SimulatedGateway:
    dry_run = True

    def __init__(self):
        self.states = {}

    def execute(self, command: Command) -> dict:
        state = "on" if command.service == "turn_on" else "off"
        self.states[command.entity_id] = state
        return {"entity_id": command.entity_id, "name": command.name,
                "service": command.service, "state": state, "status": "simulated",
                "message": "Simulated state updated. No command was sent to Home Assistant."}


def create_gateway():
    mode = os.getenv("HA_DRY_RUN", "true").lower()
    if mode not in {"true", "false"}:
        raise ValueError("HA_DRY_RUN must be true or false.")
    return SimulatedGateway() if mode == "true" else HomeAssistantGateway()
