let ws;
let audioQueue = [];
let isPlaying = false;
let barInterval;

const updateBar = () => {
  const bar = document.getElementById('audio-bar');
  const currentWidth = parseFloat(bar.style.width) || 0;
  const newWidth = currentWidth > 95 ? 50 : currentWidth + 5;
  bar.style.width = `${newWidth}%`;
};

const startBarAnimation = () => {
  if (!barInterval) {
    barInterval = setInterval(updateBar, 100);
  }
};

const stopBarAnimation = () => {
  if (barInterval) {
    clearInterval(barInterval);
    barInterval = null;
    document.getElementById('audio-bar').style.width = '0%';
  }
};

function updateStatus(newStatus) {
  const statusElement = document.getElementById('status');
  const dot = document.getElementById('status-dot');
  statusElement.innerText = newStatus;

  if (newStatus === 'Activo' || newStatus === 'Traduciendo' || newStatus === 'Enviando') {
    dot.classList.remove('dot-red');
    dot.classList.add('dot-green');
    startBarAnimation();
  } else {
    dot.classList.remove('dot-green');
    dot.classList.add('dot-red');
    stopBarAnimation();
  }
}

async function playNextAudio() {
  if (audioQueue.length > 0) {
    isPlaying = true;
    const audioUrl = audioQueue.shift();
    const audio = new Audio(audioUrl);
    audio.onended = () => {
      URL.revokeObjectURL(audioUrl);
      playNextAudio();
    };
    audio.onerror = (e) => {
      console.error("Error al reproducir audio:", e);
      URL.revokeObjectURL(audioUrl);
      playNextAudio();
    };
    await audio.play().catch(e => console.error("Error de reproducción:", e));
  } else {
    isPlaying = false;
  }
}

function toggleTranslation() {
  const button = document.getElementById('toggle-translation');
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    const lang = document.getElementById('input-language').value;
    const url = `ws://${window.location.host}/ws/live`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      console.log("Conectado al WebSocket.");
      ws.send(JSON.stringify({command: "start_translation", lang}));
      button.textContent = "Terminar Traducción";
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.status) {
        updateStatus(data.status);
      }
      if (data.text) {
        console.log("Traducción recibida:", data.text);
        document.getElementById('translation-display').innerText = data.text;

        const audioBytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));
        const audioBlob = new Blob([audioBytes], { type: 'audio/wav' });
        const audioUrl = URL.createObjectURL(audioBlob);
        audioQueue.push(audioUrl);
        if (!isPlaying) {
          playNextAudio();
        }
      }
    };
    ws.onclose = () => {
      console.log("Conexión WebSocket cerrada.");
      updateStatus('Detenido');
      button.textContent = "Iniciar Traducción";
    };
    ws.onerror = (error) => {
      console.error("Error en WebSocket:", error);
    };
  } else {
    ws.send(JSON.stringify({command: "stop_translation"}));
    button.textContent = "Iniciar Traducción";
    updateStatus('Detenido');
  }
}

document.getElementById('toggle-translation').addEventListener('click', toggleTranslation);
