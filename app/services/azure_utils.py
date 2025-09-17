# File: app/services/azure_utils.py

import azure.cognitiveservices.speech as speechsdk
import requests
from app.core.config import settings


# -------------------------------
# 1. Speech-to-Text
# -------------------------------
async def speech_to_text(audio_bytes: bytes):
    """Convierte audio en texto usando Azure Speech-to-Text."""
    try:
        # Configuración del servicio
        speech_config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY,
            region=settings.AZURE_REGION
        )
        # Detecta automáticamente el idioma (español/inglés)
        auto_detect = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(
            languages=["en-US", "es-ES"]
        )

        # Cargar audio desde memoria
        audio_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        audio_input = speechsdk.audio.PushAudioInputStream(audio_format)
        audio_input.write(audio_bytes)
        audio_input.close()
        audio_config = speechsdk.audio.AudioConfig(stream=audio_input)

        # Reconocimiento
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
            auto_detect_source_language_config=auto_detect
        )
        result = recognizer.recognize_once()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            detected_lang = result.properties.get(
                speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult
            )
            return result.text, detected_lang
        else:
            raise Exception(f"Recognition failed: {result.reason}")
    except Exception as e:
        raise Exception(f"Azure Speech-to-Text error: {e}")


# -------------------------------
# 2. Translator
# -------------------------------
async def translate_text(text: str, target_lang: str):
    """Traduce texto usando Azure Translator."""
    try:
        endpoint = settings.AZURE_TRANSLATOR_ENDPOINT
        path = "/translate?api-version=3.0"
        params = f"&to={target_lang}"
        constructed_url = endpoint + path + params

        headers = {
            "Ocp-Apim-Subscription-Key": settings.AZURE_TRANSLATOR_KEY,
            "Ocp-Apim-Subscription-Region": settings.AZURE_REGION,
            "Content-type": "application/json"
        }

        body = [{"text": text}]
        response = requests.post(constructed_url, headers=headers, json=body)
        response.raise_for_status()
        result = response.json()

        return result[0]["translations"][0]["text"]
    except Exception as e:
        raise Exception(f"Azure Translator error: {e}")


# -------------------------------
# 3. Text-to-Speech
# -------------------------------
async def text_to_speech(text: str, lang: str):
    """Convierte texto en audio usando Azure Text-to-Speech (devuelve bytes WAV)."""
    try:
        speech_config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY,
            region=settings.AZURE_REGION
        )

        # Seleccionar voz según idioma
        if lang.startswith("es"):
            speech_config.speech_synthesis_voice_name = "es-ES-AlvaroNeural"
        else:
            speech_config.speech_synthesis_voice_name = "en-US-GuyNeural"

        audio_output = speechsdk.audio.AudioOutputConfig(use_default_speaker=False)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

        result = synthesizer.speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data
        else:
            raise Exception(f"Speech synthesis failed: {result.reason}")
    except Exception as e:
        raise Exception(f"Azure Text-to-Speech error: {e}")
