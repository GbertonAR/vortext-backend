import sounddevice as sd
import asyncio
import websockets
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Configuración del Cliente ---
# Reemplaza esta URL con la de tu Azure App Service
# Es crucial que uses el subdominio de Azure y la ruta del WebSocket
# Ejemplo: "wss://mi-app-traduccion.azurewebsites.net/ws/audio"
WEBSOCKET_URL = "wss://web1-translate-dcaudfhvbefacgfk.westus-01.azurewebsites.net.azurewebsites.net/ws/audio" 

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

async def audio_stream_client():
    """
    Captura audio del micrófono y lo envía al servidor a través de WebSocket.
    """
    logger.info("📡 Iniciando captura de audio del micrófono...")
    
    try:
        # Usa el contexto de Sounddevice para capturar el audio
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE) as stream:
            
            # Conéctate al servidor de Azure App Service a través de WebSocket
            async with websockets.connect(WEBSOCKET_URL) as websocket:
                
                logger.info("✅ Conexión WebSocket establecida con el servidor.")
                
                while True:
                    # Lee un fragmento de audio del micrófono
                    # El tamaño de cada fragmento (4096) es una buena medida para streaming
                    data, overflowed = stream.read(4096)
                    
                    if overflowed:
                        logger.warning("¡Buffer de audio desbordado!")
                        
                    # Convierte los datos de audio a bytes y envíalos al servidor
                    # La función tobytes() de numpy es eficiente para esto
                    audio_bytes = data.tobytes()
                    
                    try:
                        await websocket.send(audio_bytes)
                    except websockets.exceptions.ConnectionClosed as e:
                        logger.error(f"❌ Conexión cerrada inesperadamente: {e}")
                        break
                    
    except sd.PortAudioError as e:
        logger.error(f"❌ Error de PortAudio: {e}. Asegúrate de que la biblioteca esté instalada y que el micrófono esté configurado.")
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"❌ El servidor cerró la conexión: {e}")
    except Exception as e:
        logger.error(f"❌ Ha ocurrido un error inesperado: {e}")

if __name__ == "__main__":
    try:
        # Lanza el cliente
        asyncio.run(audio_stream_client())
    except KeyboardInterrupt:
        logger.info("👋 Cliente detenido por el usuario.")
    except RuntimeError as e:
        logger.error(f"❌ Error de ejecución: {e}. Asegúrate de que asyncio.run() no esté ya ejecutándose.")