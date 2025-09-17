from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import azure.cognitiveservices.speech as speechsdk
import sounddevice as sd
import numpy as np
import os
import base64
from dotenv import load_dotenv
import json

# Cargar .env
load_dotenv()

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

if not SPEECH_KEY or not SPEECH_REGION:
    raise ValueError("Las variables de entorno SPEECH_KEY y SPEECH_REGION no están configuradas.")

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

translation_queue = asyncio.Queue()
connected_clients = set()

audio_task_running = asyncio.Event()
audio_processing_task_handle = None
broadcast_translations_task_handle = None

VOICE_MAP = {
    "es": "es-ES-ElviraNeural",
    "en": "en-US-JennyNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
}

CURRENT_TARGET_LANG = "es"


async def save_translation_to_db(text: str):
    print(f"💾 Guardando en la base de datos: {text}")
    await asyncio.sleep(0.05)


async def audio_processing_task(timeout: int = 180):
    """
    Captura audio del micrófono del servidor, lo envía a Azure STT+Translation,
    y publica traducciones en translation_queue.
    """
    global audio_processing_task_handle, CURRENT_TARGET_LANG
    print("Iniciando tarea de procesamiento de audio...")

    loop = asyncio.get_running_loop()

    # Definir formato explícito
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000, bits_per_sample=16, channels=1
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY, region=SPEECH_REGION
    )
    translation_config.speech_recognition_language = "en-US"
    translation_config.add_target_language(CURRENT_TARGET_LANG)

    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config, audio_config=audio_config
    )

    def recognizing_cb(evt):
        if evt.result.text:
            print(f"[Parcial]: {evt.result.text}")

    def recognized_cb(evt):
        try:
            text = evt.result.translations.get(CURRENT_TARGET_LANG, "")
        except Exception:
            text = ""
        if text:
            print(f"✅ Traducción reconocida ({CURRENT_TARGET_LANG}): {text}")
            loop.call_soon_threadsafe(translation_queue.put_nowait, text)

    def canceled_cb(evt):
        print("Recognizer canceled:", evt)

    recognizer.recognizing.connect(recognizing_cb)
    recognizer.recognized.connect(recognized_cb)
    recognizer.canceled.connect(canceled_cb)

    recognizer.start_continuous_recognition()
    try:
        print("🎤 Escuchando micrófono (servidor)...")
        await broadcast_status("Activo")
        last_audio_time = asyncio.get_event_loop().time()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE) as stream:
            while audio_task_running.is_set():
                data, _ = stream.read(SAMPLE_RATE // 10)
                if np.any(data):
                    try:
                        push_stream.write(data.tobytes())
                    except Exception as e:
                        print("Error escribiendo al PushAudioInputStream:", e)
                    last_audio_time = asyncio.get_event_loop().time()
                elif asyncio.get_event_loop().time() - last_audio_time > timeout:
                    print("⏰ Timeout de audio, deteniendo.")
                    audio_task_running.clear()
                    break
                await asyncio.sleep(0.01)
    except Exception as e:
        print("❌ Error en captura/recognizer:", e)
        await broadcast_status("Error")
    finally:
        try:
            push_stream.close()
        except Exception:
            pass
        try:
            recognizer.stop_continuous_recognition()
        except Exception:
            pass
        print("⚠️ Tarea de procesamiento finalizada.")
        await broadcast_status("Detenido")
        audio_processing_task_handle = None


async def broadcast_translations():
    global broadcast_translations_task_handle, CURRENT_TARGET_LANG
    print("[DEBUG] Tarea de broadcast iniciada.")
    while True:
        text = await translation_queue.get()
        if text is None:
            print("[DEBUG] Señal para terminar broadcast recibida.")
            break

        try:
            await save_translation_to_db(text)
            await broadcast_status("Traduciendo")

            voice = VOICE_MAP.get(CURRENT_TARGET_LANG, VOICE_MAP.get("en"))
            speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
            speech_config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm
            )
            speech_config.speech_synthesis_voice_name = voice
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

            print(f"➡️ Sintetizando texto en '{CURRENT_TARGET_LANG}' con voz '{voice}': {text}")

            def synth_blocking():
                future = synthesizer.speak_text_async(text)
                return future.get()

            try:
                result = await asyncio.to_thread(synth_blocking)
            except Exception as e:
                print("❌ Error en síntesis:", e)
                result = None

            if result and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_buffer = result.audio_data
                audio_base64 = base64.b64encode(audio_buffer).decode("utf-8")
                payload = {"text": text, "audio": audio_base64, "status": "Enviando", "lang": CURRENT_TARGET_LANG}

                for client in list(connected_clients):
                    try:
                        await client.send_json(payload)
                    except Exception as e:
                        print("🔌 Error al enviar a cliente:", e)
                        if client in connected_clients:
                            connected_clients.remove(client)

                await broadcast_status("Activo")
            else:
                await broadcast_status("Error")
        except Exception as e:
            print("❌ Error en broadcast_translations:", e)
            await broadcast_status("Error")
        finally:
            translation_queue.task_done()

    broadcast_translations_task_handle = None
    print("[DEBUG] Tarea broadcast finalizada.")


