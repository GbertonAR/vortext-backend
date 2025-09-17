
# File: app/schemas.py

from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    is_active: bool


class Config:
    orm_mode = True


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    owner_id: int
    created_at: datetime


class Config:
    orm_mode = True


class TranslationCreate(BaseModel):
    source_text: str
    target_lang: str
    source_lang: Optional[str] = "auto"
    project_id: Optional[int] = None


class TranslationOut(BaseModel):
    id: int
    source_text: str
    target_lang: str
    source_lang: Optional[str]
    translated_text: Optional[str]
    status: str
    project_id: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]


class Config:
    orm_mode = True
