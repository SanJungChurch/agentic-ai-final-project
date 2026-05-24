from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    SCHEDULE_MEETING = "schedule_meeting"
    RESCHEDULE_MEETING = "reschedule_meeting"
    CANCEL_MEETING = "cancel_meeting"
    OTHER = "other"


class TimeConstraint(BaseModel):
    participant: Optional[str] = None
    expression: str
    normalized_start: Optional[str] = None
    normalized_end: Optional[str] = None
    availability: str = Field(
        description="One of available, unavailable, preferred, tentative, unknown."
    )


class ExtractionResult(BaseModel):
    intent: Intent
    participants: List[str] = Field(default_factory=list)
    candidate_times: List[TimeConstraint] = Field(default_factory=list)
    unavailable_times: List[TimeConstraint] = Field(default_factory=list)
    location_preference: Optional[str] = None
    meeting_duration_minutes: Optional[int] = None
    missing_information: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source_summary: str = ""


class EmailThread(BaseModel):
    subject: Optional[str] = None
    body: str
