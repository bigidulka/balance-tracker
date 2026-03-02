from datetime import datetime

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    organization_id: int | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    organization_id: int


class UserProfileResponse(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    is_active: bool
    created_at: datetime


class OrganizationInfo(BaseModel):
    id: int
    name: str
    slug: str
    role: str


class IdentityResponse(BaseModel):
    user: UserProfileResponse
    organization: OrganizationInfo
