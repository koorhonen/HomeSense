const $ = (id) => document.getElementById(id);
let settings, busy = false, recording = false, recorder, startedAt, stopTimer, clockTimer;
const cards = new Map();

function feedback(title, message, error = false) {
  $('feedback-title').textContent = title;
  $('feedback-message').textContent = message;
  $('feedback').classList.toggle('error', error);
}
function step(name, state, label) {
  const node = document.querySelector(`[data-step="${name}"]`);
  node.className = state;
  node.querySelector('.step-state').textContent = label;
}
function resetSteps() {
  for (const name of ['input', 'speech', 'rules', 'device']) step(name, '', 'Waiting');
}
function controls() {
  $('replay-feedback').disabled = busy || recording || !('speechSynthesis' in window);
  $('send').disabled = busy || recording || !settings || !settings.speech.ready;
  $('command').disabled = busy || recording;
  $('record').disabled = busy || !settings || !settings.speech.ready || !navigator.mediaDevices?.getUserMedia;
  $('record').classList.toggle('recording', recording);
  $('record').setAttribute('aria-pressed', String(recording));
  $('record-label').textContent = recording ? 'Stop and process' : 'Start voice command';
  document.querySelectorAll('[data-example]').forEach((b) => b.disabled = busy || recording);
}
function updateCard(entity, state) {
  const card = cards.get(entity);
  if (!card) return;
  card.classList.toggle('on', state === 'on');
  card.querySelector('.device-state').textContent = state === 'on' ? 'On' : state === 'off' ? 'Off' : 'Unknown';
}
function history(text, message, status) {
  $('empty-history')?.remove();
  const li = document.createElement('li');
  const time = document.createElement('time');
  const now = new Date();
  time.dateTime = now.toISOString();
  time.textContent = now.toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const details = document.createElement('span'); details.className = 'event-details'; details.textContent = text;
  const small = document.createElement('small'); small.textContent = message; details.append(small);
  const badge = document.createElement('span'); badge.className = 'event-badge'; badge.textContent = status;
  li.append(time, details, badge); $('history').prepend(li);
  while ($('history').children.length > 20) $('history').lastElementChild.remove();
}
async function submit(source, body) {
  busy = true; controls();
  step('input', 'done', source === 'voice' ? 'Recorded' : 'Typed');
  step('speech', 'active', 'Interpreting…');
  feedback(source === 'voice' ? `${settings.speech.label} is processing the recording` : 'Gemma 4 is interpreting the command',
    source === 'voice' ? 'Audio is being sent to Google’s API. Please wait.' : 'The text command is being sent to Gemma 4 through Google’s API.');
  try {
    const response = await fetch(`/api/${source}`, {
      method: 'POST',
      headers: {'Content-Type': source === 'voice' ? 'audio/wav' : 'application/json', 'X-HomeSense-Token': settings.csrf_token},
      body,
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.message || 'Request failed.');
      error.stage = result.stage; error.transcript = result.transcript; throw error;
    }
    $('transcript').textContent = result.transcript || '—';
    step('speech', 'done', `${result.interpretation_seconds ?? '—'} s`);
    if (['rejected', 'clarification'].includes(result.status)) {
      step('rules', result.status === 'clarification' ? '' : 'error', result.status === 'clarification' ? 'Clarify' : 'Rejected'); step('device', '', 'Not executed');
    } else {
      step('rules', 'done', 'Allowed');
      step('device', result.status === 'unconfirmed' ? 'error' : 'done',
        result.status === 'simulated' ? 'Simulated' : result.status === 'confirmed' ? 'State reported' : 'Unconfirmed');
      updateCard(result.entity_id, result.state);
    }
    const labels = {simulated: 'Simulation complete', confirmed: 'Home Assistant responded', unconfirmed: 'State remains uncertain', rejected: 'Command not executed', clarification: 'Clarify the command'};
    feedback(labels[result.status], result.message, ['rejected', 'unconfirmed'].includes(result.status));
    history(result.transcript, result.message, labels[result.status]);
    speakFeedback(result.spoken_feedback || result.message);
  } catch (error) {
    if (error.stage === 'device') {
      step('speech', 'done', 'Interpreted');
      step('rules', 'done', 'Allowed');
      if (error.transcript) $('transcript').textContent = error.transcript;
    } else if (error.stage === 'rules') { step('speech', 'done', 'Interpreted'); step('rules', 'error', 'Rejected'); }
    else step('speech', 'error', 'Error');
    step('device', 'error', 'Unconfirmed');
    feedback('Processing error', error.message, true);
    speakFeedback('Command processing failed. ' + error.message);
    history(source === 'voice' ? 'Voice command' : $('command').value, error.message, 'Error');
  } finally { busy = false; controls(); }
}

