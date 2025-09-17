let ws;
let mediaRecorder;

const translatedText = document.getElementById("translatedText");
const translatedAudio = document.getElementById("translatedAudio");

document.getElementById("startBtn").onclick = async () => {
  ws = new WebSocket(`ws://${window.location.host}/ws/speech`);

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(stream);

  mediaRecorder.ondataavailable = event => {
    if (event.data.size > 0) {
      event.data.arrayBuffer().then(buf => ws.send(buf));
    }
  };

  mediaRecorder.start(250); // enviar cada 250ms

  ws.onmessage = event => {
    const msg = JSON.parse(event.data);
    translatedText.innerText = msg.translated_text;
    if (msg.audio) {
      const audioBytes = new Uint8Array(msg.audio.match(/.{2}/g).map(byte => parseInt(byte, 16)));
      const blob = new Blob([audioBytes], { type: "audio/wav" });
      translatedAudio.src = URL.createObjectURL(blob);
      translatedAudio.play();
    }
  };
};

document.getElementById("stopBtn").onclick = () => {
  if (mediaRecorder) mediaRecorder.stop();
  if (ws) ws.close();
};
