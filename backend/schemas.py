"""Request/response models for the match API."""
from pydantic import BaseModel, Field


class MatchRequest(BaseModel):
    # Demographics
    age: int | None = Field(None, description="Age")
    state: str | None = Field(None, description="State code or name")
    zip_code: str | None = Field(None, description="ZIP code")
    citizenship_status: str | None = Field(None, description="e.g. citizen, permanent resident")
    ethnicity_background: str | None = Field(None, description="Ethnicity/background")
    # Education (optional)
    current_grade_year: str | None = Field(None, description="e.g. junior, senior, freshman")
    gpa: float | None = Field(None, description="GPA")
    major_area_of_study: str | None = Field(None, description="Major or area of study")
    first_generation: bool | None = Field(None, description="First-generation student")
    # Financials
    annual_household_income: float | None = Field(None, description="Annual household income")
    employment_status: str | None = Field(None, description="e.g. employed, unemployed")
    fafsa_status: str | None = Field(None, description="e.g. completed, not started")
    # Special status
    veteran_status: bool | None = Field(None, description="Veteran")
    disability_status: bool | None = Field(None, description="Has disability")
    parental_status: str | None = Field(None, description="e.g. single parent")
    # Optional free text
    free_text: str | None = Field(None, description="Additional context or question")


class MatchItem(BaseModel):
    source: str = Field(..., description="Benefit/scholarship name or ID")
    prize: str = Field(..., description="Type and amount/description of benefit")
    deadline: str = Field("", description="Unused; catalog due dates are often stale")
    eligibility_met: str = Field(..., description="Eligibility criteria the user met")
    how_to_claim: str = Field(..., description="How to apply or claim")
    assistance_listing_id: str | None = Field(None, description="GSA listing ID for sorting/linking")
    assistance_type: str | None = Field(None, description="Award category for sorting")
    timeliness: str | None = Field(None, description="Approval/renewal interval for sorting")
    match_quality: str | None = Field(None, description="Full match, Strong match, or Partial match")


class MatchResponse(BaseModel):
    matches: list[MatchItem] = Field(default_factory=list)
    total: int = 0
    candidates_evaluated: int | None = Field(None, description="Number of programs sent to LLM for evaluation (0 = none in index)")