async function startRecording() {
  window.speechSynthesis?.cancel();
  busy = true; controls();
  let stream, context;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
    context = new AudioContext();
    await context.audioWorklet.addModule('/recorder.js');
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, 'homesense-recorder');
    const chunks = [];
    node.port.onmessage = ({data}) => {
      if (data.type === 'finished') { stopRecording(); return; }
      const samples = data.samples;
      chunks.push(samples);
      let sum = 0; for (const v of samples) sum += v * v;
      const level = Math.min(100, Math.round(Math.sqrt(sum / samples.length) * 350));
      $('volume').value = level;
      document.querySelector('.meter').setAttribute('aria-valuenow', String(level));
    };
    source.connect(node); node.connect(context.destination);
    recorder = {stream, context, node, source, chunks};
    recording = true; startedAt = performance.now(); resetSteps();
    $('command').value = '';
    $('transcript').textContent = '—'; step('input', 'active', 'Recording…');
    feedback('Listening', 'Say one command. The recording will be sent to Google when recording ends.');
    clockTimer = setInterval(() => $('record-time').textContent = `Recording ${Math.min(8, (performance.now() - startedAt) / 1000).toFixed(1)} / 8 s`, 100);
    stopTimer = setTimeout(stopRecording, 8000);
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    if (context) await context.close();
    feedback('Microphone unavailable', 'Allow microphone access and open the app using its localhost address. ' + error.message, true);
  } finally { busy = false; controls(); }
}
async function encodeWav(chunks, sampleRate) {
  const count = chunks.reduce((sum, c) => sum + c.length, 0);
  if (count < sampleRate * 0.2) throw new Error('Recording is too short. Speak for at least 0.2 seconds.');
  const input = new AudioBuffer({length: count, numberOfChannels: 1, sampleRate});
  let offset = 0;
  for (const chunk of chunks) { input.copyToChannel(chunk, 0, offset); offset += chunk.length; }
  const length = Math.min(128000, Math.floor(count * 16000 / sampleRate));
  const offline = new OfflineAudioContext(1, length, 16000);
  const player = offline.createBufferSource(); player.buffer = input; player.connect(offline.destination); player.start();
  const audio = (await offline.startRendering()).getChannelData(0);
  const bytes = new ArrayBuffer(44 + audio.length * 2), view = new DataView(bytes);
  const str = (at, text) => [...text].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  str(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); str(8, 'WAVE'); str(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, audio.length * 2, true);
  for (let i = 0; i < audio.length; i++) { const v = Math.max(-1, Math.min(1, audio[i])); view.setInt16(44 + i * 2, v * (v < 0 ? 32768 : 32767), true); }
  return bytes;
}
async function stopRecording() {
  if (!recording) return;
  recording = false; busy = true; controls(); clearTimeout(stopTimer); clearInterval(clockTimer);
  const {stream, context, source, node, chunks} = recorder;
  source.disconnect(); node.disconnect(); stream.getTracks().forEach((track) => track.stop());
  const rate = context.sampleRate; await context.close(); node.port.close(); recorder = null;
  $('volume').value = 0; $('record-time').textContent = 'Recording finished. Microphone is off.';
  try { await submit('voice', await encodeWav(chunks, rate)); }
  catch (error) { step('input', 'error', 'Error'); feedback('Recording could not be processed', error.message, true); busy = false; controls(); }
}
$('record').addEventListener('click', () => recording ? stopRecording() : startRecording());
$('command-form').addEventListener('submit', (event) => {
  event.preventDefault(); if (busy || recording || !settings) return;
  const text = $('command').value.trim(); if (!text) return;
  resetSteps(); $('transcript').textContent = text; submit('text', JSON.stringify({text}));
});
document.querySelectorAll('[data-example]').forEach((button) => button.addEventListener('click', () => { $('command').value = button.dataset.example; $('command').focus(); }));
$('clear-history').addEventListener('click', () => { $('history').replaceChildren(); });
window.addEventListener('pagehide', () => { recorder?.stream.getTracks().forEach((track) => track.stop()); });

