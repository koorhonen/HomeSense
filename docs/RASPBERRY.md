# Raspberry Pi installation guide

This guide installs the HomeSense wake-word satellite on a Raspberry Pi and connects it to the existing HomeSense server. It does not install Home Assistant on the Pi.

## Architecture

```text
Pi microphone -> local wake-word detection -> cue -> up to 8 seconds of audio
 -> HTTPS + device token -> HomeSense server
 -> Gemini transcription -> Gemma 4 interpretation -> validation
 -> simulator or Home Assistant -> response -> Pi speaker
```

Only wake-word detection runs locally on the Pi. Background audio is not continuously uploaded. The Pi holds a satellite token, not a Google API key or HA token. The initial wake phrase is **Hey Jarvis**; a custom HomeSense wake phrase requires a separately trained ONNX model.

## Before you start

Prepare a 64-bit Raspberry Pi, its suitable power supply and storage, a microphone, a speaker, and LAN connectivity to your server. This project targets **Python 3.11 on a 64-bit OS** because the pinned openWakeWord dependency has platform-specific runtime requirements. Check package support before using a newer Python version. The hardware and ARM64 installation have not been tested in this development environment.

The examples assume:

| Setting | Example |
| --- | --- |
| Server LAN address | 192.168.1.100 |
| Pi account | pi |
| Pi hostname | homesense-pi.local |
| Project directory on Pi | /home/pi/HomeSense |
| Private credential directory on Pi | /home/pi/homesense |
| Satellite HTTPS port | 8443 |

Replace these values with your actual settings. The username `pi` is an example, not a pre-existing default account. Keep both devices’ clocks synchronized. Reserve the server’s LAN IP in your router, and keep the laptop awake while using it as a server.

