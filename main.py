# main.py

import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from threading import Thread

import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech import speech

app = FastAPI()

# Mapeo de oyentes por idioma
listeners = {}

# Cargar .env
load_dotenv()

# Variables de entorno para las credenciales de Azure
SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

# Configuración del servicio de voz de Azure
# speech_config = speechsdk.SpeechTranslationConfig(
#     subscription=SPEECH_KEY,
#     region=SPEECH_REGION
# )

speech_translation_config = speechsdk.translation.SpeechTranslationConfig(
    subscription=os.getenv("SPEECH_KEY"),
    region=os.getenv("SPEECH_REGION")
)

# speech_translation_config.set_property(
#     speechsdk.PropertyId.SpeechServiceConnection_SingleLanguageTranslation, "true"
# )
speech_translation_config.set_property(
    speechsdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "500"
)

# Endpoint para el orador
@app.websocket("/ws/speaker")
async def websocket_speaker(websocket: WebSocket):
    await websocket.accept()
    print("Orador conectado.")

    try:
        # Configurar idiomas de traducción (se puede hacer dinámico)
        target_languages = ["es", "en", "fr", "it", "de", "pt"] 
        for lang in target_languages:
            speech_config.add_target_language(lang)

        # Crear un PushStream para alimentar el audio a Azure
        push_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        translator = speechsdk.translation.TranslationRecognizer(
            translation_config=speech_config, 
            audio_config=audio_config
        )

        # Función para enviar las traducciones a los oyentes
        def send_translation_to_listeners(event):
            original_text = event.result.text
            translations = event.result.translations
            
            # Iterar sobre los idiomas y enviar a los oyentes correspondientes
            for lang, translated_text in translations.items():
                if lang in listeners:
                    message = {
                        "original_text": original_text,
                        "translated_text": translated_text,
                        "audio_url": ""  # Opcional: Lógica para TTS aquí si es necesaria
                    }
                    # Usamos asyncio.run_coroutine_threadsafe para enviar de forma segura
                    # desde el hilo de Azure al loop de FastAPI.
                    loop = asyncio.get_event_loop()
                    for client in listeners[lang]:
                        asyncio.run_coroutine_threadsafe(
                            client.send_json(message), loop)
                        
        # Conectar el evento de traducción con nuestra función de envío
        translator.translation_received.connect(send_translation_to_listeners)
        
        # Iniciar el reconocimiento continuo en un hilo separado para no bloquear FastAPI
        translation_thread = Thread(target=lambda: translator.start_continuous_recognition_async().get())
        translation_thread.start()

        try:
            while True:
                audio_data = await websocket.receive_bytes()
                if audio_data:
                    # Escribir el audio en el PushStream para que Azure lo procese
                    push_stream.write(audio_data)
        finally:
            # Asegurarse de detener el reconocimiento al salir del bucle
            translator.stop_continuous_recognition_async().get()

    except WebSocketDisconnect:
        print("Orador desconectado.")

# Endpoint para los oyentes
@app.websocket("/ws/listener")
async def websocket_listener(websocket: WebSocket, lang: str):
    await websocket.accept()

    # Validar idioma y agregar al mapeo de oyentes
    if lang not in listeners:
        listeners[lang] = []
    listeners[lang].append(websocket)
    
    print(f"Oyente conectado. Idioma: {lang}. Total oyentes: {len(listeners[lang])}")

    try:
        while True:
            # Mantener la conexión abierta
            await websocket.receive_text()
    except WebSocketDisconnect:
        # Eliminar al oyente de la lista al desconectarse
        if websocket in listeners[lang]:
            listeners[lang].remove(websocket)
            print(f"Oyente desconectado. Idioma: {lang}. Oyentes restantes: {len(listeners[lang])}")
            if not listeners[lang]:
                del listeners[lang]
                print(f"No quedan oyentes para el idioma {lang}. Eliminando la clave.")
                
if __name__ == "__main__":  
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)             