async function initialize() {
  try {
    const response = await fetch('/api/status');
    if (!response.ok) throw new Error('Could not load server settings.');
    settings = await response.json();
    renderSpeech();
    feedback('Ready for a command', settings.speech.ready ? 'Gemma 4 interprets voice and text commands. Use your own phrasing.' : 'Set GEMINI_API_KEY in the server .env file and restart the server to enable commands.');
    $('mode').textContent = settings.dry_run ? 'Simulation mode' : 'Home Assistant enabled';
    $('mode-description').textContent = settings.dry_run ? 'Try commands safely. Real devices are not controlled.' : 'Allowed commands are sent to real devices.';
    $('mode-dot').classList.toggle('live', !settings.dry_run);
    $('device-stage').textContent = settings.dry_run ? 'Simulator' : 'Home Assistant';
    $('device-stage-hint').textContent = settings.dry_run ? 'Updates the simulated device state' : 'Sends the command and checks the state';
    $('device-note').textContent = settings.dry_run ? 'Simulated devices · states reset when the server restarts.' : 'States show the last command result. External state changes are not tracked continuously.';
    for (const device of settings.devices) {
      const card = document.createElement('div'); card.className = 'device';
      const icon = document.createElement('span'); icon.className = 'device-icon'; icon.textContent = device.entity_id.startsWith('light.') ? '☼' : '⏻'; icon.setAttribute('aria-hidden', 'true');
      const info = document.createElement('div');
      const name = document.createElement('span'); name.className = 'device-name'; name.textContent = device.name;
      const id = document.createElement('span'); id.className = 'device-id'; id.textContent = device.entity_id; info.append(name, id);
      const state = document.createElement('span'); state.className = 'device-state'; state.setAttribute('aria-live', 'polite');
      card.append(icon, info, state); cards.set(device.entity_id, card); $('devices').append(card);
      updateCard(device.entity_id, settings.states[device.entity_id]);
    }
    if (!navigator.mediaDevices?.getUserMedia) $('record-hint').textContent = 'Voice input requires a microphone-capable browser and the localhost address.';
    controls();
  } catch (error) { feedback('Application connection unavailable', error.message, true); }
}
function renderSpeech() {
  $('speech-badge').textContent = '● Gemma 4 · cloud API';
  $('speech-label').textContent = settings.speech.label;
  $('speech-location').textContent = 'Gemini transcribes; Gemma 4 interprets';
  $('speech-note').textContent = settings.speech.ready
    ? 'Gemma 4 interprets commands. Gemini transcribes audio. Processing uses Google’s cloud, including in simulation.'
    : 'Command interpretation requires a Google API key.';
  $('privacy-note').textContent = 'Voice, text commands, and allowed device names are sent to Google’s API, including in simulation mode.';
}
let utterance, lastSpoken = 'Ready for a command.';
function speakFeedback(text) {
  lastSpoken = String(text).slice(0, 600);
  if (!$('spoken-feedback').checked || !('speechSynthesis' in window) || recording) return;
  try {
    speechSynthesis.cancel();
    utterance = new SpeechSynthesisUtterance(lastSpoken);
    utterance.lang = 'en-GB';
    const english = speechSynthesis.getVoices().find(voice => voice.lang.toLowerCase().startsWith('en'));
    if (english) utterance.voice = english;
    utterance.onstart = () => $('spoken-note').textContent = 'Speaking feedback…';
    utterance.onend = () => $('spoken-note').textContent = 'Spoken feedback is on.';
    utterance.onerror = () => $('spoken-note').textContent = 'Speech playback failed. The result remains visible; try Replay feedback.';
    speechSynthesis.speak(utterance);
  } catch { $('spoken-note').textContent = 'The browser could not play speech. The result remains visible.'; }
}
$('spoken-feedback').addEventListener('change', () => {
  if (!$('spoken-feedback').checked) window.speechSynthesis?.cancel();
  $('spoken-note').textContent = $('spoken-feedback').checked ? 'Spoken feedback is on.' : 'Spoken feedback is off.';
});
$('replay-feedback').addEventListener('click', () => speakFeedback(lastSpoken));
if (!('speechSynthesis' in window)) {
  $('spoken-feedback').checked = false; $('spoken-feedback').disabled = true; $('replay-feedback').disabled = true;
  $('spoken-note').textContent = 'This browser does not support speech synthesis. Text feedback is available.';
}
window.addEventListener('pagehide', () => window.speechSynthesis?.cancel());
initialize();
