"""Single-user localhost browser server; not for internet exposure."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading

from .application import Application
from .rules import Rules
from .services import ROOT, MAX_BYTES, ServiceError, create_gateway, load_env
from .google_ai import GoogleAIInterpreter

PORT = 8000
STATIC = ROOT / "static"
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/recorder.js": ("recorder.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_handler(application: Application):
    csrf = secrets.token_urlsafe(32)
    lock = application.request_lock

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # Do not log audio, transcripts, credentials, or request paths.
            pass

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            # Drain small rejected requests before closing. Otherwise Windows may
            # reset the TCP connection before the client reads the error response.
            if self.command == "POST" and not getattr(self, "body_consumed", False):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 < length <= MAX_BYTES and not self.headers.get("Transfer-Encoding"):
                        self.connection.settimeout(0.5)
                        self.rfile.read(length)
                except (ValueError, OSError):
                    pass
                finally:
                    self.body_consumed = True
                    self.connection.settimeout(10)
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            # Exact Host validation also blocks DNS rebinding requests.
            return (len(self.headers.get_all("Host", [])) == 1
                    and self.headers.get("Host") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"})

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"message": "Invalid server address."})
            if self.path == "/api/status":
                return self.send(200, {
                    "dry_run": application.gateway.dry_run,
                    "csrf_token": csrf,
                    "max_seconds": 8,
                    "speech": {"label": "Gemma 4 (Google API)", "ready": bool(getattr(application.interpreter, "api_key", ""))},
                    "devices": [{"name": d["name"], "entity_id": d["entity_id"]} for d in application.rules.devices],
                    "states": dict(getattr(application.gateway, "states", {})),
                })
            entry = FILES.get(self.path)
            if entry:
                filename, content_type = entry
                return self.send(200, (STATIC / filename).read_bytes(), content_type)
            self.send(404, {"message": "Not found."})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {"message": "Invalid server address."})
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers['Host']}":
                return self.send(403, {"message": "Request blocked (Origin)."})
            if not secrets.compare_digest(self.headers.get("X-HomeSense-Token", "").encode("utf-8"), csrf.encode("ascii")):
                return self.send(403, {"message": "Refresh the page before sending a command."})
            if self.path not in {"/api/voice", "/api/text"}:
                return self.send(404, {"message": "Not found."})
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                return self.send(400, {"message": "Invalid transfer encoding."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send(400, {"message": "Invalid content length."})
            limit = MAX_BYTES if self.path == "/api/voice" else 2048
            if not 0 < length <= limit:
                return self.send(413, {"message": "The request is empty or too large."})
            expected_type = "audio/wav" if self.path == "/api/voice" else "application/json"
            if self.headers.get_content_type() != expected_type:
                return self.send(415, {"message": "Invalid content type."})
            if not lock.acquire(blocking=False):
                return self.send(409, {"message": "A previous command is still processing."})
            try:
                body = self.rfile.read(length)
                self.body_consumed = True
                if len(body) != length:
                    return self.send(400, {"message": "The request was truncated."})
                if self.path == "/api/voice":
                    result = application.voice(body)
                else:
                    payload = json.loads(body)
                    text = payload.get("text") if isinstance(payload, dict) else None
                    if not isinstance(text, str) or not 1 <= len(text) <= 300:
                        return self.send(400, {"message": "Enter a command containing 1–300 characters."})
                    result = application.text(text)
                self.send(200, result)
            except ServiceError as exc:
                self.send(502, {"message": str(exc), "stage": exc.stage, "transcript": exc.transcript})
            except (ValueError, UnicodeError):
                self.send(400, {"message": "Invalid input."})
            except (TimeoutError, ConnectionError):
                pass
            except Exception:
                self.send(500, {"message": "Internal error. The command result could not be confirmed."})
            finally:
                lock.release()

    return Handler


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        print("Connection handling failed; details are not logged.")


def main():
    load_env(ROOT / ".env")
    rules = Rules(json.loads((ROOT / "devices.json").read_text(encoding="utf-8-sig")))
    app = Application(rules, GoogleAIInterpreter(), create_gateway())
    server = LocalServer(("127.0.0.1", PORT), make_handler(app))
    remote = None
    try:
        satellite_mode = os.getenv("SATELLITE_ENABLED", "false").lower()
        if satellite_mode not in {"true", "false"}:
            raise ValueError("SATELLITE_ENABLED must be true or false.")
        if satellite_mode == "true":
            from .satellite_api import start_satellite
            remote = start_satellite(app)
        print(f"HomeSense: http://127.0.0.1:{PORT} | simulation={app.gateway.dry_run}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if remote:
            remote[0].should_exit = True
            remote[1].join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
