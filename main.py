import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from threading import Thread
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk
import time
import tempfile

app = FastAPI()

# Ajusta orígenes según tu frontend
origins = [
    "https://proud-dune-06afaf61e.1.azurestaticapps.net",
]


# origins = [
#     "http://localhost:5173",
# ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Estado por sala ---
# Cada sala tendrá:
# {
#   "listeners": { lang: [websockets] },
#   "input_lang": "en-US",
#   "push_stream": obj,
#   "translator": obj,
#   "storage_method": str,
#   "start_time": float,
#   "speaker_count": int,
#   "last_text": str,
#   "transcript_original": [ "segment 1", "segment 2", ... ],
#   "translations": { "es": ["seg1","seg2"], "en": [...] }
# }
rooms = {}

# Cargar .env
load_dotenv()
SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

print(f"Azure Key: {'Sí' if SPEECH_KEY else 'No'}, Region: {'Sí' if SPEECH_REGION else 'No'}")


def ensure_room(room_id: str, input_lang: str = "en-US", storage_method: str = "NO_RECORD"):
    if room_id not in rooms:
        rooms[room_id] = {
            "listeners": {},
            "input_lang": input_lang,
            "push_stream": None,
            "translator": None,
            "storage_method": storage_method,
            "start_time": time.time(),
            "speaker_count": 0,
            "last_text": "",
            "transcript_original": [],
            "translations": {}
        }


# --- WebSocket Orador ---
@app.websocket("/ws/speaker/{room_id}")
async def websocket_speaker(websocket: WebSocket, room_id: str):
    await websocket.accept()
    print(f"✨ Orador conectado a la sala {room_id}")

    loop = asyncio.get_running_loop()

    ensure_room(room_id)
    rooms[room_id]["speaker_count"] += 1
    rooms[room_id]["start_time"] = time.time()

    # Audio config
    audio_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    # SpeechTranslationConfig
    speech_translation_config = speechsdk.translation.SpeechTranslationConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_translation_config.speech_recognition_language = rooms[room_id].get("input_lang", "en-US")
    target_languages = ["es", "en", "fr", "it", "de", "pt", "zh-Hans"]
    for lang in target_languages:
        speech_translation_config.add_target_language(lang)

    translator = speechsdk.translation.TranslationRecognizer(
        translation_config=speech_translation_config,
        audio_config=audio_config
    )

    rooms[room_id]["push_stream"] = push_stream
    rooms[room_id]["translator"] = translator

    # --- Función para enviar traducciones finales ---
    def send_translation_to_listeners(result_event):
        try:
            translations = result_event.translations
            original_text = (result_event.text or "").strip()
            if not original_text:
                return

            # Evitar duplicados por el mismo resultado
            if original_text == rooms[room_id].get("last_text", ""):
                return
            rooms[room_id]["last_text"] = original_text

            # Guardar original
            rooms[room_id]["transcript_original"].append(original_text)

            # Guardar traducciones por idioma y enviar a oyentes
            for lang, translated_text in translations.items():
                if translated_text is None:
                    continue
                # inicializar lista de traducciones
                rooms[room_id]["translations"].setdefault(lang, []).append(translated_text)

                # construir mensaje
                message = {
                    "original_text": original_text,
                    "translated_text": translated_text,
                    "audio_url": ""
                }
                # enviar solo a oyentes interesados en ese idioma
                if lang in rooms[room_id]["listeners"]:
                    for client in list(rooms[room_id]["listeners"][lang]):
                        try:
                            asyncio.run_coroutine_threadsafe(client.send_json(message), loop)
                        except Exception as e:
                            print(f"Error enviando a oyente en {lang}: {e}")
        except Exception as e:
            print("Error en send_translation_to_listeners:", e)

    translator.recognized.connect(lambda evt: send_translation_to_listeners(evt.result))
    translator.recognizing.connect(lambda evt: print(f"Parcial: {evt.result.text}"))  # solo consola

    translation_thread = Thread(target=lambda: translator.start_continuous_recognition_async().get(), daemon=True)
    translation_thread.start()
    print(f"Reconocimiento iniciado en sala {room_id}")

    try:
        while True:
            audio_data = await websocket.receive_bytes()
            if audio_data and rooms[room_id]["storage_method"] == "NO_RECORD":
                push_stream.write(audio_data)
    except WebSocketDisconnect:
        print(f"Orador desconectado de sala {room_id}")
    finally:
        try:
            push_stream.close()
        except Exception:
            pass
        try:
            translator.stop_continuous_recognition_async().get()
        except Exception:
            pass
        translation_thread.join(timeout=1)
        rooms[room_id]["translator"] = None
        rooms[room_id]["push_stream"] = None
        # NOTA: no eliminamos el transcript para que pueda exportarse luego
        rooms[room_id]["speaker_count"] -= 1
        if rooms[room_id]["speaker_count"] < 0:
            rooms[room_id]["speaker_count"] = 0
        await websocket.close()
        print(f"Reconocimiento detenido en sala {room_id}")


