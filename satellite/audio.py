"""Bounded speech segment: eight seconds maximum, ending after one second of silence."""
import io
import math
import struct
import wave

RATE = 16000
FRAME = 1280


class Segment:
    def __init__(self, threshold=300):
        self.threshold = threshold
        self.data = bytearray()
        self.spoken = False
        self.quiet_frames = 0

    def feed(self, data: bytes) -> bool:
        if len(data) % 2:
            raise ValueError("Invalid PCM frame.")
        data = data[:max(0, RATE * 8 * 2 - len(self.data))]
        if not data:
            return True
        self.data.extend(data)
        samples = len(data) // 2
        rms = math.sqrt(sum(v[0] ** 2 for v in struct.iter_unpack("<h", data)) / samples)
        if rms >= self.threshold:
            self.spoken = True
            self.quiet_frames = 0
        else:
            self.quiet_frames += samples
        return (len(self.data) >= RATE * 8 * 2
                or (self.spoken and self.quiet_frames >= RATE)
                or (not self.spoken and len(self.data) >= RATE * 3 * 2))

    def wav(self):
        if not self.spoken:
            return None
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(RATE)
            audio.writeframes(self.data)
        return output.getvalue()
