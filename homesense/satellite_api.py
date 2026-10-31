"""Separate HTTPS/ASGI voice satellite endpoint, without browser or HA credentials."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import ssl
import threading
import time
import uuid

from .services import MAX_BYTES, ROOT, ServiceError, validate_audio


class SatelliteAPI:
    def __init__(self, application, token: str, satellites=None):
        if len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("The satellite token must be a random token containing at least 32 characters.")
        self.satellites = satellites
        self.application = application
        self.token = token.encode("ascii")
        self.results = {}
        self.pending = set()
        self.last_request = -float("inf")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        async def reply(code, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            await send({"type": "http.response.start", "status": code, "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
            ]})
            await send({"type": "http.response.body", "body": body})

        headers = {}
        for key, value in scope["headers"]:
            if key in headers:
                return await reply(400, {"message": "Duplicate HTTP header."})
            headers[key] = value
        if scope.get("scheme") != "https":
            return await reply(403, {"message": "HTTPS is required."})
        if scope.get("path") != "/satellite/voice" or scope.get("method") != "POST":
            return await reply(404, {"message": "Not found."})
        context = None
        identity = "legacy"
        expected = self.token
        if self.satellites is not None:
            identity = headers.get(b"x-satellite-id", b"").decode("ascii", errors="replace")
            device = self.satellites.get(identity)
            expected = device["token"].encode("ascii") if device else b"invalid"
            if device:
                context = {"id": identity, "name": device["name"], "room": device["room"]}
        if b"origin" in headers or (self.satellites is not None and context is None) or not secrets.compare_digest(headers.get(b"authorization", b""), b"Bearer " + expected):
            return await reply(403, {"message": "Satellite authentication failed."})
        try:
            request_id = headers[b"x-request-id"].decode("ascii")
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
            timestamp = int(headers[b"x-request-timestamp"])
            length = int(headers[b"content-length"])
        except (KeyError, ValueError, UnicodeError):
            return await reply(400, {"message": "Invalid request ID, timestamp, or content length."})
        if abs(time.time() - timestamp) > 60:
            return await reply(400, {"message": "The request has expired. Check the device clocks."})
        if not 0 < length <= MAX_BYTES:
            return await reply(413, {"message": "The recording is too large or empty."})
        if headers.get(b"content-type") != b"audio/wav" or b"transfer-encoding" in headers:
            return await reply(415, {"message": "Send WAV audio with a known content length."})
        body = bytearray()
        read_deadline = time.monotonic() + 10
        try:
            while True:
                remaining = read_deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                event = await asyncio.wait_for(receive(), timeout=min(5, remaining))
                if event["type"] == "http.disconnect":
                    return
                body.extend(event.get("body", b""))
                if len(body) > length:
                    return await reply(413, {"message": "Content length exceeded."})
                if not event.get("more_body", False):
                    break
        except asyncio.TimeoutError:
            return await reply(408, {"message": "Recording upload timed out."})
        if len(body) != length:
            return await reply(400, {"message": "The recording was truncated."})
        try:
            validate_audio(bytes(body))
        except ServiceError as exc:
            return await reply(400, {"message": str(exc)})
        if not self.application.request_lock.acquire(blocking=False):
            return await reply(409, {"message": "A previous command is processing. Record a fresh command later."})
        worker_owns_lock = False
        try:
            now = time.monotonic()
            self.results = {k: v for k, v in self.results.items() if now - v[0] < 600}
            digest = hashlib.sha256(body).digest()
            cache_key = (identity, request_id)
            cached = self.results.get(cache_key)
            if cached:
                if cached[1] != digest:
                    return await reply(409, {"message": "The request ID was reused with different audio."})
                return await reply(cached[2], cached[3])
            last = self.last_request.get(identity, -float("inf")) if isinstance(self.last_request, dict) else self.last_request
            if now - last < 2:
                return await reply(429, {"message": "Requests arrived too quickly."})
            if not isinstance(self.last_request, dict): self.last_request = {}
            self.last_request[identity] = now
            def execute():
                # The worker owns the lock until it finishes, even if the client disconnects.
                try:
                    code = 502
                    result = {"message": "Processing failed. Check the device state before sending a new command."}
                    try:
                        result = self.application.voice(bytes(body), context=context) if context else self.application.voice(bytes(body))
                        code = 200
                    except ServiceError as exc:
                        result = {"message": str(exc), "stage": exc.stage}
                    except Exception:
                        pass
                    if len(self.results) >= 128:
                        self.results.pop(next(iter(self.results)))
                    self.results[cache_key] = (now, digest, code, result)
                    return code, result
                finally:
                    self.application.request_lock.release()

            task = asyncio.create_task(asyncio.to_thread(execute))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
            worker_owns_lock = True
            code, result = await asyncio.shield(task)
            return await reply(code, result)
        finally:
            if not worker_owns_lock:
                self.application.request_lock.release()



def start_satellite(application):
    """Start only when SATELLITE_ENABLED=true is explicitly configured."""
    import uvicorn

    registry_path = os.getenv("SATELLITE_REGISTRY_FILE", "").strip()
    satellites = load_satellites(ROOT / registry_path) if registry_path else None
    token_path = ROOT / os.getenv("SATELLITE_TOKEN_FILE", ".secrets/satellite-token.txt")
    token = token_path.read_text(encoding="ascii").strip() if satellites is None else "registry-mode-placeholder-token-000000"
    cert = ROOT / os.getenv("SATELLITE_TLS_CERT", ".secrets/server.crt")
    key = ROOT / os.getenv("SATELLITE_TLS_KEY", ".secrets/server.key")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    api = SatelliteAPI(application, token, satellites)
    config = uvicorn.Config(api, host=os.getenv("SATELLITE_BIND", "127.0.0.1"),
        port=int(os.getenv("SATELLITE_PORT", "8443")),
        ssl_certfile=str(cert), ssl_keyfile=str(key), ssl_version=ssl.PROTOCOL_TLS_SERVER,
        lifespan="off", proxy_headers=False, access_log=False,
        limit_concurrency=8, limit_max_requests=None, timeout_keep_alive=2)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="satellite-https", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise RuntimeError("The satellite HTTPS server did not start.")
        time.sleep(0.05)
    print("Satellite HTTPS endpoint started. Browser access remains localhost-only.")
    return server, thread


def load_satellites(path):
    """Load trusted locations and individual credentials; never trust client room headers."""
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not 1 <= len(records) <= 128:
        raise ValueError("Satellite registry must contain 1-128 devices.")
    result = {}
    tokens = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"id", "name", "room", "token_file"}:
            raise ValueError("Each satellite requires id, name, room, and token_file.")
        identity = record["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", identity) or identity in result:
            raise ValueError("Satellite IDs must be unique lowercase identifiers.")
        for field in ("name", "room"):
            value = record[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
                raise ValueError("Satellite names and rooms require 1-80 printable characters.")
        token = (ROOT / record["token_file"]).read_text(encoding="ascii").strip()
        if len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token) or token in tokens:
            raise ValueError("Each satellite requires a distinct strong token.")
        tokens.add(token)
        result[identity] = {"name": record["name"], "room": record["room"], "token": token}
    return result
