from datetime import date, time, datetime, timedelta
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    program_id: str
    name: str = Field(min_length=1, max_length=200)
    start_date: date | None = None

    @field_validator("start_date", mode="before")
    @classmethod
    def default_start_date(cls, value):
        return value or date.today() + timedelta(days=10)


class BatchReschedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    module_id: str
    professor_id: str
    session_start: datetime
    session_end: datetime


class BatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    program_id: str
    name: str
    start_date: date
    sessions: list[SessionRead] = Field(default_factory=list)
