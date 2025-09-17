
# File: app/crud.py

from sqlalchemy.orm import Session
from app import models, schemas
from app.security import hash_password, verify_password
from datetime import datetime


# Users
def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()


def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(email=user.email, hashed_password=hash_password(user.password))
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def authenticate_user(db: Session, email: str, password: str):
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


# Projects
def create_project(db: Session, owner_id: int, project: schemas.ProjectCreate):
    db_project = models.Project(name=project.name, description=project.description, owner_id=owner_id)
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


# Translations
def create_translation(db: Session, translation_in: schemas.TranslationCreate):
    db_tr = models.Translation(
    source_text=translation_in.source_text,
    source_lang=translation_in.source_lang,
    target_lang=translation_in.target_lang,
    status="pending",
    project_id=translation_in.project_id,
    )
    db.add(db_tr)
    db.commit()
    db.refresh(db_tr)
    return db_tr


def set_translation_result(db: Session, translation_id: int, translated_text: str):
    tr = db.query(models.Translation).filter(models.Translation.id == translation_id).first()
    if tr:
        tr.translated_text = translated_text
        tr.status = "completed"
        tr.completed_at = datetime.utcnow()
        db.add(tr)
        db.commit()
        db.refresh(tr)
        return tr


def get_translation(db: Session, translation_id: int):
    return db.query(models.Translation).filter(models.Translation.id == translation_id).first()


def list_translations(db: Session, skip: int = 0, limit: int = 100):

    return db.query(models.Translation).offset(skip).limit(limit).all()