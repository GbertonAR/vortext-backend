import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from threading import Thread
from fastapi.middleware.cors import CORSMiddleware

import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech import speech

import io

app = FastAPI()

# Añadir CORS
origins = [
    "http://localhost",
    "http://localhost:5173", # Reemplaza con la URL de tu frontend en producción
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mapeo de oyentes por idioma
listeners = {}

# Variables de estado globales
is_processing = False
input_lang = "en-US"  # Idioma del orador por defecto
storage_method = "NO_RECORD"

# Cargar .env
print("Cargando variables de entorno desde .env...")
load_dotenv()
print("Variables de entorno cargadas.")

# Variables de entorno para las credenciales de Azure
SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

# Log para verificar las variables de entorno
print(f"AZURE_SPEECH_KEY cargada: {'Sí' if SPEECH_KEY else 'No'}")
print(f"AZURE_SPEECH_REGION cargada: {'Sí' if SPEECH_REGION else 'No'}")

# Configuración del servicio de voz de Azure
try:
    speech_translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY,
        region=SPEECH_REGION
    )
    print("Configuración de Azure SpeechTranslationConfig exitosa.")
except Exception as e:
    print(f"ERROR: No se pudo configurar Azure Speech. Revisa tus credenciales. {e}")

speech_translation_config.set_property(
    speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "500"
)

# Endpoint para el orador
@app.websocket("/ws/speaker")
async def websocket_speaker(websocket: WebSocket):
    await websocket.accept()
    print("✨ Orador conectado.")

    # Variables globales (considera pasarlas como parámetros en el futuro)
    global is_processing, input_lang, storage_method, listeners, SPEECH_KEY, SPEECH_REGION

    if not is_processing:
        await websocket.send_text("Servicio de traducción no iniciado.")
        await websocket.close()
        return

    # ✅ Configurar el formato de audio solo UNA vez
    audio_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000,
        bits_per_sample=16,
        channels=1
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

    # ✅ Configurar SpeechTranslationConfig
    speech_translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY,
        region=SPEECH_REGION
    )

    # Configurar idioma de reconocimiento
    speech_translation_config.speech_recognition_language = input_lang
    print(f"Idioma del orador configurado: {input_lang}")

    # Configurar idiomas de traducción
    target_languages = ["es", "en", "fr", "it", "de", "pt"]
    for lang in target_languages:
        speech_translation_config.add_target_language(lang)

    # ✅ Crear TranslationRecognizer
    translator = speechsdk.translation.TranslationRecognizer(
        translation_config=speech_translation_config,
        audio_config=audio_config
    )

    # ✅ Obtener loop actual para callbacks
    loop = asyncio.get_running_loop()

    # ✅ Función para enviar traducciones a los oyentes
    def send_translation_to_listeners(result_event):
        print(f"Evento de traducción: '{result_event.result_id}'")
        print(f"Texto reconocido: '{result_event.text}'")
        print(f"Traducciones: {result_event.translations}")

        translations = result_event.translations
        original_text = result_event.text

        for lang, translated_text in translations.items():
            if lang in listeners:
                message = {
                    "original_text": original_text,
                    "translated_text": translated_text,
                    "audio_url": ""
                }
                for client in listeners[lang]:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            client.send_json(message), loop
                        )
                        print(f"Mensaje enviado a oyente en idioma {lang}.")
                    except Exception as e:
                        print(f"ERROR al enviar mensaje a oyente en {lang}: {e}")

    # ✅ Conectar eventos del traductor
    translator.recognized.connect(lambda evt: send_translation_to_listeners(evt.result))
    translator.recognizing.connect(lambda evt: print(f"Parcial: {evt.result.text}"))

    print("Iniciando reconocimiento continuo de Azure...")
    translation_thread = Thread(target=lambda: translator.start_continuous_recognition_async().get())
    translation_thread.start()
    print("Reconocimiento continuo iniciado.")

    try:
        while True:
            audio_data = await websocket.receive_bytes()
            if audio_data:
                #print(f"✅ Audio recibido del orador. Tamaño: {len(audio_data)} bytes.")

                if storage_method == "NO_RECORD":
                    push_stream.write(audio_data)
                else:
                    print(f"⚠️ Método de almacenamiento {storage_method} no implementado. Ignorando audio.")

    except WebSocketDisconnect:
        print("❌ Orador desconectado.")
    finally:
        print("Deteniendo reconocimiento continuo de Azure...")
        push_stream.close()
        translator.stop_continuous_recognition_async().get()
        translation_thread.join()
        await websocket.close()
        print("Reconocimiento continuo detenido y WebSocket cerrado.")
        
        
