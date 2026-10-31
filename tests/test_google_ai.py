import json
import os
import unittest
from unittest.mock import patch

from homesense.google_ai import GoogleAIInterpreter
from homesense.services import ServiceError, SimulatedGateway
from homesense.application import Application
from homesense.rules import RuleError, Rules
from test_homesense import wav, DEVICES, decision


def response(value=None, reason="STOP"):
    value = decision() if value is None else value
    return {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": json.dumps(value)}]}}]}


class GoogleAITests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.ai = GoogleAIInterpreter()
        self.app = Application(Rules(DEVICES), self.ai, SimulatedGateway())

    def test_room_context_survives_transcription_and_reaches_prompt(self):
        context = {"id": "kitchen-pi", "name": "Kitchen", "room": "Kitchen"}
        devices = [{**d, "room": "Kitchen"} for d in DEVICES]
        app = Application(Rules(devices), self.ai, SimulatedGateway())
        speech = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Valot"}]}}]}
        with patch("homesense.google_ai.request_json", side_effect=[speech, response()]) as request:
            result = app.voice(wav(), context=context)
            prompt = request.call_args.kwargs["payload"]["systemInstruction"]["parts"][0]["text"]
            self.assertIn('"room": "Kitchen"', prompt)
            self.assertIn("explicitly named room or device takes precedence", prompt)
            self.assertIn("multiple local devices match, clarify", prompt)
            self.assertEqual(result["satellite"], context)

    def test_missing_key_blocks_both_inputs_without_network(self):
        os.environ.pop("GEMINI_API_KEY")
        app = Application(Rules(DEVICES), GoogleAIInterpreter(), SimulatedGateway())
        with patch("homesense.google_ai.request_json") as request:
            for method, value in [(app.voice, wav()), (app.text, "Valot keittiöön")]:
                with self.assertRaises(ServiceError): method(value)
            request.assert_not_called()

    def test_invalid_config(self):
        for key, value in [("GEMINI_API_KEY", "abc\r\nInjected: x"), ("GEMINI_API_KEY", "ää"),
                           ("GEMMA_MODEL", "../evil"), ("GEMINI_STT_MODEL", "gemini-test?key=bad"),
                           ("GEMINI_TIMEOUT", "0"), ("GEMINI_TIMEOUT", "301"), ("GEMINI_TIMEOUT", "nan")]:
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                GoogleAIInterpreter()

    def test_audio_transcription_then_gemma_interpretation(self):
        with patch("homesense.google_ai.request_json", side_effect=[{"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Sytytä keittiön valo"}]}}]}, response()]) as request:
            result = self.app.voice(wav())
            self.assertEqual(result["state"], "on")
            self.assertIn("Simulation", result["spoken_feedback"])
            self.assertEqual(request.call_count, 2)
            speech_payload = request.call_args_list[0].kwargs["payload"]
            self.assertEqual(speech_payload["contents"][0]["parts"][1]["inlineData"]["mimeType"], "audio/wav")
            self.assertIn("gemini-", request.call_args_list[0].args[0])
            self.assertIn("gemma-4-", request.call_args_list[1].args[0])
            payload = request.call_args.kwargs["payload"]
            self.assertIn("light.keittio", payload["systemInstruction"]["parts"][0]["text"])
            self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingLevel"], "minimal")
            self.assertNotIn("test-key", request.call_args.args[0])

    def test_free_form_text_is_sent_to_gemini(self):
        text = "Laitahan keittiöön valot"
        with patch("homesense.google_ai.request_json", return_value=response()) as request:
            result = self.app.text(text)
            self.assertEqual(result["transcript"], text)
            self.assertEqual(result["status"], "simulated")
            self.assertEqual(request.call_args.kwargs["payload"]["contents"][0]["parts"], [{"text": text}])

    def test_silent_transcription_does_not_call_gemma(self):
        result = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": ""}]}}]}
        with patch("homesense.google_ai.request_json", return_value=result) as request:
            with self.assertRaises(ServiceError): self.app.voice(wav())
            self.assertEqual(request.call_count, 1)
            self.assertEqual(self.app.gateway.states, {})

    def test_text_calls_only_gemma(self):
        with patch("homesense.google_ai.request_json", return_value=response()) as request:
            self.app.text("Laitahan keittiöön valot")
            self.assertEqual(request.call_count, 1)
            self.assertIn("/gemma-4-26b-a4b-it:", request.call_args.args[0])

    def test_invalid_raw_intent_transcript_is_rejected_by_validator(self):
        for value in [decision(text=""), decision(text="x" * 301)]:
            with self.assertRaises(RuleError): self.app.rules.validate_intent(value)

    def test_ambiguous_command_asks_without_execution(self):
        result = decision("Laita valo päälle", "clarify", "", "", "Which room do you mean? Repeat the complete command.")
        with patch("homesense.google_ai.request_json", return_value=response(result)):
            value = self.app.text("Laita valo päälle")
            self.assertEqual(value["status"], "clarification")
            self.assertEqual(value["spoken_feedback"], result["message"])
            self.assertEqual(self.app.gateway.states, {})

    def test_negation_rejected_without_execution(self):
        result = decision("Älä sytytä valoa", "reject", "", "", "The light will not be switched on.")
        with patch("homesense.google_ai.request_json", return_value=response(result)):
            self.assertEqual(self.app.text("Sytytä keittiön valo")["status"], "rejected")
            self.assertEqual(self.app.gateway.states, {})

    def test_invalid_audio_never_leaves_machine(self):
        with patch("homesense.google_ai.request_json") as request:
            with self.assertRaises(ServiceError): self.app.voice(b"bad")
            request.assert_not_called()

    def test_unsafe_intents_rejected_independently_of_model(self):
        values = [decision(device="lock.door"), decision(action="toggle"), decision(status="other"),
                  decision(status="clarify", message="Mikä valo?"), decision(status="reject", message="Ei"),
                  {**decision(), "extra": "injection"},
                  {**decision(), "action": ["turn_on", "turn_off"]}, decision(message="x" * 241)]
        for value in values:
            with self.subTest(value=value), patch("homesense.google_ai.request_json", return_value=response(value)):
                with self.assertRaises(ServiceError): self.app.text("Sytytä keittiön valo")
                self.assertEqual(self.app.gateway.states, {})

    def test_blocked_malformed_or_truncated_api_response(self):
        for value in [{}, {"candidates": None}, response(reason="MAX_TOKENS"), response(reason="SAFETY"),
                      {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not json"}]}}]}]:
            with patch("homesense.google_ai.request_json", return_value=value), self.assertRaises(ServiceError):
                self.app.text("Sytytä keittiön valo")

    def test_thoughts_not_used_as_actions(self):
        value = response(decision("Älä sytytä valoa", "reject", "", "", "The light will not be switched on."))
        value["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": json.dumps(decision())})
        with patch("homesense.google_ai.request_json", return_value=value):
            self.assertEqual(self.app.text("Sytytä keittiön valo")["status"], "rejected")

    def test_api_error_no_retry_or_device_action(self):
        with patch("homesense.google_ai.request_json", side_effect=ServiceError("HTTP 429")) as request:
            with self.assertRaises(ServiceError): self.app.text("Sytytä valo")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(self.app.gateway.states, {})

    def test_transient_500_retried_before_single_device_execution(self):
        with patch("homesense.google_ai.request_json", side_effect=[ServiceError("HTTP 500", http_status=500), response()]) as request, patch("homesense.google_ai.time.sleep") as sleep, patch.object(self.app.gateway, "execute", wraps=self.app.gateway.execute) as execute:
            self.assertEqual(self.app.text("Laitahan keittiöön valot")["status"], "simulated")
            self.assertEqual(request.call_count, 2)
            self.assertEqual(execute.call_count, 1)
            sleep.assert_called_once()

    def test_exhausted_500_never_executes_device(self):
        with patch("homesense.google_ai.request_json", side_effect=ServiceError("HTTP 500", http_status=500)) as request, patch("homesense.google_ai.time.sleep"), patch.object(self.app.gateway, "execute") as execute:
            with self.assertRaises(ServiceError) as error:
                self.app.text("Laitahan keittiöön valot")
            self.assertEqual(request.call_count, 3)
            self.assertIn("before device control", str(error.exception))
            execute.assert_not_called()

    def test_client_errors_are_not_retried(self):
        for status in [400, 401, 403, 429]:
            with self.subTest(status=status), patch("homesense.google_ai.request_json", side_effect=ServiceError("HTTP error", http_status=status)) as request:
                with self.assertRaises(ServiceError): self.app.text("Valot päälle")
                self.assertEqual(request.call_count, 1)

    def test_spoken_feedback_does_not_claim_uncertain_state(self):
        with patch("homesense.google_ai.request_json", return_value=response()), patch.object(self.app.gateway, "execute", return_value={"status": "unconfirmed", "message": "No confirmation"}):
            self.assertIn("could not be confirmed", self.app.text("Valot päälle")["spoken_feedback"])
