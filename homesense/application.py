"""Gemma 4 interprets, validation checks, and the device gateway executes."""
from typing import Protocol
import time
import threading

from .rules import Command, RuleError, Rules
from .services import ServiceError


class Interpreter(Protocol):
    def interpret_text(self, text: str, devices: list[dict], context=None) -> dict: ...
    def interpret_audio(self, audio: bytes, devices: list[dict], context=None) -> dict: ...


class DeviceGateway(Protocol):
    dry_run: bool
    def execute(self, command: Command) -> dict: ...


class Application:
    def __init__(self, rules: Rules, interpreter: Interpreter, gateway: DeviceGateway):
        self.rules = rules
        self.interpreter = interpreter
        self.gateway = gateway
        self.request_lock = threading.Lock()

    def voice(self, audio: bytes, context=None) -> dict:
        return self._handle("voice", audio, context)

    def text(self, text: str) -> dict:
        if not isinstance(text, str) or not 1 <= len(text) <= 300:
            raise ServiceError("Enter a command containing 1–300 characters.", stage="speech")
        return self._handle("text", text)

    def _handle(self, source, value, context=None):
        started = time.monotonic()
        try:
            method = self.interpreter.interpret_audio if source == "voice" else self.interpreter.interpret_text
            decision = method(value, self.rules.devices, context=context) if context else method(value, self.rules.devices)
        except ServiceError as exc:
            raise ServiceError(str(exc), stage="speech") from exc
        elapsed = round(time.monotonic() - started, 2)
        try:
            command = self.rules.validate_intent(decision)
        except RuleError as exc:
            raise ServiceError(str(exc), stage="rules") from exc
        base = {"satellite": context, "source": source, "transcript": decision["transcript"], "interpretation_seconds": elapsed}
        if command is None:
            status = "clarification" if decision["status"] == "clarify" else "rejected"
            return {**base, "status": status, "message": decision["message"], "spoken_feedback": decision["message"]}
        try:
            result = self.gateway.execute(command)
        except ServiceError as exc:
            raise ServiceError(str(exc), stage="device", transcript=decision["transcript"]) from exc
        target = "on" if command.service == "turn_on" else "off"
        if result["status"] == "simulated":
            spoken = f"Simulation: {command.name} {target}."
        elif result["status"] == "confirmed":
            spoken = f"Home Assistant reports: {command.name} {target}."
        else:
            spoken = f"The command was sent, but the state of {command.name} could not be confirmed."
        return {**base, **result, "spoken_feedback": spoken}