# --- WebSocket Oyente ---
@app.websocket("/ws/listener/{room_id}")
async def websocket_listener(websocket: WebSocket, room_id: str):
    # NOTE: listener passes lang via query param ?lang=es
    await websocket.accept()
    params = websocket.query_params
    lang = params.get("lang", "es")
    ensure_room(room_id)
    if lang not in rooms[room_id]["listeners"]:
        rooms[room_id]["listeners"][lang] = []
    rooms[room_id]["listeners"][lang].append(websocket)
    print(f"👂 Oyente conectado a sala {room_id}, idioma {lang}")

    try:
        while True:
            # el cliente puede enviar pings o comandos, aquí solo recibimos y descartamos
            await websocket.receive_text()
    except WebSocketDisconnect:
        try:
            rooms[room_id]["listeners"][lang].remove(websocket)
        except Exception:
            pass
        print(f"🚪 Oyente desconectado de sala {room_id}, idioma {lang}")
        if not rooms[room_id]["listeners"].get(lang):
            rooms[room_id]["listeners"].pop(lang, None)


# --- Configuración sala ---
@app.post("/configure/{room_id}")
async def configure_room(room_id: str, request: Request):
    form_data = await request.form()
    action = form_data.get("action")
    input_lang = form_data.get("input_lang") or "en-US"
    storage_method = form_data.get("storage_method") or "NO_RECORD"

    ensure_room(room_id)
    rooms[room_id]["input_lang"] = input_lang
    rooms[room_id]["storage_method"] = storage_method
    rooms[room_id]["start_time"] = time.time()

    return JSONResponse({"status": action, "room_id": room_id, "input_lang": input_lang, "storage_method": storage_method})


# --- Endpoint estadísticas ---
@app.get("/stats")
async def stats():
    now = time.time()
    stats_data = []
    for room_id, info in rooms.items():
        oyentes_total = sum(len(clients) for clients in info["listeners"].values())
        tiempo = int(now - info.get("start_time", now))
        stats_data.append({
            "sala": room_id,
            "oradores": info.get("speaker_count", 0),
            "oyentes": oyentes_total,
            "tiempo_segundos": tiempo
        })
    return JSONResponse(stats_data)


# --- Export endpoints ---
@app.get("/export/original/{room_id}")
async def export_original(room_id: str):
    if room_id not in rooms:
        return JSONResponse({"error": "Sala no encontrada"}, status_code=404)
    text_segments = rooms[room_id].get("transcript_original", [])
    full_text = "\n".join(text_segments).strip()
    if full_text == "":
        return JSONResponse({"room_id": room_id, "text": ""})
    # devolver como attachment txt
    return Response(content=full_text, media_type="text/plain", headers={
        "Content-Disposition": f"attachment; filename={room_id}_original.txt"
    })


@app.get("/export/translation/{room_id}/{lang}")
async def export_translation(room_id: str, lang: str):
    if room_id not in rooms:
        return JSONResponse({"error": "Sala no encontrada"}, status_code=404)
    lang_list = rooms[room_id].get("translations", {}).get(lang, [])
    full_text = "\n".join(lang_list).strip()
    return Response(content=full_text, media_type="text/plain", headers={
        "Content-Disposition": f"attachment; filename={room_id}_translation_{lang}.txt"
    })


# Util: mapear input_lang a voz por defecto (neural voices)
VOICE_MAP = {
    "en-US": "en-US-JennyNeural",
    "es-ES": "es-ES-AlvaroNeural",
    "fr-FR": "fr-FR-DeniseNeural",
    "it-IT": "it-IT-ElsaNeural",
    "de-DE": "de-DE-KatjaNeural",
    "pt-PT": "pt-PT-DuarteNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural"
}


