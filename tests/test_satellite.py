import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import ssl
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from homesense.application import Application
from homesense.rules import Rules
from homesense.satellite_api import SatelliteAPI
from homesense.services import SimulatedGateway
from satellite.audio import Segment, RATE, FRAME
from satellite.client import Client
from test_homesense import DEVICES, StubInterpreter, wav

TOKEN = "test-satellite-token-0123456789-abcdefghijk"


class SatelliteAPITests(unittest.TestCase):
    def setUp(self):
        self.app = Application(Rules(DEVICES), StubInterpreter(), SimulatedGateway())
        self.api = SatelliteAPI(self.app, TOKEN)

    def request(self, body=None, *, request_id=None, headers=None, scheme="https", path="/satellite/voice"):
        body = wav() if body is None else body
        values = {b"authorization": ("Bearer " + TOKEN).encode(), b"content-type": b"audio/wav",
                  b"content-length": str(len(body)).encode(), b"x-request-id": (request_id or str(uuid.uuid4())).encode(),
                  b"x-request-timestamp": str(int(time.time())).encode()}
        values.update(headers or {})
        output = []
        async def run():
            async def receive(): return {"type": "http.request", "body": body, "more_body": False}
            async def send(event): output.append(event)
            await self.api({"type": "http", "scheme": scheme, "method": "POST", "path": path,
                            "headers": list(values.items())}, receive, send)
        asyncio.run(run())
        return output[0]["status"], json.loads(output[1]["body"])


    def test_registry_identity_and_location_are_trusted(self):
        self.api = SatelliteAPI(self.app, TOKEN, {
            "kitchen-pi": {"name": "Kitchen", "room": "Kitchen", "token": TOKEN},
            "office-pi": {"name": "Office", "room": "Office", "token": TOKEN + "2"}})
        self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request(headers={b"x-satellite-id": b"office-pi"})[0], 403)
        captured = []
        original = self.app.voice
        def voice(audio, context=None):
            captured.append(context)
            return original(audio)
        with patch.object(self.app, "voice", side_effect=voice):
            identity = str(uuid.uuid4())
            self.assertEqual(self.request(request_id=identity, headers={b"x-satellite-id": b"kitchen-pi", b"x-room": b"Office"})[0], 200)
            self.assertEqual(captured[0]["room"], "Kitchen")
            self.assertEqual(self.request(request_id=identity, headers={b"x-satellite-id": b"office-pi", b"authorization": ("Bearer " + TOKEN + "2").encode()})[0], 200)
            self.assertEqual(captured[1]["room"], "Office")

    def test_authenticated_audio_reaches_simulator(self):
        code, result = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["state"], "on")
        self.assertIn("Simulation", result["spoken_feedback"])

    def test_wrong_token_and_browser_origin_rejected(self):
        for headers in [{b"authorization": b"Bearer wrong"}, {b"origin": b"https://evil.example"}]:
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(self.app.gateway.states, {})

    def test_https_required(self):
        self.assertEqual(self.request(scheme="http")[0], 403)

    def test_no_other_remote_endpoints(self):
        for path in ["/", "/api/status", "/api/text", "/.env"]:
            self.assertEqual(self.request(path=path)[0], 404)

    def test_audio_and_header_validation(self):
        for headers, body, expected in [({}, b"bad", 400), ({b"content-length": b"999999"}, wav(), 413),
                 ({b"content-type": b"text/plain"}, wav(), 415),
                 ({b"x-request-timestamp": b"1"}, wav(), 400),
                 ({b"x-request-id": b"not-uuid"}, wav(), 400)]:
            self.assertEqual(self.request(body, headers=headers)[0], expected)

    def test_duplicate_id_does_not_execute_again(self):
        identity = str(uuid.uuid4())
        with patch.object(self.app.gateway, "execute", wraps=self.app.gateway.execute) as execute:
            self.assertEqual(self.request(request_id=identity)[0], 200)
            self.assertEqual(self.request(request_id=identity)[0], 200)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(self.request(wav(0.7), request_id=identity)[0], 409)

    def test_shared_busy_lock_blocks_remote_command(self):
        self.app.request_lock.acquire()
        try: self.assertEqual(self.request()[0], 409)
        finally: self.app.request_lock.release()

    def test_rate_limit(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request()[0], 429)

    def test_weak_device_token_rejected(self):
        with self.assertRaises(ValueError): SatelliteAPI(self.app, "short")

    def test_cancelled_client_does_not_release_active_command_lock(self):
        started, finish = threading.Event(), threading.Event()
        original = self.app.voice
        def slow(audio):
            started.set(); finish.wait(3)
            return original(audio)
        async def run():
            body = wav()
            headers = [(b"authorization", ("Bearer " + TOKEN).encode()), (b"content-type", b"audio/wav"),
                       (b"content-length", str(len(body)).encode()), (b"x-request-id", str(uuid.uuid4()).encode()),
                       (b"x-request-timestamp", str(int(time.time())).encode())]
            async def receive(): return {"type": "http.request", "body": body}
            async def send(event): pass
            task = asyncio.create_task(self.api({"type": "http", "scheme": "https", "method": "POST",
                         "path": "/satellite/voice", "headers": headers}, receive, send))
            try:
                for _ in range(100):
                    if started.is_set(): break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(self.app.request_lock.locked())
            finally:
                finish.set()
                for _ in range(100):
                    if not self.app.request_lock.locked(): break
                    await asyncio.sleep(0.01)
            self.assertFalse(self.app.request_lock.locked())
        with patch.object(self.app, "voice", side_effect=slow): asyncio.run(run())


