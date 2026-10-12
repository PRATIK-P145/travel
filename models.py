"""Request and response models for Travel-Genie."""
from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SUPPORTED_CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD", "THB"]
TRAVEL_STYLES = ["budget", "balanced", "luxury"]
INTERESTS = ["food", "nature", "adventure", "history", "beaches", "shopping", "culture", "nightlife"]
MAX_TRIP_DAYS = 14


class TripRequest(BaseModel):
    start_city: str = Field(min_length=2, max_length=80)
    destination: str = Field(min_length=2, max_length=80)
    departure_date: date
    return_date: Optional[date] = None
    duration_days: Optional[int] = Field(default=None, ge=1, le=MAX_TRIP_DAYS)
    travelers: int = Field(default=2, ge=1, le=20)
    budget: float = Field(gt=0, le=100_000_000)
    currency: str = "INR"
    travel_style: Literal["budget", "balanced", "luxury"] = "balanced"
    interests: list[str] = Field(default_factory=list)
    accommodation: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=1000)

    @field_validator("start_city", "destination", "accommodation", "notes", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Currency must be one of: {', '.join(SUPPORTED_CURRENCIES)}.")
        return v

    @field_validator("interests")
    @classmethod
    def _interests(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in values:
            item = str(item).strip().lower()
            if item not in INTERESTS:
                raise ValueError(f"Unknown interest '{item}'. Choose from: {', '.join(INTERESTS)}.")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _dates(self):
        if self.departure_date < date.today():
            raise ValueError("Departure date can't be in the past.")
        if self.return_date is None and self.duration_days is None:
            raise ValueError("Provide either a return date or a trip duration.")
        if self.return_date is not None:
            if self.return_date < self.departure_date:
                raise ValueError("Return date must be on or after the departure date.")
            days = (self.return_date - self.departure_date).days + 1
            if days > MAX_TRIP_DAYS:
                raise ValueError(f"Trips are limited to {MAX_TRIP_DAYS} days.")
            if self.duration_days is not None and self.duration_days != days:
                raise ValueError("Return date and duration don't match. Provide only one, or make them agree.")
            self.duration_days = days
        else:
            self.return_date = self.departure_date + timedelta(days=self.duration_days - 1)
        return self


class Slot(BaseModel):
    title: str
    description: str = ""
    est_cost: int = 0  # estimated cost for the whole group


class Meals(BaseModel):
    breakfast: str = ""
    lunch: str = ""
    dinner: str = ""


class Day(BaseModel):
    day: int
    date: str
    title: str
    morning: Slot
    afternoon: Slot
    evening: Slot
    meals: Meals
    transport: str = ""
    est_day_cost: int = 0  # sum of the three slot estimates


class Stay(BaseModel):
    area: str
    why: str = ""
    est_per_night: int = 0


class BudgetLine(BaseModel):
    key: str
    label: str
    amount: int


class Budget(BaseModel):
    currency: str
    lines: list[BudgetLine]
    estimated_total: int
    user_budget: int
    difference: int  # user_budget - estimated_total; negative means over budget
    within_budget: bool
    per_person: int
    suggestions: list[str]
    note: str


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    kind: Literal["search", "maps"] = "search"


class TripSummary(BaseModel):
    start_city: str
    destination: str
    departure_date: str
    return_date: str
    duration_days: int
    travelers: int
    budget: float
    currency: str
    travel_style: str
    interests: list[str]


class Meta(BaseModel):
    itinerary_source: Literal["gemini", "fallback"]
    research_source: Literal["serpapi", "unavailable", "not_configured"]
    model: Optional[str] = None
    generated_at: str
    verified: bool = False
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str


class PlanResponse(BaseModel):
    trip: TripSummary
    overview: str
    days: list[Day]
    accommodation: list[Stay]
    budget: Budget
    tips: list[str]
    caveats: list[str]
    sources: list[Source]
    meta: Meta
