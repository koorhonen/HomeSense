# Validation and known limitations

## Automated checks

The English-language version passed 58 Python tests using Uvicorn 0.54.0. Coverage includes:

- Gemma text interpretation and the separate Gemini audio transcription stage.
- Missing keys, invalid configuration, malformed/blocked/incomplete model results, and allowed devices/actions.
- AI retries before device control, with at most one gateway execution after successful interpretation.
- Simulation, uncertain HA results, and local browser HTTP protections.
- Actual loopback TLS client/server communication and Uvicorn HTTPS -> satellite API -> Application -> simulated device state.
- Certificate hostname verification, satellite tokens, timestamps, request sizes, duplicate IDs, rate limits, shared command locking, and disconnected clients.
- Audio silence detection, recording termination, and the eight-second size limit.

The browser smoke test passes in Microsoft Edge with a fake microphone, model responses, and speech playback. It checks typed commands, state cards, clarification, HTML-safe output, spoken-feedback calls and muting, and mobile layout. No real audio is sent to Google by automated tests.

## Previously verified live API behavior

With the user’s explicit approval, a real gemma-4-26b-a4b-it request interpreted the Finnish command “Laitahan keittiöön valot” as light.keittio / turn_on / simulated. Successful observations took approximately 2.0–6.5 seconds. No real HA load was switched. Transient Google errors remain possible; a successful sample is not an availability guarantee.

## Not verified on physical hardware

A physical Raspberry Pi, ARM64 dependency installation, ONNX wake-word accuracy, USB microphone capture, and audible eSpeak playback have not been tested in this environment. Browser speech playback was verified by calls to a test double, not by listening to a real voice. Verify the complete voice pipeline on your device using [RASPBERRY.md](RASPBERRY.md).

The LAN firewall has not been changed and the satellite endpoint has not been permanently enabled. There has been no independent security audit or safety certification.

The interface, runtime diagnostics, setup files, and documentation were translated into English. Finnish command inputs and device aliases were retained. Supplied sample display names were translated; custom names are preserved. The Raspberry guide now includes OS/SSH setup, clean source transfer, dependency installation, audio tests, certificates, systemd management, and a troubleshooting table.

## Multi-room satellites

60 Python tests pass, including per-device authentication, trusted room metadata, replay isolation across devices, and propagation through transcription to the Gemma prompt. Cloud semantic room resolution and physical multi-microphone operation have not been tested live.
