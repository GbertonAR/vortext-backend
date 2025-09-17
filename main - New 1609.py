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
from typing import Optional

# Cargar .env
load_dotenv()

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

if not SPEECH_KEY or not SPEECH_REGION:
    raise ValueError("Las variables de entorno SPEECH_KEY y SPEECH_REGION no están configuradas.")

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cola que lleva textos listos para sintetizar y enviar
translation_queue = asyncio.Queue()

connected_clients = set()

audio_task_running = asyncio.Event()
audio_processing_task_handle = None
broadcast_translations_task_handle = None

# Voz por idioma (mapa). Ajustá si querés otras voces de Azure.
VOICE_MAP = {
    "es": "es-ES-ElviraNeural",
    "en": "en-US-JennyNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
}

# Valor por defecto (si cliente no envía)
CURRENT_TARGET_LANG = "es"

# Config base (speech recognizer source language default)
# Vamos a crear objetos específicos cuando arranque la tarea
translation_config_base = speechsdk.translation.SpeechTranslationConfig(
    subscription=SPEECH_KEY,
    region=SPEECH_REGION
)

# Config para sintetizador (se clonará/ajustará por idioma)
speech_config_base = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
# Nos aseguremos que el formato de salida sea compatible (WAV PCM).
speech_config_base.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm)


async def save_translation_to_db(text: str):
    # placeholder - en producción cambiar por llamada real a la DB
    print(f"💾 Guardando en la base de datos: {text}")
    await asyncio.sleep(0.05)


async def audio_processing_task(timeout: int = 180):
    """
    Captura audio del micrófono del servidor, alimenta al recognizer y
    publica traducciones a translation_queue. Usa CURRENT_TARGET_LANG para
    escoger la traducción a enviar.
    """
    global audio_processing_task_handle, CURRENT_TARGET_LANG
    print("Iniciando tarea de procesamiento de audio...")

    loop = asyncio.get_running_loop()

    # Crear PushAudioInputStream y recognizer localmente para poder setear idiomas dinámicamente
    push_stream = speechsdk.audio.PushAudioInputStream()
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    # Clonar la config base para setear source/targets
    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY,
        region=SPEECH_REGION
    )

    # Config de reconocimiento: asumimos entrada en inglés por defecto, pero podés ajustar
    translation_config.speech_recognition_language = "en-US"
    # Aseguramos el target pedido (CURRENT_TARGET_LANG)
    translation_config.target_languages.clear()  # si existe la API
    translation_config.add_target_language(CURRENT_TARGET_LANG)

    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config,
        audio_config=audio_config
    )

    def recognized_cb(evt):
        # evt.result.translations es un dict: lang_code -> translation_text
        try:
            text = evt.result.translations.get(CURRENT_TARGET_LANG, "")
        except Exception:
            text = ""
        if text:
            print(f"✅ Traducción reconocida ({CURRENT_TARGET_LANG}): {text}")
            loop.call_soon_threadsafe(translation_queue.put_nowait, text)

    def canceled_cb(evt):
        print("Recognizer canceled:", evt)

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
            # detener reconocimiento de forma asíncrona
            recognizer.stop_continuous_recognition()
        except Exception:
            pass
        print("⚠️ Tarea de procesamiento finalizada.")
        await broadcast_status("Detenido")
        audio_processing_task_handle = None


async def broadcast_translations():
    """
    Lee de translation_queue y sintetiza audio (en CURRENT_TARGET_LANG),
    convierte a base64 y lo envía a todos los clientes conectados.
    """
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

            # elegir voz según CURRENT_TARGET_LANG
            voice = VOICE_MAP.get(CURRENT_TARGET_LANG, VOICE_MAP.get("en"))

            # Crear una config de síntesis local con la voz correcta
            speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
            speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm)
            speech_config.speech_synthesis_voice_name = voice

            # Crear sintetizador local
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

            print(f"➡️ Sintetizando texto en '{CURRENT_TARGET_LANG}' con voz '{voice}': {text}")

            # Ejecutar la síntesis en hilo para no bloquear el event loop
            def synth_blocking():
                future = synthesizer.speak_text_async(text)
                return future.get()

            try:
                result = await asyncio.to_thread(synth_blocking)
            except Exception as e:
                print("❌ Error en síntesis (to_thread):", e)
                result = None

            if result and result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_buffer = result.audio_data  # bytes
                audio_base64 = base64.b64encode(audio_buffer).decode("utf-8")

                payload = {"text": text, "audio": audio_base64, "status": "Enviando", "lang": CURRENT_TARGET_LANG}

                # enviar a todos los clientes
                for client in list(connected_clients):
                    try:
                        await client.send_json(payload)
                    except Exception as e:
                        print("🔌 Error al enviar a cliente, eliminando:", e)
                        if client in connected_clients:
                            connected_clients.remove(client)

                await broadcast_status("Activo")
            else:
                # error o cancelación
                if result is not None:
                    try:
                        cancellation_details = result.cancellation_details
                        print("❌ Síntesis cancelada:", cancellation_details.reason)
                    except Exception:
                        pass
                await broadcast_status("Error")
        except Exception as e:
            print("❌ Error en broadcast_translations:", e)
            await broadcast_status("Error")
        finally:
            translation_queue.task_done()

    broadcast_translations_task_handle = None
    print("[DEBUG] Tarea broadcast finalizada.")


