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


class CalendarEvent(BaseModel):
    participant: str
    start: str
    end: str
    title: str = ""
    location: Optional[str] = None


class TimeCandidate(BaseModel):
    source_expression: str
    start: str
    end: str
    available_participants: List[str] = Field(default_factory=list)
    preferred_by: List[str] = Field(default_factory=list)


class CandidateDecision(BaseModel):
    candidate: TimeCandidate
    valid: bool
    score: int
    hard_violations: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    conflicts: List[CalendarEvent] = Field(default_factory=list)


class ScheduleRecommendation(BaseModel):
    selected: Optional[CandidateDecision] = None
    candidates: List[CandidateDecision] = Field(default_factory=list)
    status: str = Field(description="One of selected, no_valid_candidate, missing_candidate_times.")
    summary: str = ""


class ReplyDraft(BaseModel):
    subject: str
    body: str
    status: str = Field(description="One of ready, needs_clarification, no_valid_candidate.")
    rationale: List[str] = Field(default_factory=list)


class PlaceCandidate(BaseModel):
    name: str
    address: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[float] = None
    source_url: Optional[str] = None
    availability_hint: Optional[str] = None
    score: float = 0.0


class PlaceRecommendation(BaseModel):
    query: str
    candidates: List[PlaceCandidate] = Field(default_factory=list)
    selected: Optional[PlaceCandidate] = None
    status: str = Field(description="One of selected, no_query, no_candidates.")
    summary: str = ""


class ReservationRequest(BaseModel):
    place_name: str
    start: str
    end: str
    party_size: int = 1
    customer_name: str = "Schedule-to-Action Agent"
    note: Optional[str] = None


class ReservationResult(BaseModel):
    status: str = Field(description="One of confirmed, failed, skipped, needs_manual_action.")
    place_name: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    confirmation_id: Optional[str] = None
    message: str = ""
    failure_reason: Optional[str] = None
    steps: List[str] = Field(default_factory=list)
