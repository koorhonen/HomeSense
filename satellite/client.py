"""Wake phrase -> short recording -> HTTPS -> spoken feedback on the Pi."""
import argparse
import json
import math
import os
import re
from pathlib import Path
import ssl
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from homesense.services import NoRedirect, load_env, validate_audio
from .audio import FRAME, RATE, Segment


class Client:
    def __init__(self):
        self.satellite_id = os.getenv("HOMESENSE_SATELLITE_ID", "").strip()
        if self.satellite_id and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.satellite_id):
            raise ValueError("Invalid HOMESENSE_SATELLITE_ID.")
        self.url = os.environ["HOMESENSE_SERVER_URL"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("Set an HTTPS server address without a path, for example https://192.168.1.100:8443")
        self.token = Path(os.environ["HOMESENSE_TOKEN_FILE"]).read_text(encoding="ascii").strip()
        if len(self.token) < 32 or any(not 33 <= ord(c) <= 126 for c in self.token):
            raise ValueError("Invalid satellite token.")
        self.timeout = float(os.getenv("REQUEST_TIMEOUT", "145"))
        if not math.isfinite(self.timeout) or not 5 <= self.timeout <= 610:
            raise ValueError("REQUEST_TIMEOUT must be between 5 and 610 seconds.")
        context = ssl.create_default_context(cafile=os.environ["HOMESENSE_CA_FILE"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def send(self, audio):
        validate_audio(audio)
        request = urllib.request.Request(self.url + "/satellite/voice", data=audio, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "audio/wav",
            "X-Satellite-Id": self.satellite_id, "X-Request-Id": str(uuid.uuid4()), "X-Request-Timestamp": str(int(time.time())),
        })
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise ValueError("The server response is too large.")
                result = json.loads(data)
                if not isinstance(result, dict) or not isinstance(result.get("spoken_feedback"), str):
                    raise ValueError("The server response was invalid.")
                return result
        except urllib.error.HTTPError as exc:
            status = exc.code; exc.close()
            raise RuntimeError(f"The server rejected the request (HTTP {status}). The command will not be retried automatically.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("The connection failed. The command result may be uncertain. Check the state before sending a new command.") from None


def say(text):
    try:
        subprocess.run(["espeak-ng", "-v", "en", "--stdin"], input=str(text)[:600],
                       text=True, check=True, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        print("Speech playback failed; the result is available as text.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path(__file__).with_name(".env")))
    parser.add_argument("--send-wav", type=Path, help="Send a test recording without wake-word detection")
    args = parser.parse_args()
    load_env(Path(args.config))
    client = Client()
    if args.send_wav:
        result = client.send(args.send_wav.read_bytes())
        print(result["spoken_feedback"]); say(result["spoken_feedback"])
        return
    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    model_path = Path(os.environ["WAKE_MODEL"])
    if not model_path.is_file() or model_path.suffix != ".onnx":
        raise ValueError("WAKE_MODEL requires a downloaded ONNX wake-word model.")
    model = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
    threshold = float(os.getenv("WAKE_THRESHOLD", "0.5"))
    rms = float(os.getenv("SPEECH_RMS", "300"))
    if not 0 < threshold <= 1 or not 0 < rms <= 32767:
        raise ValueError("Invalid wake-word or speech-level threshold.")
    device = os.getenv("MIC_DEVICE") or None
    if device and device.isdecimal(): device = int(device)
    print("Satellite ready. Wake-word detection is local. Commands are sent to the server.")
    while True:
        try:
            model.reset()
            with sd.RawInputStream(samplerate=RATE, blocksize=FRAME, channels=1, dtype="int16", device=device) as stream:
                while True:
                    data, overflow = stream.read(FRAME)
                    if overflow: raise RuntimeError("Microphone audio overflowed.")
                    scores = model.predict(np.frombuffer(data, dtype=np.int16))
                    if scores and max(float(v) for v in scores.values()) >= threshold:
                        break
            # Close microphone capture before playing the cue or spoken feedback.
            print("Wake phrase detected. Speak after the cue.")
            beep = b"".join(struct.pack("<h", int(3500 * math.sin(2 * math.pi * 880 * i / RATE))) for i in range(2400))
            with sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16") as output:
                output.write(beep)
            segment = Segment(rms)
            with sd.RawInputStream(samplerate=RATE, blocksize=FRAME, channels=1, dtype="int16", device=device) as stream:
                while True:
                    data, overflow = stream.read(FRAME)
                    if overflow: raise RuntimeError("The recording overflowed. No command was sent.")
                    if segment.feed(bytes(data)): break
            audio = segment.wav()
            if audio is None:
                print("No speech detected; nothing was sent.")
                continue
            print("Processing command…")
            result = client.send(audio)
            print(result["spoken_feedback"]); say(result["spoken_feedback"])
        except KeyboardInterrupt:
            break
        except Exception as exc:
            # Do not log configuration, credentials, raw HTTP responses, or recordings.
            print("Command failed (" + type(exc).__name__ + "). Check the connection and device state.")
            say("Command failed. Check the device state.")
            time.sleep(2)


if __name__ == "__main__":
    main()
