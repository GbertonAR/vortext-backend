# File: app/api/router.py

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from app import crud, schemas
from app.db.session import get_db
from app.api.deps import get_current_user
from app.security import create_access_token
from datetime import timedelta
from app.core.config import settings
from pydantic import BaseModel
import base64

# Importamos los servicios de Azure
from app.services.azure_utils import speech_to_text, translate_text, text_to_speech


# Modelo de respuesta para el frontend
class TranslationResponse(BaseModel):
    transcribed_text: str
    translated_text: str
    detected_language: str
    target_language: str
    translated_audio_base64: str


router = APIRouter()

# -------------------------------
# AUTH
# -------------------------------
@router.post('/auth/register', response_model=schemas.UserOut)
def register(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = crud.get_user_by_email(db, user_in.email)
    if existing:
        raise HTTPException(status_code=400, detail='Email already registered')
    user = crud.create_user(db, user_in)
    return user


@router.post('/auth/login', response_model=schemas.Token)
def login(form_data: schemas.UserCreate, db: Session = Depends(get_db)):
    user = crud.authenticate_user(db, form_data.email, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail='Incorrect email or password')
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    return {"access_token": token, "token_type": "bearer"}


# -------------------------------
# PROJECTS
# -------------------------------
@router.post('/projects', response_model=schemas.ProjectOut)
def create_project(project_in: schemas.ProjectCreate, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.create_project(db, current_user.id, project_in)


@router.get('/projects', response_model=list[schemas.ProjectOut])
def list_projects(skip: int = 0, limit: int = 100, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(crud.models.Project).filter(crud.models.Project.owner_id == current_user.id).offset(skip).limit(limit).all()


# -------------------------------
# TRANSLATIONS
# -------------------------------
@router.post('/translations', response_model=schemas.TranslationOut)
def create_translation(translation_in: schemas.TranslationCreate, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    tr = crud.create_translation(db, translation_in)
    # Aquí podría invocarse un worker (ej. Celery) o hacerlo síncrono
    return tr


@router.get('/translations', response_model=list[schemas.TranslationOut])
def list_translations(skip: int = 0, limit: int = 100, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_translations(db, skip, limit)


@router.get('/translations/{translation_id}', response_model=schemas.TranslationOut)
def get_translation(translation_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    tr = crud.get_translation(db, translation_id)
    if not tr:
        raise HTTPException(status_code=404, detail='Not found')
    return tr


# -------------------------------
# TRANSLATE AUDIO ENDPOINT
# -------------------------------
@router.post("/translate-audio/", response_model=TranslationResponse)
async def translate_audio_endpoint(audio_file: UploadFile = File(...)):
    """
    Endpoint que recibe un archivo de audio, lo transcribe (Speech-to-Text),
    lo traduce (Translator) y devuelve el texto traducido + audio (Text-to-Speech).
    """
    try:
        audio_content = await audio_file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al leer el archivo de audio: {e}")

    # 1. Transcribir con Azure Speech-to-Text
    try:
        transcribed_text, detected_language = await speech_to_text(audio_content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en la transcripción: {e}")

    # 2. Determinar idioma objetivo
    if detected_language.startswith("es"):
        target_language = "en"
    elif detected_language.startswith("en"):
        target_language = "es"
    else:
        raise HTTPException(status_code=400, detail="Idioma no soportado (solo español/inglés)")

    # 3. Traducir con Azure Translator
    try:
        translated_text = await translate_text(transcribed_text, target_language)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en la traducción: {e}")

    # 4. Convertir traducción a audio con Azure Text-to-Speech
    try:
        translated_audio_bytes = await text_to_speech(translated_text, target_language)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en la generación de audio: {e}")

    # 5. Codificar audio en base64
    translated_audio_base64 = base64.b64encode(translated_audio_bytes).decode("utf-8")

    # 6. Respuesta al frontend
    return TranslationResponse(
        transcribed_text=transcribed_text,
        translated_text=translated_text,
        detected_language=detected_language,
        target_language=target_language,
        translated_audio_base64=translated_audio_base64
    )
