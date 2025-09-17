from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Azure OpenAI (pueden estar vacíos si no existen en el .env)
    AZURE_OPENAI_API_KEY: str | None = None
    AZURE_OPENAI_ENDPOINT: str | None = None
    AZURE_OPENAI_DEPLOYMENT: str | None = None
    AZURE_OPENAI_API_VERSION: str | None = None

    # Speech
    SPEECH_KEY: str | None = None
    SPEECH_REGION: str | None = None

    # Translator
    TRANSLATOR_KEY: str | None = None
    TRANSLATOR_REGION: str | None = None

    class Config:
        env_file = ".env"
        extra = "allow"  # permite variables extra en el .env

# Crear una instancia global
settings = Settings()
