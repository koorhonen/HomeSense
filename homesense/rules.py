"""Device catalog and independent validation of AI-proposed actions."""
import re
import unicodedata
from dataclasses import dataclass


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return re.sub(r"\s+", " ", text.rstrip(".!?"))


@dataclass(frozen=True)
class Command:
    entity_id: str
    name: str
    service: str

    @property
    def domain(self) -> str:
        return self.entity_id.split(".")[0]


class RuleError(ValueError):
    pass


class Rules:
    def __init__(self, devices: list[dict]):
        self.devices = devices
        self.aliases = {}
        seen = set()
        for device in devices:
            room = device.get("room", "")
            if not isinstance(room, str) or len(room) > 80 or any(ord(c) < 32 for c in room):
                raise ValueError("Device room must contain at most 80 printable characters.")
            entity = device["entity_id"]
            if not re.fullmatch(r"(?:light|switch)\.[a-z0-9_]+", entity):
                raise ValueError("Only light and switch entities are allowed.")
            if entity in seen:
                raise ValueError("Duplicate device entity_id.")
            seen.add(entity)
            for alias in [device["name"], *device["aliases"]]:
                key = normalize(alias)
                if not key:
                    raise ValueError("Device names must not be empty.")
                if key in self.aliases and self.aliases[key]["entity_id"] != entity:
                    raise ValueError("The same device name refers to multiple devices.")
                self.aliases[key] = device

    def validate_intent(self, decision: dict) -> Command | None:
        required = {"transcript", "status", "device_id", "action", "message"}
        if not isinstance(decision, dict) or set(decision) != required:
            raise RuleError("The Gemma 4 intent structure was invalid.")
        if any(not isinstance(value, str) for value in decision.values()):
            raise RuleError("The Gemma 4 intent fields were invalid.")
        if len(decision["transcript"]) > 300 or len(decision["message"]) > 240:
            raise RuleError("The Gemma 4 intent was too long.")
        if decision["status"] in {"clarify", "reject"}:
            if decision["device_id"] or decision["action"] or not decision["message"].strip():
                raise RuleError("An ambiguous intent contained a device command.")
            return None
        if decision["status"] != "execute" or not decision["transcript"].strip():
            raise RuleError("The Gemma 4 intent status was invalid.")
        device = next((d for d in self.devices if d["entity_id"] == decision["device_id"]), None)
        if device is None or decision["action"] not in {"turn_on", "turn_off"}:
            raise RuleError("The proposed device or action is not allowed.")
        return Command(device["entity_id"], device["name"], decision["action"])
