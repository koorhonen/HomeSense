import http.client
import io
import json
import os
from pathlib import Path
import struct
import threading
import unittest
from unittest.mock import patch
import wave

from homesense.application import Application
from homesense.rules import RuleError, Rules
from homesense.services import (HomeAssistantGateway, ServiceError,
                              SimulatedGateway, create_gateway, validate_audio, MAX_BYTES)
import homesense.server as web

DEVICES = json.loads((Path(__file__).resolve().parent.parent / "devices.json").read_text(encoding="utf-8"))


def wav(seconds=0.5, rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<h", 1000) * int(rate * seconds) * channels)
    return buffer.getvalue()


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules(DEVICES)




    def test_ambiguous_alias_configuration_rejected(self):
        devices = [dict(d) for d in DEVICES]
        devices[1]["aliases"] = ["keittiön valo"]
        with self.assertRaises(ValueError):
            Rules(devices)

    def test_unsupported_device_rejected(self):
        with self.assertRaises(ValueError):
            Rules([{**DEVICES[0], "entity_id": "lock.front_door"}])


class ServicesTests(unittest.TestCase):
    def test_audio_validation(self):
        validate_audio(wav())
        for data in [b"not a wav", wav(0.1), wav(8.1), wav(rate=8000), wav(channels=2), wav()[:-100], b"x" * (MAX_BYTES + 1)]:
            with self.subTest(length=len(data)), self.assertRaises(ServiceError):
                validate_audio(data)

    def test_simulation_never_calls_network_and_preserves_state(self):
        app = Application(Rules(DEVICES), StubInterpreter(), SimulatedGateway())
        with patch("homesense.services.request_json", side_effect=AssertionError("network")):
            result = app.text("Sytytä keittiön valo")
            self.assertEqual(result["status"], "simulated")
            self.assertEqual(app.gateway.states["light.keittio"], "on")
            app.text("Sammuta keittiön valo")
            self.assertEqual(app.gateway.states["light.keittio"], "off")

    def test_invalid_command_does_not_execute(self):
        app = Application(Rules(DEVICES), StubInterpreter(), SimulatedGateway())
        with patch.object(app.gateway, "execute", side_effect=AssertionError("execute")):
            self.assertEqual(app.text("Älä sytytä keittiön valo")["status"], "rejected")





    def test_real_mode_requires_token(self):
        with patch.dict(os.environ, {"HA_DRY_RUN": "false"}, clear=True):
            with self.assertRaises(ValueError):
                create_gateway()

    def test_invalid_mode_rejected(self):
        with patch.dict(os.environ, {"HA_DRY_RUN": "typo"}, clear=True):
            with self.assertRaises(ValueError):
                create_gateway()

    def test_home_assistant_post_and_reported_state(self):
        with patch.dict(os.environ, {"HA_URL": "http://127.0.0.1:8123", "HA_TOKEN": "test-only"}, clear=True):
            gateway = HomeAssistantGateway()
        with patch("homesense.services.request_json", side_effect=[[], {"state": "on"}]) as request:
            result = gateway.execute(Rules(DEVICES).validate_intent(decision()))
            self.assertEqual(result["status"], "confirmed")
            self.assertTrue(request.call_args_list[0].args[0].endswith("/api/services/light/turn_on"))
            self.assertEqual(request.call_args_list[0].kwargs["payload"], {"entity_id": "light.keittio"})

    def test_no_automatic_retry_on_uncertain_post(self):
        with patch.dict(os.environ, {"HA_TOKEN": "test-only"}, clear=True):
            gateway = HomeAssistantGateway()
        with patch("homesense.services.request_json", side_effect=ServiceError("timeout")) as request:
            with self.assertRaises(ServiceError):
                gateway.execute(Rules(DEVICES).validate_intent(decision()))
            self.assertEqual(request.call_count, 1)

    def test_unconfirmed_state_is_not_success(self):
        with patch.dict(os.environ, {"HA_TOKEN": "test-only"}, clear=True):
            gateway = HomeAssistantGateway()
        with patch("homesense.services.request_json", side_effect=[[], ServiceError("timeout")]):
            self.assertEqual(gateway.execute(Rules(DEVICES).validate_intent(decision()))["status"], "unconfirmed")


def decision(text="Sytytä keittiön valo", status="execute", device="light.keittio", action="turn_on", message=""):
    return {"transcript": text, "status": status, "device_id": device, "action": action, "message": message}


class StubInterpreter:
    def interpret_audio(self, data, devices):
        validate_audio(data)
        return decision()

    def interpret_text(self, text, devices):
        if text.startswith("Älä") or "<" in text:
            return decision(text, "reject", "", "", "The command will not be executed.")
        return decision(text, action="turn_off" if text.startswith("Sammuta") else "turn_on")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Application(Rules(DEVICES), StubInterpreter(), SimulatedGateway())
        cls.server = web.LocalServer(("127.0.0.1", 0), web.make_handler(cls.application))
        cls.port = cls.server.server_port
        cls.previous_port = web.PORT
        web.PORT = cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        web.PORT = cls.previous_port

    def request(self, method="GET", path="/api/status", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def auth(self):
        _, _, body = self.request()
        return {"X-HomeSense-Token": json.loads(body)["csrf_token"], "Content-Type": "application/json"}

    def test_status_hides_secrets_and_has_headers(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["dry_run"])
        self.assertNotIn("HA_TOKEN", body.decode())
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_text_simulation_through_http(self):
        status, _, body = self.request("POST", "/api/text", json.dumps({"text": "Sytytä keittiön valo"}).encode(), self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "on")

    def test_voice_pipeline_with_test_double(self):
        headers = self.auth(); headers["Content-Type"] = "audio/wav"
        status, _, body = self.request("POST", "/api/voice", wav(), headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["source"], "voice")






    def test_removed_configuration_endpoint_is_unavailable(self):
        self.assertEqual(self.request("POST", "/api/speech", b'{}', self.auth())[0], 404)

    def test_status_only_exposes_gemini_readiness(self):
        speech = json.loads(self.request()[2])["speech"]
        self.assertEqual(speech, {"label": "Gemma 4 (Google API)", "ready": False})

    def test_missing_csrf_rejected(self):
        self.assertEqual(self.request("POST", "/api/text", b'{}', {"Content-Type": "application/json"})[0], 403)

    def test_wrong_host_rejected(self):
        self.assertEqual(self.request(headers={"Host": "evil.example"})[0], 403)

    def test_cross_origin_rejected_even_with_token(self):
        headers = self.auth(); headers["Origin"] = "https://evil.example"
        self.assertEqual(self.request("POST", "/api/text", b'{}', headers)[0], 403)

    def test_oversize_payload_rejected(self):
        self.assertEqual(self.request("POST", "/api/text", b"x" * 2049, self.auth())[0], 413)

    def test_wrong_media_type_rejected(self):
        headers = self.auth(); headers["Content-Type"] = "text/plain"
        self.assertEqual(self.request("POST", "/api/text", b'{}', headers)[0], 415)

    def test_invalid_json_and_fields(self):
        for body in [b"{", b"[]", b'{"text":1}', b'{"text":""}']:
            self.assertEqual(self.request("POST", "/api/text", body, self.auth())[0], 400)

    def test_no_arbitrary_static_file_access(self):
        for path in ["/.env", "/../devices.json", "/static/../../.env", "/devices.json"]:
            self.assertEqual(self.request(path=path)[0], 404)

    def test_script_is_display_data_and_not_executed(self):
        status, _, body = self.request("POST", "/api/text", json.dumps({"text": "<script>alert(1)</script>"}), self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