# Endpoint para los oyentes
@app.websocket("/ws/listener")
async def websocket_listener(websocket: WebSocket, lang: str):
    await websocket.accept()
    if lang not in listeners:
        listeners[lang] = []
    listeners[lang].append(websocket)
    print(f"👂 Oyente conectado. Idioma: {lang}. Total oyentes: {len(listeners[lang])}")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in listeners[lang]:
            listeners[lang].remove(websocket)
            print(f"🚪 Oyente desconectado. Idioma: {lang}. Oyentes restantes: {len(listeners[lang])}")
            if not listeners[lang]:
                del listeners[lang]
                print(f"🚫 No quedan oyentes para el idioma {lang}. Eliminando la clave.")

@app.get("/", response_class=HTMLResponse)
def root():
    global is_processing, input_lang, storage_method
    status = "Activo" if is_processing else "Detenido"
    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <title>Vortex Live Translation - Configuración</title>
        <style>
            body {{ font-family: Arial, sans-serif; background-color: #f4f6f8; color: #333; padding: 20px; }}
            .container {{ max-width: 600px; margin: auto; background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
            h2, h3 {{ color: #0078D7; }}
            label {{ font-weight: bold; margin-top: 10px; display: block; }}
            select, button {{ width: 100%; padding: 10px; margin-top: 5px; border-radius: 5px; border: 1px solid #ccc; }}
            button {{ background-color: #0078D7; color: white; cursor: pointer; }}
            .status {{ margin-top: 20px; padding: 10px; background-color: #e0e0e0; border-radius: 5px; }}
            .active {{ background-color: #d4edda; color: #155724; }}
            .inactive {{ background-color: #f8d7da; color: #721c24; }}
        </style>
      </head>
      <body>
        <div class="container">
          <h2>Panel de Operador - Vortex Live Translation</h2>
          <div class="status {'active' if is_processing else 'inactive'}">
            <p><strong>Estado del servicio:</strong> {status}</p>
            <p><strong>Idioma del orador:</strong> {input_lang}</p>
            <p><strong>Método de almacenamiento:</strong> {storage_method}</p>
          </div>
          <form action="/configure" method="post">
            <label for="input_lang">Idioma del orador:</label>
            <select name="input_lang" id="input_lang">
              <option value="es-ES" {'selected' if input_lang == "es-ES" else ''}>Español</option>
              <option value="en-US" {'selected' if input_lang == "en-US" else ''}>Inglés</option>
              <option value="fr-FR" {'selected' if input_lang == "fr-FR" else ''}>Francés</option>
              <option value="it-IT" {'selected' if input_lang == "it-IT" else ''}>Italiano</option>
              <option value="de-DE" {'selected' if input_lang == "de-DE" else ''}>Alemán</option>
              <option value="pt-PT" {'selected' if input_lang == "pt-PT" else ''}>Portugués</option>
            </select>
            <br>
            <label for="storage_method">Método de almacenamiento:</label>
            <select name="storage_method" id="storage_method">
              <option value="NO_RECORD" {'selected' if storage_method == "NO_RECORD" else ''}>Procesar en memoria (sin grabación)</option>
              <option value="LOCAL_RECORD" {'selected' if storage_method == "LOCAL_RECORD" else ''} disabled>Grabar localmente (no implementado)</option>
            </select>
            <br>
            <button type="submit" name="action" value="start" style="background-color: #2ecc71;">Iniciar Traducción</button>
            <button type="submit" name="action" value="stop" style="background-color: #e74c3c;">Detener Traducción</button>
          </form>
        </div>
      </body>
    </html>
    """

@app.post("/configure")
async def configure(request: Request):
    global is_processing, input_lang, storage_method
    form_data = await request.form()
    action = form_data.get("action")

    if action == "start":
        input_lang = form_data.get("input_lang")
        storage_method = form_data.get("storage_method")
        is_processing = True
        print(f"Servicio iniciado. Orador: {input_lang}, Almacenamiento: {storage_method}")
    elif action == "stop":
        is_processing = False
        print("Servicio detenido por el operador.")

    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__": 
    print("Iniciando servidor Uvicorn...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)