## 1. Prepare Raspberry Pi OS and SSH

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and follow the official [getting-started guide](https://www.raspberrypi.com/documentation/computers/getting-started.html). Select a 64-bit image compatible with Python 3.11; if the current image uses a newer Python, use a supported Python 3.11 environment or an appropriate maintained legacy image. Do not ignore incompatible dependency errors.

In Imager customization, create your user, set the hostname, configure Wi-Fi if needed, and enable SSH, preferably with your SSH public key. Write the image, boot the Pi, and connect from your computer:

```powershell
ssh pi@homesense-pi.local
```

If hostname discovery fails, use the Pi’s LAN IP from your router instead. On the Pi, verify the architecture and Python version:

```bash
uname -m
python3 --version
```

Expect `aarch64` and Python 3.11 for this guide. Do not assume a package built for x86 Windows or 32-bit ARM will work.

## 2. Copy the project without server secrets

Do not copy the server’s `.env`, `.secrets/server.key`, virtual environments, or `.tools` directory to the Pi.

One option is to create a clean transfer archive in the project directory on Windows:

```powershell
tar -czf homesense-pi.tar.gz --exclude="satellite/.env" --exclude="*/__pycache__" homesense satellite
scp homesense-pi.tar.gz pi@homesense-pi.local:/home/pi/
```

The archive includes only those two source directories, excluding the Pi private configuration and Python caches. The server’s top-level secret files are excluded. You can also transfer a clean source checkout over SSH.

On the Pi:

```bash
mkdir -p /home/pi/HomeSense
cd /home/pi/HomeSense
tar -xzf /home/pi/homesense-pi.tar.gz
mkdir -p /home/pi/homesense
chmod 700 /home/pi/homesense
```

The command `python -m satellite.client` must run from the project root containing both `satellite` and `homesense`.

## 3. Install the Pi dependencies

Run on the Pi:

```bash
sudo apt update
sudo apt install python3-venv python3-dev libportaudio2 espeak-ng alsa-utils
cd /home/pi/HomeSense
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r satellite/requirements.txt
```

Use a virtual environment. If pip reports that tflite-runtime, ONNX Runtime, or another wheel is unavailable, stop and verify Python 3.11 and aarch64 rather than installing an unrelated binary or bypassing dependencies.

## 4. Test the microphone and speaker

Connect the microphone and speaker. Run:

```bash
arecord -l
aplay -l
.venv/bin/python -m sounddevice
```

Note the sounddevice input index or device name for MIC_DEVICE. Select the intended default speaker in the OS audio settings; the cue and eSpeak use the default output.

Record and play a short test, using the default ALSA input/output:

```bash
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/homesense-mic-test.wav
aplay /tmp/homesense-mic-test.wav
espeak-ng -v en 'HomeSense speaker test.'
```

If the default device is wrong, configure the OS audio defaults before continuing. Microphone and speaker support for 16 kHz must be verified. The WAV format is mono, signed 16-bit PCM at 16 kHz.

## 5. Download the wake-word models

Run once on the Pi during setup:

```bash
.venv/bin/python -c "import openwakeword; openwakeword.utils.download_models()"
find .venv -name 'hey_jarvis*.onnx'
```

Use the exact ONNX path reported by find as WAKE_MODEL. The download also supplies the shared feature-extraction models. The client does not download models while listening. Check upstream model licenses if you later use this commercially.

## 6. Prepare the server and certificates

On the server computer, in its project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server-requirements.txt
.\.venv\Scripts\python.exe tools/create_satellite_credentials.py --host 192.168.1.100
```

Use your actual server IP or DNS name, without an https prefix. It must match the Pi’s URL. This creates `.secrets/ca.crt`, `server.crt`, `server.key`, and `satellite-token.txt`. Existing files are not overwritten. The CA private key is not retained. Certificates are valid for one year; renewal requires a new certificate set and updating the Pi’s trusted CA.

Protect the server’s `.secrets` directory with account-specific file permissions. Keep the server private key on the server.

Add these settings to the server’s existing `.env`, preserving its Google and HA settings:

```dotenv
SATELLITE_ENABLED=true
SATELLITE_BIND=192.168.1.100
SATELLITE_PORT=8443
SATELLITE_TOKEN_FILE=.secrets/satellite-token.txt
SATELLITE_TLS_CERT=.secrets/server.crt
SATELLITE_TLS_KEY=.secrets/server.key
HA_DRY_RUN=true
```

Stop any existing HomeSense process using port 8000. Start the server using the environment where Uvicorn was installed:

```powershell
.\.venv\Scripts\python.exe -m homesense.server
```

Allow TCP 8443 through the server’s firewall **only from the Pi’s IP on the private LAN**. Do not forward this port from the internet. The application does not change firewall rules. Browser access remains at http://127.0.0.1:8000 on the server computer.

## 7. Transfer only the satellite credentials

From the server project directory, transfer the CA and device token over SSH:

```powershell
scp .secrets/ca.crt .secrets/satellite-token.txt pi@homesense-pi.local:/home/pi/homesense/
```

On the Pi:

```bash
chmod 600 /home/pi/homesense/satellite-token.txt
cd /home/pi/HomeSense
cp satellite/.env.example satellite/.env
chmod 600 satellite/.env
nano satellite/.env
```

Configure:

```dotenv
HOMESENSE_SERVER_URL=https://192.168.1.100:8443
HOMESENSE_CA_FILE=/home/pi/homesense/ca.crt
HOMESENSE_TOKEN_FILE=/home/pi/homesense/satellite-token.txt
WAKE_MODEL=/home/pi/HomeSense/.venv/lib/python3.11/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx
WAKE_THRESHOLD=0.5
MIC_DEVICE=
SPEECH_RMS=300
REQUEST_TIMEOUT=145
```

Replace WAKE_MODEL with the path found earlier. Leave MIC_DEVICE empty for the default microphone, or use its sounddevice index/name. REQUEST_TIMEOUT=145 allows the default two model calls to complete; increase it appropriately if you raise the server’s model timeout.

WAKE_THRESHOLD controls wake detection sensitivity. SPEECH_RMS is a simple sound-level threshold for recording segmentation, not a speech recognizer. Tune it for your microphone and room noise.

## 8. Verify HTTPS before using the wake word

On the Pi, make an authenticated test with a short Finnish command recorded into a valid WAV:

```bash
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/homesense-command.wav
.venv/bin/python -m satellite.client --send-wav /tmp/homesense-command.wav
```

Say **Sytytä keittiön valo** during the recording. With HA_DRY_RUN=true, expect spoken feedback beginning with Simulation. This test sends the command to Google but does not control a real HA device.

The private CA and server hostname are verified. Never disable certificate validation to make the test pass. Both the client and server need correct clocks.

## 9. Run wake-word mode

```bash
.venv/bin/python -m satellite.client
```

Say **Hey Jarvis**, wait for the short cue, then say a Finnish command such as **Laitahan keittiöön valot**. The speaker returns English feedback. Press Ctrl+C to stop.

The microphone closes during the cue, network processing, and playback. If no sound exceeds the speech threshold within about three seconds, nothing is sent. Recording ends after about one second of silence following speech or at the eight-second limit. The client never automatically resends a command after a connection failure.

The server’s simulator state can be viewed by refreshing its browser interface. The browser does not yet continuously display satellite events. Enable real HA control only after testing with a low-risk device.

## 10. Start automatically with systemd

Edit `satellite/homesense-satellite.service` and replace User, WorkingDirectory, and ExecStart with your actual account and paths. The template uses pi and /home/pi/HomeSense.

After manual testing succeeds:

```bash
sudo cp satellite/homesense-satellite.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homesense-satellite
sudo systemctl status homesense-satellite
```

Useful management commands:

```bash
sudo systemctl restart homesense-satellite
sudo systemctl stop homesense-satellite
journalctl -u homesense-satellite -n 50 --no-pager
```

The service account needs audio-device access and read access to the model, CA, and token. The service drops normal stdout feedback rather than storing commands in the journal; startup errors go to stderr. Recordings stay in memory. If service-mode audio fails, compare its account and default audio configuration with the account used for the successful manual test.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Cannot SSH to hostname | Use the Pi’s IP, confirm its user account, SSH setting, and LAN connectivity. |
| pip cannot find a runtime wheel | Confirm aarch64 and Python 3.11; check upstream ARM64 package availability. |
| Microphone missing | Check USB connection, arecord/sounddevice device lists, and audio permissions. |
| Wake phrase never triggers | Check the ONNX path, shared models, microphone selection, and threshold. Say Hey Jarvis, not HomeSense. |
| Commands are cut short | Wait for the cue; adjust SPEECH_RMS and room/microphone placement. |
| Cue or feedback is silent | Test aplay and espeak-ng and correct the OS default speaker. |
| TLS verification fails | Check the CA file, exact IP/DNS name, certificate expiry, and both clocks. |
| Connection refused/times out | Check server power/sleep, SATELLITE_BIND, port 8443, LAN address, and firewall. |
| HTTP 403 | Check the satellite token; do not copy a Google or HA token into its place. |
| HTTP 400 | Check request timestamps and clocks or WAV format. |
| HTTP 409 | Another command is active or a request ID was reused with different audio. |
| HTTP 429 | Requests arrived too quickly. Record a fresh command later. |
| HTTP 502 | Server-side AI/HA processing failed. Check device state before repeating. |

## Security and known limitations

The token permits voice requests for the server’s allowed devices. Treat the Pi as a trusted device and protect it physically. A wake phrase is not user authentication: television audio, visitors, or a recording can activate it.

The server checks TLS, the token, timestamps, request IDs, recording limits, rate limits, and a shared command lock. Duplicate protection is an in-memory cache of up to 128 results for ten minutes, subject to the timestamp check. It resets on restart and is not durable exactly-once delivery. The client neither queues stale commands nor retries them automatically.

This is a single-user LAN prototype. Real Pi hardware, microphone capture, wake-word accuracy, ARM64 package installation, and audible feedback must be verified on your device. Do not expose it directly to the internet or use it as-is for safety-critical loads.

References: [Raspberry Pi setup](https://www.raspberrypi.com/documentation/computers/getting-started.html), [SSH and SCP](https://www.raspberrypi.com/documentation/computers/remote-access.html), [openWakeWord](https://github.com/dscripka/openWakeWord), [sounddevice](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html).

## Multiple rooms and satellites

Install the client on each Pi using the steps above. Give each Pi a unique hostname
and ID (for example `kitchen-pi` and `office-pi`). The display name can be its location.
All clients connect to the same server and share its public CA certificate, but each
must have its own random bearer token. Never copy the server private key to a Pi.

1. Copy `satellites.example.json` to `.secrets/satellites.json` on the server.
2. Generate one token per Pi on the server, for example:

   ```powershell
   python -c "import secrets; from pathlib import Path; Path('.secrets/kitchen-token.txt').write_text(secrets.token_urlsafe(48), encoding='ascii')"
   python -c "import secrets; from pathlib import Path; Path('.secrets/office-token.txt').write_text(secrets.token_urlsafe(48), encoding='ascii')"
   ```

3. Edit the registry: `id` is the client identifier, `name` is its display name,
   `room` is the default command location, and `token_file` is a path relative to
   the server project (absolute paths also work). Protect the registry and token files.
4. Set `SATELLITE_REGISTRY_FILE=.secrets/satellites.json` in the server `.env` and restart.
   In registry mode, the legacy shared token is not accepted.
5. Copy only the corresponding token and public CA certificate to each Pi using SSH.
   Set `HOMESENSE_SATELLITE_ID=kitchen-pi` in that Pi's `satellite/.env` and point
   `HOMESENSE_TOKEN_FILE` to its individual token. Restart its systemd service.
6. Add matching `room` values to the entries in `devices.json`, then restart the server.
   Use consistent room values, such as `Kitchen` and `Office`.

From Kitchen, “käynnistä valot” uses Kitchen as its default location. An explicit
request such as “käynnistä työhuoneen valot” takes precedence. Browser commands have
no automatic room context. Missing local devices or multiple matching devices must
produce a clarification. Room context is sent to the cloud interpreter with the catalog.
The independent entity/action allowlist still validates every proposed operation.
For all lights in a room, configure one Home Assistant light group and list that group
as one device; this version executes one entity per command.

Test in simulation first: use a WAV saying a generic lights command from each Pi,
check the returned `satellite` metadata and selected device, and then test an explicit
other-room request and an ambiguous request. Model decisions are probabilistic;
these scenarios should also be tested against your configured cloud model.

Requests are processed one at a time without queuing. Rate limits and replay IDs are
scoped to each satellite. Revoke one Pi by removing its registry entry and restarting
the server. Rotate its token if compromised. A Pi token permits commands to any allowed
entity; a room is a convenience default, not an access-control boundary.

If adjacent microphones hear the same wake phrase, both may capture it. There is no
cross-device acoustic arbitration in this version; place microphones carefully and
adjust wake thresholds. Busy responses are not retried automatically.

## Migration from the previous project name

The project is now HomeSense. The Python package is `homesense`; start it with `python -m homesense.server`. On existing Pis, update imports by deploying the current client, rename `KOTIALY_*` configuration keys to `HOMESENSE_*`, and update the project and credential paths in the configuration and systemd unit. Stop and disable the old satellite unit before enabling `homesense-satellite`. Existing tokens and TLS certificates remain valid; their filenames do not need to change. The wake phrase remains Hey Jarvis.
