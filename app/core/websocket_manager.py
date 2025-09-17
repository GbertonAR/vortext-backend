from typing import Dict
from fastapi import WebSocket
from .translator import Translator

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, WebSocket] = {}
        self.translator = Translator()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active[id(ws)] = ws

    async def disconnect(self, ws: WebSocket):
        self.active.pop(id(ws), None)

    async def handle_message(self, ws: WebSocket, message: dict):
        msg_type = message.get("type")
        if msg_type == "text":
            src = message.get("text")
            target_lang = message.get("target","en")
            translated = await self.translator.translate_text(src, target_lang)
            return {"type":"translation", "original": src, "translated": translated}
        elif msg_type == "audio_chunk":
            # optional: stream audio to speech->text then translate
            # For demo we assume client sends text only
            pass
        return None
