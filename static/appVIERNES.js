let ws;
let audioQueue = [];
let isPlaying = false;

function updateStatus(newStatus, level = 0) {
  const statusElement = document.getElementById("status");
  const dot = document.getElementById("status-dot");
  statusElement.innerText = newStatus;

  if (newStatus === "Activo" || newStatus === "Traduciendo" || newStatus === "Enviando") {
    dot.classList.remove("dot-red");
    dot.classList.add("dot-green");
  } else {
    dot.classList.remove("dot-green");
    dot.classList.add("dot-red");
    document.getElementById("audio-bar").style.width = "0%";
  }

  if (level) {
    const bar = document.getElementById("audio-bar");
    const clamped = Math.min(100, level);
    bar.style.width = clamped + "%";
  }
}

function toggleTranslation() {
  const btn = document.getElementById("toggle-translation");
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    ws = new WebSocket(`wss://${window.location.host}/ws/speaker`);
    ws.onopen = () => {
      const inputLang = document.getElementById("input-language").value;
      ws.send(JSON.stringify({ command: "start_translation", lang: "es", input_lang: inputLang }));
      btn.innerText = "Terminar Traducción";
      btn.classList.remove("start-button");
      btn.classList.add("stop-button");
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.status) updateStatus(data.status, data.level || 0);
      if (data.text) {
        document.getElementById("translation-display").innerText = data.text;
        if (data.audio) {
          const audioBlob = new Blob([Uint8Array.from(atob(data.audio), c => c.charCodeAt(0))], { type: "audio/wav" });
          const audioUrl = URL.createObjectURL(audioBlob);
          audioQueue.push(audioUrl);
          if (!isPlaying) playNextAudio();
        }
      }
    };
    ws.onclose = () => {
      updateStatus("Detenido");
      btn.innerText = "Iniciar Traducción";
      btn.classList.remove("stop-button");
      btn.classList.add("start-button");
    };
  } else {
    ws.send(JSON.stringify({ command: "stop_translation" }));
    ws.close();
    btn.innerText = "Iniciar Traducción";
    btn.classList.remove("stop-button");
    btn.classList.add("start-button");
    updateStatus("Detenido");
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
    audio.onerror = () => {
      URL.revokeObjectURL(audioUrl);
      playNextAudio();
    };
    await audio.play().catch((e) => console.error("Error de reproducción:", e));
  } else {
    isPlaying = false;
  }
}
