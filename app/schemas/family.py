from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class FamilyMember(BaseModel):
    user_id: str
    role: Literal["admin", "member"]
    email: str | None = None
    name: str | None = None


class FamilyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FamilyCreated(BaseModel):
    family_id: str
    name: str


class FamilyOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    created_by: str
    members: list[FamilyMember]
    created_at: datetime


class InviteRequest(BaseModel):
    email: EmailStr
    # Optional when the caller administers exactly one family.
    family_id: str | None = None


class InviteResponse(BaseModel):
    status: Literal["invited"] = "invited"


class JoinRequest(BaseModel):
    code: str = Field(min_length=1)