class AudioTests(unittest.TestCase):
    def test_silence_is_not_sent(self):
        segment = Segment()
        for _ in range(38): done = segment.feed(b"\0" * FRAME * 2)
        self.assertTrue(done)
        self.assertIsNone(segment.wav())

    def test_speech_ends_after_silence_and_is_valid_wav(self):
        segment = Segment()
        self.assertFalse(segment.feed(struct.pack("<h", 1000) * FRAME))
        for _ in range(13): done = segment.feed(b"\0" * FRAME * 2)
        self.assertTrue(done)
        from homesense.services import validate_audio
        validate_audio(segment.wav())

    def test_recording_never_exceeds_eight_seconds(self):
        segment = Segment()
        segment.feed(struct.pack("<h", 1000) * RATE * 12)
        self.assertEqual(len(segment.data), RATE * 8 * 2)


class SatelliteClientTLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try: import cryptography
        except ImportError: raise unittest.SkipTest("The TLS test requires cryptography")
        from tools.create_satellite_credentials import create
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        create("127.0.0.1", cls.root)
        token = (cls.root / "satellite-token.txt").read_text().strip()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                result = {"spoken_feedback": "Simulation complete."}
                status = 200 if self.headers.get("Authorization") == "Bearer " + token else 403
                data = json.dumps(result).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args): pass
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.root / "server.crt"), str(cls.root / "server.key"))
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def config(self, hostname="127.0.0.1"):
        return patch.dict(os.environ, {"HOMESENSE_SERVER_URL": f"https://{hostname}:{self.server.server_port}",
            "HOMESENSE_CA_FILE": str(self.root / "ca.crt"),
            "HOMESENSE_TOKEN_FILE": str(self.root / "satellite-token.txt")}, clear=True)

    def test_real_https_with_private_ca(self):
        with self.config(): self.assertEqual(Client().send(wav())["spoken_feedback"], "Simulation complete.")

    def test_certificate_hostname_is_verified(self):
        with self.config("localhost"), self.assertRaises(RuntimeError): Client().send(wav())

    def test_credentials_never_overwritten(self):
        from tools.create_satellite_credentials import create
        before = (self.root / "server.key").read_bytes()
        with self.assertRaises(ValueError): create("127.0.0.1", self.root)
        self.assertEqual(before, (self.root / "server.key").read_bytes())


class UvicornIntegrationTests(unittest.TestCase):
    def test_actual_https_endpoint_to_application(self):
        try:
            import uvicorn
            import cryptography
        except ImportError:
            self.skipTest("Install server-requirements.txt to run the HTTPS integration test")
        from tools.create_satellite_credentials import create
        from homesense.satellite_api import start_satellite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create("127.0.0.1", root)
            app = Application(Rules(DEVICES), StubInterpreter(), SimulatedGateway())
            config = {"SATELLITE_BIND": "127.0.0.1", "SATELLITE_PORT": "0",
                      "SATELLITE_TOKEN_FILE": str(root / "satellite-token.txt"),
                      "SATELLITE_TLS_CERT": str(root / "server.crt"), "SATELLITE_TLS_KEY": str(root / "server.key")}
            with patch.dict(os.environ, config, clear=True): server, thread = start_satellite(app)
            try:
                port = server.servers[0].sockets[0].getsockname()[1]
                with patch.dict(os.environ, {"HOMESENSE_SERVER_URL": f"https://127.0.0.1:{port}",
                        "HOMESENSE_CA_FILE": str(root / "ca.crt"), "HOMESENSE_TOKEN_FILE": str(root / "satellite-token.txt")}, clear=True):
                    result = Client().send(wav())
                self.assertEqual(result["status"], "simulated")
                self.assertEqual(app.gateway.states["light.keittio"], "on")
            finally:
                server.should_exit = True; thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
