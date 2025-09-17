from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.translation_record import TranslationRecord

router = APIRouter(tags=["translate"])

@router.get("/translate")
def translate_text(q: str, db: Session = Depends(get_db)):
    """
    Endpoint demo: recibe un texto (?q=hola) y devuelve su "traducción".
    Ahora mismo solo invierte el texto, pero aquí irá la integración con Azure Translator.
    """
    translated = q[::-1]  # 🚧 DEMO: texto invertido

    record = TranslationRecord(
        source_lang="auto",
        target_lang="es",
        source_text=q,
        translated_text=translated
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "id": record.id,
        "original": q,
        "translated": translated,
        "created_at": record.created_at
    }
