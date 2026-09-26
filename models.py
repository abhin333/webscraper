"""Pydantic request/response models."""

from typing import Optional

from pydantic import BaseModel, EmailStr


class ConsultRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    company: Optional[str] = None
    message: Optional[str] = None