async def broadcast_status(status: str):
    for client in list(connected_clients):
        try:
            await client.send_json({"status": status})
        except Exception as e:
            print("Error enviando status a cliente:", e)
            if client in connected_clients:
                connected_clients.remove(client)


@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket handler: acepta comandos JSON:
      - { command: "start_translation", lang: "es" }
      - { command: "stop_translation" }
    """
    global audio_processing_task_handle, broadcast_translations_task_handle, CURRENT_TARGET_LANG
    await ws.accept()
    connected_clients.add(ws)
    print(f"✅ Nuevo cliente conectado: {ws.client}. Total: {len(connected_clients)}")

    # enviar estado inicial
    if audio_task_running.is_set():
        await ws.send_json({"status": "Activo"})
    else:
        await ws.send_json({"status": "Detenido"})

    try:
        while True:
            raw = await ws.receive_text()
            # el cliente manda JSON; intentamos parsear
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"command": raw}

            command = parsed.get("command")
            if command == "start_translation":
                # si envian lang, actualizamos
                requested_lang = parsed.get("lang")
                if requested_lang:
                    CURRENT_TARGET_LANG = requested_lang
                    print(f"[WS] Idioma objetivo actualizado a: {CURRENT_TARGET_LANG}")

                if not audio_task_running.is_set():
                    audio_task_running.set()
                    # iniciar tareas
                    audio_processing_task_handle = asyncio.create_task(audio_processing_task())
                    broadcast_translations_task_handle = asyncio.create_task(broadcast_translations())
                    print("🟢 Comando start recibido. Tareas iniciadas.")
                else:
                    print("⚠️ Start recibido pero la tarea de audio ya está en ejecución.")
                await ws.send_json({"status": "Activo"})
            elif command == "stop_translation":
                if audio_task_running.is_set():
                    audio_task_running.clear()
                    # signal stop to broadcast
                    await translation_queue.put(None)
                    print("🔴 Comando stop recibido. Tareas detenidas.")
                await ws.send_json({"status": "Detenido"})
            else:
                print("[WS] Comando no reconocido:", parsed)
    except WebSocketDisconnect:
        print(f"⚠️ Cliente {ws.client} se desconectó.")
    finally:
        if ws in connected_clients:
            connected_clients.remove(ws)
        print(f"Cliente desconectado. Clientes restantes: {len(connected_clients)}")
        # si no quedan clientes, detener tareas
        if not connected_clients and audio_task_running.is_set():
            audio_task_running.clear()
            await translation_queue.put(None)
            print("Último cliente desconectado: deteniendo tareas.")


# Endpoint web simple para comprobar servidor
@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <title>Vortex Live Translation - Server</title>
      </head>
      <body>
        <h2>Vortex Live Translation - Backend</h2>
        <p>Servidor en ejecución.</p>
      </body>
    </html>
    """


if __name__ == "__main__":  
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
    
# 5) Pruebas a realizar (manual checklist)

# Para verificar que todo funcione localmente:

# Frontend

# Levantá el frontend (npm run dev) y abrí la app.

# Seleccioná idioma (ej. Español).

# Pulsá Iniciar Traducción → el botón debe pasar a “Terminar Traducción” y el select debe quedar deshabilitado.

# Pulsá 🔇 Audio OFF durante la reproducción: la reproducción actual debe detenerse inmediatamente y la cola vaciarse.

# Pulsá Terminar Traducción → el estado debe pasar a Detenido y el select volver a habilitarse.

# Backend

# Levantá FastAPI con uvicorn main:app --reload (o el comando que uses).

# Asegurate de que SPEECH_KEY y SPEECH_REGION estén en tu .env.

# Iniciá la traducción desde la UI con idioma fr/de/en/es y verificá logs: el backend debe imprimir Idioma objetivo actualizado a: xx y luego las síntesis con la voz mapeada.

# Verificá en frontend que el audio y texto llegan correctamente.

# 6) Notas y limitaciones / recomendaciones finales

# Actualmente el backend mantiene un único idioma objetivo global (CURRENT_TARGET_LANG) para todos los clientes. Si en el futuro querés dar soporte a múltiples clientes con idiomas distintos de forma concurrente (cada cliente escucha su propia traducción), habría que crear arquitecturas por sesión (crear recognizer + synth por cliente o realizar traducciones separadas por cliente) — esto implica mayor uso de recursos en el servidor.

# Los nombres de voces en VOICE_MAP pueden ajustarse según las voces que tengas disponibles en tu suscripción de Azure. Si una voz solicitada no está disponible se usa en-US-JennyNeural como fallback.

# Si la latencia o concurrencia es crítica, recomiendo: 1) usar cola persistente y workers, 2) limitar el número de sintetizadores simultáneos y 3) agregar monitoreo.

# Si querés, hago a continuación (elige una):

# A) Ajusto VOICE_MAP con las voces exactas que querés / las que permite tu subscription (si me pasás listado).

# B) Hago la variante “cada cliente con su propio idioma” (arquitectura por sesión) y te entrego la versión completa (esto requiere más trabajo y consumo de recursos, pero es posible).

# C) Te preparo un script de pruebas (postman + cliente JS) que simula mensajes y te ayuda a validar sin usar micrófono.

# ¿Con cuál seguimos?    