@app.post("/stop_mic_global")
async def stop_mic_global():
    """
    Detiene la tarea global del micrófono (administrativamente).
    No importa cuántos clientes estén conectados.
    """
    global audio_task_running
    if audio_task_running.is_set():
        audio_task_running.clear()
        await translation_queue.put(None)  # señal para detener broadcast
        print("🔴 [ADMIN] Micrófono global detenido por endpoint.")
        return {"status": "detenido"}
    else:
        return {"status": "ya estaba detenido"}

async def broadcast_status(status: str):
    for client in list(connected_clients):
        try:
            await client.send_json({"status": status})
        except Exception:
            if client in connected_clients:
                connected_clients.remove(client)


@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    global audio_processing_task_handle, broadcast_translations_task_handle, CURRENT_TARGET_LANG
    await ws.accept()
    connected_clients.add(ws)
    print(f"✅ Nuevo cliente conectado: {ws.client}. Total: {len(connected_clients)}")

    # Enviar estado inicial del micrófono
    await ws.send_json({"status": "Activo" if audio_task_running.is_set() else "Detenido"})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"command": raw}

            command = parsed.get("command")
            if command == "start_translation":
                requested_lang = parsed.get("lang")
                if requested_lang:
                    CURRENT_TARGET_LANG = requested_lang
                    print(f"[WS] Idioma objetivo actualizado a: {CURRENT_TARGET_LANG}")

                # Iniciar tarea global de micrófono si no está activa
                if not audio_task_running.is_set():
                    audio_task_running.set()
                    audio_processing_task_handle = asyncio.create_task(audio_processing_task())
                    broadcast_translations_task_handle = asyncio.create_task(broadcast_translations())
                    print("🟢 Comando start recibido. Tareas iniciadas.")
                else:
                    print("⚠️ Start recibido pero ya está en ejecución.")
                await ws.send_json({"status": "Activo"})

            elif command == "stop_translation":
                # Solo actualizar estado del cliente, no detener el micrófono global
                print(f"🔴 Cliente {ws.client} finalizó traducción (no se detiene micrófono global).")
                await ws.send_json({"status": "Detenido"})

            else:
                print("[WS] Comando no reconocido:", parsed)

    except WebSocketDisconnect:
        print(f"⚠️ Cliente {ws.client} se desconectó.")
    finally:
        if ws in connected_clients:
            connected_clients.remove(ws)
        print(f"Cliente desconectado. Restan: {len(connected_clients)}")

# Endpoint para la interfaz web
@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Vortex Live Translation</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f6f8; color: #333;
                    display: flex; flex-direction: column; justify-content: center; align-items: center; 
                    height: 100vh; margin: 0; }
            .container { text-align: center; background: white; padding: 2rem 3rem; border-radius: 12px;
                            box-shadow: 0 4px 12px rgba(0,0,0,0.1); width: 80%; max-width: 600px; }
            h1 { color: #0078D7; }
            .status-line { display: flex; align-items: center; justify-content: center; margin-bottom: 1rem; }
            .status-dot { height: 20px; width: 20px; border-radius: 50%; margin-right: 10px; }
            .dot-red { background-color: #e74c3c; }
            .dot-green { background-color: #2ecc71; }
            .bar-container { height: 10px; width: 100%; background: #eee; border-radius: 5px; overflow: hidden; margin-top: 1rem; }
            .bar { height: 100%; background: #0078D7; transition: width 0.1s; }
            #translation-display { margin-top: 1.5rem; padding: 1rem; border: 1px solid #ddd;
                                    border-radius: 8px; min-height: 100px; text-align: left; background-color: #f9f9f9; white-space: pre-wrap; }
            .button { padding: 10px 20px; font-size: 16px; cursor: pointer; border: none; border-radius: 5px; }
            .start-button { background-color: #2ecc71; color: white; }
            .stop-button { background-color: #e74c3c; color: white; margin-left: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🌐 Traducción en Vivo (Servidor)</h1>
            <div class="status-line">
                <div id="status-dot" class="status-dot dot-red"></div>
                <p><strong>Estado:</strong> <span id="status">Desconectado</span></p>
            </div>
            <div id="controls">
                <button class="button start-button" onclick="startTranslation()">Iniciar Traducción</button>
                <button class="button stop-button" onclick="stopTranslation()">Terminar Traducción</button>
                <button class="button stop-button" onclick="stopMicGlobal()">Detener Micrófono Global</button>
            </div>
            <div class="bar-container">
                <div id="audio-bar" class="bar"></div>
            </div>
            <div id="translation-display">No hay traducciones aún.</div>
        </div>
        <script>
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

            function startTranslation() {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send("start_translation");
                    updateStatus('Activo');
                    return;
                }
                
                const url = `ws://${window.location.host}/ws/live`;
                ws = new WebSocket(url);
                ws.onopen = () => {
                    console.log("Conectado al WebSocket.");
                    ws.send("start_translation");
                };

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    
                    if (data.status) {
                        updateStatus(data.status);
                    }
                    
                    if (data.text) {
                        console.log("Traducción recibida:", data.text);
                        document.getElementById('translation-display').innerText = data.text;
                        
                        const audioBlob = new Blob([new Uint8Array(data.audio)], { type: 'audio/wav' });
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
                };
                ws.onerror = (error) => {
                    console.error("Error en WebSocket:", error);
                };
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

            function stopTranslation() {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send("stop_translation");
                }
            }
        </script>
    </body>
    </html>
    """
    
if __name__ == "__main__":  
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)    
