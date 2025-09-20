// audio-processor.js

class AudioProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const inputChannelData = inputs[0];
    if (inputChannelData && inputChannelData.length > 0) {
      this.port.postMessage(inputChannelData[0]); 
    }
    return true; 
  }
}

registerProcessor('audio-processor', AudioProcessor);