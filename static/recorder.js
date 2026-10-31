// AudioWorklet keeps audio capture separate from UI rendering.
class Recorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frames = 0;
    this.finished = false;
  }
  process(inputs) {
    if (this.finished) return true;
    const channels = inputs[0];
    if (channels && channels.length) {
      const length = Math.min(channels[0].length, sampleRate * 8 - this.frames);
      const mono = new Float32Array(length);
      for (const channel of channels) {
        for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / channels.length;
      }
      this.port.postMessage({type: 'chunk', samples: mono}, [mono.buffer]);
      this.frames += length;
      if (this.frames >= sampleRate * 8) {
        this.finished = true;
        this.port.postMessage({type: 'finished'});
      }
    }
    return true;
  }
}
registerProcessor('homesense-recorder', Recorder);