@app.post("/export/audio/{room_id}")
async def export_audio(room_id: str, voice_lang: str = Form(None)):
    """
    Genera un WAV con el texto original completo de la sala usando Azure TTS y lo devuelve.
    voice_lang: opcional, si no se pasa se elige automáticamente desde input_lang de la sala.
    """
    if room_id not in rooms:
        return JSONResponse({"error": "Sala no encontrada"}, status_code=404)
    segments = rooms[room_id].get("transcript_original", [])
    full_text = "\n".join(segments).strip()
    if not full_text:
        return JSONResponse({"error": "No hay texto para sintetizar"}, status_code=400)

    chosen_voice = None
    if voice_lang:
        chosen_voice = voice_lang
    else:
        input_lang = rooms[room_id].get("input_lang", "en-US")
        chosen_voice = VOICE_MAP.get(input_lang, "en-US-JennyNeural")

    # crear config de Azure TTS
    try:
        speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        # voz
        # la propiedad 'speech_synthesis_voice_name' define la voz
        speech_config.speech_synthesis_voice_name = chosen_voice

        # temporal file
        tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmpfile_name = tmpfile.name
        tmpfile.close()

        audio_out = speechsdk.audio.AudioOutputConfig(filename=tmpfile_name)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_out)

        result = synthesizer.speak_text_async(full_text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            print("TTS failed:", result.reason)
            return JSONResponse({"error": "Fallo en la síntesis de audio"}, status_code=500)

        # leer bytes y devolver
        def iterfile():
            with open(tmpfile_name, "rb") as f:
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    yield chunk

        filename = f"{room_id}_original.wav"
        headers = {
            "Content-Disposition": f"attachment; filename={filename}"
        }
        return StreamingResponse(iterfile(), media_type="audio/wav", headers=headers)
    except Exception as e:
        print("Error generando audio:", e)
        return JSONResponse({"error": "Error al generar audio"}, status_code=500)


# --- Página principal (unchanged) ---
@app.get("/", response_class=HTMLResponse)
def root():
    html_content = """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <title>Vortex Live Translation - Multi-sala</title>
        <style>
            body { font-family: Arial; background-color: #f4f6f8; color: #333; padding: 20px; }
            .container { max-width: 600px; margin: auto; background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
            h2 { color: #0078D7; }
            label { font-weight: bold; margin-top: 10px; display: block; }
            select, button, input { width: 100%; padding: 10px; margin-top: 5px; border-radius: 5px; border: 1px solid #ccc; }
            button { background-color: #0078D7; color: white; cursor: pointer; }
            .status { margin-top: 20px; padding: 10px; background-color: #e0e0e0; border-radius: 5px; }
            .active { background-color: #d4edda; color: #155724; }
            .inactive { background-color: #f8d7da; color: #721c24; }
            .stats { margin-top: 20px; background: #f9f9f9; padding: 10px; border-radius: 5px; font-size: 14px; }
        </style>
      </head>
      <body>
        <div class="container">
          <h2>Panel de Operador - Multi-sala</h2>
          <label for="room_id">ID de la sala:</label>
          <input type="text" id="room_id" placeholder="Sala1"/>

          <label for="input_lang">Idioma del orador:</label>
          <select id="input_lang">
            <option value="es-ES">Español</option>
            <option value="en-US">Inglés</option>
            <option value="fr-FR">Francés</option>
            <option value="it-IT">Italiano</option>
            <option value="de-DE">Alemán</option>
            <option value="pt-PT">Portugués</option>
            <option value="zh-CN">Chino</option>
          </select>

          <label for="storage_method">Método de almacenamiento:</label>
          <select id="storage_method">
            <option value="NO_RECORD">Procesar en memoria (sin grabación)</option>
            <option value="LOCAL_RECORD" disabled>Grabar localmente (no implementado)</option>
          </select>

          <button onclick="configureRoom('start')" style="background-color: #2ecc71;">Iniciar Traducción</button>
          <button onclick="configureRoom('stop')" style="background-color: #e74c3c;">Detener Traducción</button>

          <div id="status" class="status inactive">Estado: Detenido</div>

          <div class="stats">
            <h3>📊 Estadísticas</h3>
            <div id="statsContent">Sin datos</div>
          </div>
        </div>

        <script>
        async function configureRoom(action){
            const room_id = document.getElementById('room_id').value || 'Sala1';
            const input_lang = document.getElementById('input_lang').value;
            const storage_method = document.getElementById('storage_method').value;

            const formData = new FormData();
            formData.append('action', action);
            formData.append('input_lang', input_lang);
            formData.append('storage_method', storage_method);

            const res = await fetch(`/configure/${room_id}`, { method: 'POST', body: formData });
            const data = await res.json();
            document.getElementById('status').textContent = `Estado: ${action.toUpperCase()} (Sala: ${room_id}, Idioma: ${input_lang})`;
            document.getElementById('status').className = action === 'start' ? 'status active' : 'status inactive';
        }

        async function loadStats(){
            const res = await fetch('/stats');
            const data = await res.json();
            let html = '';
            data.forEach(item => {
                html += `<p><strong>Sala:</strong> ${item.sala} | <strong>Oradores:</strong> ${item.oradores} | <strong>Oyentes:</strong> ${item.oyentes} | <strong>Tiempo:</strong> ${item.tiempo_segundos} seg</p>`;
            });
            if(html==='') html = 'Sin datos';
            document.getElementById('statsContent').innerHTML = html;
        }

        setInterval(loadStats, 3000); // refresca cada 3 segundos
        </script>
      </body>
    </html>
    """
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
