# HomeSense

Voice and text control for Home Assistant, with a simulator and a Raspberry Pi voice satellite. **Gemma 4 interprets commands through Google’s cloud API.** Gemini only transcribes audio. No local language model or dedicated GPU is required.

The interface, documentation, errors, and spoken feedback are in English. Finnish commands and your existing device names remain supported. The initial wake phrase is **Hey Jarvis**.

## Quick start on Windows

1. Open `start.cmd` and keep its console window open.
2. Open **http://127.0.0.1:8000**.
3. Configure the API key below, restart the server, and refresh the browser.
4. In simulation mode, try the Finnish command **Sytytä keittiön valo** (turn on the kitchen light).

Use a supported, security-patched Python 3.11 or later. Browser-only operation has no pip dependencies. The launcher can also use the Python runtime bundled with Codex, when available. Alternatively, run `python -m homesense.server` from the project directory. Stop with Ctrl+C.

Simulation is enabled by default. Device states are kept in server memory; browser history is kept only in the current tab. States start as Unknown and reset when the server restarts. The simulator never contacts Home Assistant, but AI requests still use Google’s API.

## Google API configuration

Copy `.env.example` to `.env` without overwriting existing secrets, then configure:

```dotenv
GEMINI_API_KEY=your_api_key_here
GEMMA_MODEL=gemma-4-26b-a4b-it
GEMINI_STT_MODEL=gemini-3.1-flash-lite
GEMINI_TIMEOUT=60
HA_DRY_RUN=true
```

Create your key in [Google AI Studio](https://aistudio.google.com/apikey). Keep it in the server’s private `.env` file or process environment, never in source control or the Raspberry Pi configuration. Restart the server after changing settings. Process environment variables take precedence over `.env`.

Without a key, the interface opens but command submission is disabled. Actual key permissions, model availability, billing, and quotas are only verified by a real inference request.

`GEMMA_MODEL` accepts `gemma-4-26b-a4b-it` or `gemma-4-31b-it`. `GEMINI_STT_MODEL` selects the separate audio transcription model. Both use the same Google API key: the variable name GEMINI_API_KEY refers to the API, not the model interpreting commands. Each model call has a GEMINI_TIMEOUT budget of 1–300 seconds.

## Natural-language interpretation

You do not need to list every phrasing. For example, **Laitahan keittiöön valot** can be interpreted as turning on the kitchen light.

Text requires one Gemma 4 request. Voice requires two requests: Gemini transcribes the audio, then Gemma 4 interprets that text. The hosted Gemma variants used here do not accept audio directly. This application runs neither model locally.

Gemma receives the current command and the allowed device IDs, names, and aliases from `devices.json`. It proposes one JSON decision: execute, clarify, or reject. The server independently validates its structure, target device, and action before calling a device gateway. Only `light` and `switch` entities and `turn_on`/`turn_off` actions are supported. A model confidence score does not grant permission to execute anything.

When a request is ambiguous, repeat the complete clarified command. Conversation history is not sent to the model. Negations, conflicting instructions, multiple actions, scheduled requests, and attempts to bypass rules are instructed to be rejected or clarified. Structural validation cannot prove that the model understood your intent: test these cases in simulation.

**Voice, text commands, and the allowed device catalog are sent to Google, including in simulation mode.** API calls may incur charges; check your project’s quotas and data processing terms. HTTP 500/502/503/504 responses receive at most two additional AI attempts with backoff within the time budget. Invalid output, other errors, and Home Assistant commands are not retried automatically.

## Browser controls and spoken feedback

Click **Start voice command**, allow microphone access, speak, and click again to stop. Recording is limited to eight seconds. The browser produces mono, 16 kHz, 16-bit WAV audio; the server validates it before processing.

**Spoken feedback** is enabled by default. Turn it off with the checkbox or use **Replay feedback** to repeat the last response. The browser requests an English voice through SpeechSynthesis. Available voices and pronunciation depend on the browser and operating system; some voices may use an online service. Playback failure does not change the device result. Playback is cancelled before recording starts.

Execution feedback is based on the gateway result: simulated actions are explicitly labelled Simulation, Home Assistant results are described as reported states, and unconfirmed states remain uncertain. Clarification questions come from the model.

## Device catalog and Home Assistant

Edit `devices.json` to add allowed devices and aliases. Preserve your actual Home Assistant entity IDs. Duplicate names referring to different devices are rejected. Restart the server after edits.

To enable real device control:

1. Add the devices to Home Assistant and update their entity IDs in `devices.json`.
2. Set HA_URL, HA_TOKEN, and HA_DRY_RUN=false in `.env`.
3. Restart the server and check that the interface says **Home Assistant enabled**.

HA_TOKEN is a Home Assistant long-lived access token. Use a trusted LAN and preferably HTTPS. The application’s allowlist does not reduce the permissions of a stolen HA token.

The gateway calls turn_on/turn_off, then checks the state reported by Home Assistant. A reported state is not an independent physical measurement. An uncertain result is not retried. Device cards display the result of the last command and do not continuously track external state changes.

## Raspberry Pi installation

See the complete [Raspberry Pi installation guide](docs/RASPBERRY.md): OS preparation, project transfer, server certificates, Python dependencies, microphone tests, wake-word models, HTTPS verification, systemd startup, and troubleshooting.

The Pi detects the wake phrase locally and sends only the subsequent recording to the server. The server retains all Google/HA credentials and returns spoken feedback. The separate HTTPS satellite endpoint is disabled by default; the browser endpoint remains localhost-only.

## Project structure

```text
static/                    Browser interface, WAV conversion, feedback
homesense/server.py          Localhost browser server
homesense/application.py     Interpretation, validation, and gateway orchestration
homesense/google_ai.py       Gemini transcription and Gemma 4 cloud interpretation
homesense/rules.py           Device catalog and independent intent validation
homesense/services.py        HTTP transport, WAV checks, simulator, HA gateway
homesense/satellite_api.py   Optional authenticated HTTPS satellite endpoint
satellite/                 Raspberry Pi client and service template
```

Application receives its interpreter and gateway through its constructor. A new gateway implements execute(Command) and a dry_run property. Add allowed actions with tests; never derive arbitrary URLs or service names from model output.

## Verification

```powershell
python -m unittest discover -s tests -v
```

Full satellite integration tests require `server-requirements.txt`. AI/HA calls use test doubles in automated tests. Optional browser smoke tests require Playwright and a browser: start the app in simulation mode and run `node tests/ui-smoke.cjs`. The test uses fake microphone input, AI responses, and speech playback, without sending recordings to Google. Screenshots are written to `docs`.

[Security](SECURITY.md) · [Validation and limitations](docs/VALIDATION.md)

References: [Hosted Gemma API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api), [Gemini audio](https://ai.google.dev/gemini-api/docs/audio), [Home Assistant REST](https://contributors.home-assistant.io/core/internals/rest-api/).

Multiple room satellites use individual credentials and server-assigned locations. See [multi-room setup](docs/RASPBERRY.md#multiple-rooms-and-satellites).
