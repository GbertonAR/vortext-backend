from sqlalchemy import Column, Integer, String, Text, DateTime, func
from .base import Base

class TranslationRecord(Base):
    __tablename__ = "translation_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    source_lang = Column(String(8))
    target_lang = Column(String(8))
    source_text = Column(Text)
    translated_text = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
