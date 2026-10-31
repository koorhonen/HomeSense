# Security model and limitations

HomeSense is a single-user LAN prototype, not an independently audited or certified system.

## Implemented controls

- The browser server listens only on 127.0.0.1. Exact Host checks help prevent DNS rebinding.
- State-changing browser requests require a random CSRF token. Foreign Origin headers are rejected and CORS access is not granted.
- Content Security Policy allows only same-origin resources and blocks framing. No external scripts, fonts, or analytics are used.
- UI content is inserted with textContent. Model output is never executed as code, shell commands, arbitrary URLs, or HA service names.
- Gemma 4 proposes an intent. Independent validation accepts only one well-formed action targeting a configured light/switch entity, with turn_on or turn_off. Clarified or rejected intents must not include executable actions.
- Simulation is the default and its gateway never calls HA. Real mode requires valid HA_URL and HA_TOKEN configuration.
- Google requests use a fixed HTTPS endpoint, validated model identifiers, certificate verification, no redirects, and no environment proxy settings.
- Google and HA keys stay server-side, out of URLs, browser code, and status responses. Private .env files are excluded from source control. Raw API error bodies are not returned to the browser.
- Missing Google credentials block both voice and text requests before any network call.
- WAV format and duration are validated. Limits include eight seconds of audio, 2048-byte text requests, and 1 MiB upstream responses. Commands share a lock; connection concurrency and network timeouts are bounded.
- Blocked, truncated, malformed, or disallowed model output does not reach the device gateway. Model thought parts are ignored.
- Transient AI HTTP 500/502/503/504 failures have at most two retries with backoff inside the request budget, before device execution. Invalid output and HA commands are not automatically retried.
- Recordings and command history are not written to application logs. State and history are kept in memory, although the operating system may swap memory to disk.
- HA’s reported target state is checked. Unconfirmed state is not reported as confirmed and the action is not automatically repeated.

## Trust boundaries

The computer account, local processes, browser extensions, and configured Pi are trusted. CSRF does not defend against malware already running on the host. Protect account access, private configuration files, and backups. Do not put private .env files or keys in shared/synchronized folders. Revoke leaked credentials at the issuing service.

AI can misunderstand a negation or infer the wrong action from noise. Structural validation cannot establish what the user intended. Test in simulation and start with low-risk lights. A switch entity can control any load: safety-critical equipment needs separate risk assessment and physical interlocks.

HA’s reported state may be optimistic. Independent physical confirmation may need a sensor. The application allowlist does not constrain a stolen HA token’s permissions. Use appropriately limited HA access, a trusted network, and preferably HTTPS.

## Google cloud data processing

Typed commands, recorded voice, and device catalog information are sent to Google, including in simulation mode. Voice uses two calls: audio goes to Gemini for transcription; the resulting text and allowed device names/IDs/aliases go to Gemma 4 for interpretation. Typed commands call Gemma directly. HA tokens, the server configuration file, and conversation history are not included in prompts.

Cloud retention, logging, use of submitted data, billing, and quotas depend on the Google project and service terms. Local non-logging does not imply that the cloud provider does not log. Review those terms before using sensitive speech. Restrict API access and quotas using the provider’s available controls.

## Spoken feedback

Browser feedback uses SpeechSynthesis with an English voice where available; the browser’s voice provider may use the network. The Pi uses local eSpeak NG. Playback is stopped before microphone capture, and playback failure never retries a device action. Model clarification text is displayed and spoken as data, not executed.

## Optional Raspberry Pi endpoint

SATELLITE_ENABLED starts a separate Uvicorn HTTPS endpoint while browser access remains localhost-only. The only satellite route is POST /satellite/voice. It requires a separate random token of at least 32 characters, a fresh timestamp, UUID request ID, and bounded WAV body. Browser Origin headers are rejected. The Pi verifies the private CA and server hostname; validation is never bypassed.

Browser and satellite commands share the same execution lock. A bounded in-memory response cache prevents duplicate execution for a cached request ID, but is not a durable exactly-once guarantee across restarts. Request rate, upload duration, and connection concurrency are bounded. A disconnected client does not release the command lock before the worker finishes.

The Pi token grants permission to submit commands for all configured devices; it is not a multi-user authorization model. The wake phrase is not identity verification and can be triggered by other people or playback. Protect the Pi and its credential file.

LAN access is disabled by default. Setup requires the intended server address, certificates, credentials, and a firewall rule limited to the Pi’s IP. The app does not change firewall rules or router port forwarding. Do not expose this prototype directly to the internet.

See the [Raspberry Pi installation guide](docs/RASPBERRY.md) for token transfer, certificate renewal, service operation, and deployment limitations. Keep Python, the browser, Uvicorn, the Pi OS, and Home Assistant security-patched. A multi-user or internet deployment requires a new threat assessment and authentication/authorization design.

Multi-room registry mode uses separate device credentials and trusted server-assigned room metadata. Room context is not an access restriction. Remove a registry entry and restart to revoke it. Requests remain serialized; no cross-microphone wake arbitration is provided.
