import azure.cognitiveservices.speech as speechsdk

SPEECH_KEY = "1zZVViJwiJk8BAB97wLRQAwWRk8VGMsWp84I1TG77C6tUUqazbTBJQQJ99BIACHYHv6XJ3w3AAAEACOGVnMc"
SPEECH_REGION = "eastus2"

class SpeechService:
    def __init__(self):
        self.speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
        self.speech_config.speech_recognition_language = "en-US"

    async def recognize_and_translate(self, audio_bytes, target_lang="es"):
        """
        Recibe audio en bytes y devuelve texto traducido + audio TTS en bytes
        """
        # Configuración traducción
        translation_config = speechsdk.translation.SpeechTranslationConfig(
            subscription=SPEECH_KEY,
            region=SPEECH_REGION
        )
        translation_config.speech_recognition_language = "en-US"
        translation_config.add_target_language(target_lang)

        # Configurar flujo de audio
        audio_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=audio_stream)

        recognizer = speechsdk.translation.TranslationRecognizer(
            translation_config=translation_config,
            audio_config=audio_config
        )

        # Push del audio
        audio_stream.write(audio_bytes)
        audio_stream.close()

        # Reconocer una vez
        result = recognizer.recognize_once_async().get()

        translated_text = ""
        tts_bytes = b""

        print("Result {translated_text}".format(
            translated_text=result.text
            ))



        if result.reason == speechsdk.ResultReason.TranslatedSpeech:
            translated_text = list(result.translations.values())[0]

            # Generar TTS
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=self.speech_config, audio_config=None)
            tts_result = synthesizer.speak_text_async(translated_text).get()
            tts_bytes = tts_result.audio_data

        return translated_text, tts_bytes

speech_service = SpeechService()
