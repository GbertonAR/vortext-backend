from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import azure.cognitiveservices.speech as speechsdk
import os
import base64
from dotenv import load_dotenv
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

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

# Archivos estáticos (para servir el JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

translation_queue = asyncio.Queue()
audio_queue = asyncio.Queue()
connected_clients = set()
audio_task_running = asyncio.Event()

VOICE_MAP = {
    "es": "es-ES-ElviraNeural",
    "en": "en-US-JennyNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "pt": "pt-PT-FernandaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}

CURRENT_TARGET_LANG = "es"
CURRENT_INPUT_LANG = "en-US"

async def save_translation_to_db(text: str):
    logger.info(f"💾 Guardando en la base de datos: {text}")
    await asyncio.sleep(0.05)


async def audio_translation_task():
    """
    Recibe audio de la cola (proveniente del WebSocket del cliente),
    lo envía a Azure STT+Translation y publica traducciones en la cola de traducciones.
    """
    global CURRENT_TARGET_LANG, CURRENT_INPUT_LANG
    logger.info("Iniciando tarea de traducción de audio...")

    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=SAMPLE_RATE, bits_per_sample=16, channels=CHANNELS
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY, region=SPEECH_REGION
    )
    translation_config.speech_recognition_language = CURRENT_INPUT_LANG
    translation_config.add_target_language(CURRENT_TARGET_LANG)

    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config, audio_config=audio_config
    )

    def recognizing_cb(evt):
        if evt.result.text:
            logger.info(f"[Parcial]: {evt.result.text}")
            
    def recognized_cb(evt):
        try:
            text = evt.result.translations.get(CURRENT_TARGET_LANG, "")
        except Exception:
            text = ""
        if text:
            logger.info(f"✅ Traducción reconocida ({CURRENT_TARGET_LANG}): {text}")
            asyncio.create_task(translation_queue.put(text))

    def canceled_cb(evt):
        logger.info("Recognizer canceled:", evt)

    recognizer.recognizing.connect(recognizing_cb)
    recognizer.recognized.connect(recognized_cb)
    recognizer.canceled.connect(canceled_cb)

    recognizer.start_continuous_recognition()
    
    try:
        logger.info("📡 Escuchando flujo de audio del cliente...")
        await broadcast_status("Activo")
        while audio_task_running.is_set():
            audio_data = await audio_queue.get()
            if audio_data is None:
                continue
            
            push_stream.write(audio_data)
            audio_queue.task_done()
            
    except Exception as e:
        logger.error("❌ Error en el procesador de audio:", e)
        await broadcast_status("Error")
    finally:
        push_stream.close()
        recognizer.stop_continuous_recognition()
        logger.info("⚠️ Tarea de traducción de audio finalizada.")
        await broadcast_status("Detenido")

async def broadcast_translations():
    logger.info("[DEBUG] Tarea de broadcast iniciada.")
    while True:
        text = await translation_queue.get()
        if text is None:
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

            def synth_blocking():
                future = synthesizer.speak_text_async(text)
                return future.get()

            result = await asyncio.to_thread(synth_blocking)

            if result and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_buffer = result.audio_data
                audio_base64 = base64.b64encode(audio_buffer).decode("utf-8")
                payload = {"text": text, "audio": audio_base64, "status": "Enviando", "lang": CURRENT_TARGET_LANG}
                for client in list(connected_clients):
                    try:
                        await client.send_json(payload)
                    except Exception as e:
                        connected_clients.remove(client)
                await broadcast_status("Activo")
        except Exception as e:
            logger.error("❌ Error en broadcast_translations:", e)
            await broadcast_status("Error")
        finally:
            translation_queue.task_done()
            
    logger.info("[DEBUG] Tarea broadcast finalizada.")

async def broadcast_status(status: str, level: int = 0):
    for client in list(connected_clients):
        try:
            await client.send_json({"status": status, "level": level})
        except Exception:
            connected_clients.remove(client)

@app.websocket("/ws/audio")
async def audio_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("✅ Cliente de audio conectado.")
    try:
        while True:
            audio_bytes = await websocket.receive_bytes()
            await audio_queue.put(audio_bytes)
    except WebSocketDisconnect:
        logger.info("⚠️ Cliente de audio desconectado.")

@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    global audio_processing_task_handle
    global CURRENT_TARGET_LANG, CURRENT_INPUT_LANG
    await ws.accept()
    connected_clients.add(ws)
    logger.info(f"✅ Nuevo cliente de control conectado: {ws.client}. Total: {len(connected_clients)}")

    await ws.send_json({"status": "Activo" if audio_task_running.is_set() else "Detenido"})

    try:
        while True:
            raw = await ws.receive_text()
            parsed = json.loads(raw) if raw.startswith("{") else {"command": raw}
            command = parsed.get("command")

            if command == "start_translation":
                requested_lang = parsed.get("lang")
                input_lang = parsed.get("input_lang")
                if requested_lang:
                    CURRENT_TARGET_LANG = requested_lang
                if input_lang:
                    CURRENT_INPUT_LANG = input_lang
                if not audio_task_running.is_set():
                    audio_task_running.set()
                    asyncio.create_task(audio_translation_task())
                    asyncio.create_task(broadcast_translations())
                await ws.send_json({"status": "Activo"})

            elif command == "stop_translation":
                logger.info(f"🔴 Cliente {ws.client} finalizó traducción.")
                await ws.send_json({"status": "Detenido"})

    except WebSocketDisconnect:
        logger.info(f"⚠️ Cliente de control {ws.client} se desconectó.")
    finally:
        connected_clients.remove(ws)
        logger.info(f"Cliente de control desconectado. Restan: {len(connected_clients)}")

# Endpoint web
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
            .stop-button { background-color: #e74c3c; color: white; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🌐 Traducción en Vivo (Servidor)</h1>
            <div class="status-line">
                <div id="status-dot" class="status-dot dot-red"></div>
                <p><strong>Estado:</strong> <span id="status">Desconectado</span></p>
            </div>
            <label for="input-language">Idioma del orador:</label>
            <select id="input-language">
              <option value="en-US">Inglés</option>
              <option value="es-ES">Español</option>
              <option value="fr-FR">Francés</option>
              <option value="de-DE">Alemán</option>
              <option value="pt-PT">Portugués</option>
              <option value="zh-CN">Chino</option>
            </select>
            <button class="button start-button" id="toggle-translation" onclick="toggleTranslation()">
              Iniciar Traducción
            </button>
            <div class="bar-container">
                <div id="audio-bar" class="bar"></div>
            </div>
            <div id="translation-display">No hay traducciones aún.</div>
        </div>
        <script src="/static/app.js"></script>
    </body>
    </html>
    """
    
if __name__ == "__main__":
    try:
        import uvicorn
    except ImportError:
        logger.error('uvicorn no está instalado. Instale uvicorn con: pip install uvicorn')
        raise
    uvicorn.run(app, host="0.0.0.0", port=8000)