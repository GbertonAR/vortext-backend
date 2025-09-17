# backend/app/services/speech_service.py
import asyncio
import base64
import threading
import azure.cognitiveservices.speech as speechsdk
from typing import Optional

SPEECH_KEY = None
SPEECH_REGION = None

def init_azure(key: str, region: str):
    global SPEECH_KEY, SPEECH_REGION
    SPEECH_KEY = key
    SPEECH_REGION = region

class SpeechSession:
    """
    Manage one session: PushAudioInputStream + TranslationRecognizer.
    Events send JSON to websocket via provided send_json coroutine.
    """
    def __init__(self, send_json_coro, source_lang="en-US", target_lang="es"):
        if SPEECH_KEY is None:
            raise RuntimeError("Azure keys not initialized. Call init_azure() first.")
        self.send_json = send_json_coro  # async function to send JSON to client
        self.source_lang = source_lang
        self.target_lang = target_lang

        # create push stream with required format - we'll set format on creation
        self.push_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=SPEECH_KEY, region=SPEECH_REGION
        )
        translation_config.speech_recognition_language = self.source_lang
        translation_config.add_target_language(self.target_lang)

        self.recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config
        )

        # Attach events
        self.recognizer.recognizing.connect(self._on_recognizing)
        self.recognizer.recognized.connect(self._on_recognized)
        self.recognizer.canceled.connect(self._on_canceled)
        # optional: synthesizing (if using speech synthesis with events)

        self._loop = asyncio.get_event_loop()
        self._running = False
        self._thread = None

    def start(self):
        """Start continuous recognition in a separate thread (non-blocking)."""
        if self._running:
            return
        self._running = True
        def target():
            try:
                # start continuous recognition (blocks until stop called)
                self.recognizer.start_continuous_recognition()
            except Exception as e:
                # send error back
                asyncio.run_coroutine_threadsafe(self.send_json({"error": str(e)}), self._loop)
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop recognition and close stream."""
        if not self._running:
            return
        try:
            self.recognizer.stop_continuous_recognition()
        except Exception:
            pass
        self._running = False
        try:
            self.push_stream.close()
        except Exception:
            pass

    def push_audio(self, pcm_bytes: bytes):
        """
        Push raw PCM bytes into the stream. Expect PCM 16-bit little-endian.
        """
        # push may be called frequently from async loop
        try:
            self.push_stream.write(pcm_bytes)
        except Exception as e:
            # if stream closed, ignore
            pass

    # Event handlers - these run in azure SDK thread, so schedule to asyncio loop
    def _on_recognizing(self, evt):
        # partial recognition - evt.result.text and evt.result.translations
        try:
            text = evt.result.text
            translations = evt.result.translations
            # Build payload
            payload = {
                "type": "recognizing",
                "original": text,
                "translations": translations  # dict lang->text
            }
            asyncio.run_coroutine_threadsafe(self.send_json(payload), self._loop)
        except Exception:
            pass

    def _on_recognized(self, evt):
        try:
            result = evt.result
            if result.reason == speechsdk.ResultReason.TranslatedSpeech:
                original = result.text
                translations = result.translations
                # We will synthesize TTS for the first target language
                tts_audio_b64 = None
                try:
                    # Synthesize using speech synthesizer (synchronous)
                    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
                    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
                    target_text = list(translations.values())[0]
                    sres = synthesizer.speak_text_async(target_text).get()
                    if sres.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                        audio_bytes = sres.audio_data
                        tts_audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                except Exception as e:
                    tts_audio_b64 = None

                payload = {
                    "type": "recognized",
                    "original": original,
                    "translations": translations,
                    "audio_b64": tts_audio_b64
                }
                asyncio.run_coroutine_threadsafe(self.send_json(payload), self._loop)
            elif result.reason == speechsdk.ResultReason.RecognizedSpeech:
                payload = {
                    "type": "recognized",
                    "original": result.text,
                    "translations": {},
                    "audio_b64": None
                }
                asyncio.run_coroutine_threadsafe(self.send_json(payload), self._loop)
            elif result.reason == speechsdk.ResultReason.NoMatch:
                payload = { "type": "nomatch" }
                asyncio.run_coroutine_threadsafe(self.send_json(payload), self._loop)
        except Exception:
            pass

    def _on_canceled(self, evt):
        payload = {
            "type": "canceled",
            "reason": str(evt.reason),
            "details": getattr(evt, "error_details", None)
        }
        asyncio.run_coroutine_threadsafe(self.send_json(payload), self._loop)
