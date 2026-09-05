"""LLM reasoning: filter programs to 100% mandatory criteria match and return structured output."""
import json
import os
from typing import Any

from openai import OpenAI

from backend.schemas import MatchItem, MatchRequest


def _listing_summary(listing: dict[str, Any]) -> str:
    aid = listing.get("assistanceListingId") or listing.get("programId") or ""
    title = listing.get("title") or ""
    overview = listing.get("overview") or {}
    obj = overview.get("objective") or ""
    desc = overview.get("assistanceListingDescription") or ""
    criteria = listing.get("criteriaForApplying") or {}
    appl = criteria.get("applicant") or {}
    ben = criteria.get("beneficiary") or {}
    appl_desc = appl.get("description") if isinstance(appl, dict) else ""
    ben_desc = ben.get("description") if isinstance(ben, dict) else ""
    app = listing.get("assistanceApplication") or {}
    proc = (app.get("applicationProcedure") or {}) if isinstance(app, dict) else {}
    app_url = proc.get("url") or proc.get("URL") or ""
    app_proc_desc = proc.get("description") if isinstance(proc, dict) else ""
    fin = listing.get("financialInformation") or {}
    obligations = (fin.get("obligations") or []) if isinstance(fin, dict) else []
    award_parts = []
    for ob in obligations:
        if isinstance(ob, dict):
            at = ob.get("assistanceType") or {}
            if isinstance(at, dict):
                award_parts.append(at.get("name") or at.get("code") or "")
            vals = ob.get("values") or []
            for v in vals:
                if isinstance(v, dict) and v.get("actual") is not None:
                    award_parts.append(f"amount: {v.get('actual')}")
    approval = (app.get("approval") or {}) if isinstance(app, dict) else {}
    renewal = (app.get("renewal") or {}) if isinstance(app, dict) else {}
    return (
        f"ID: {aid}\nTitle: {title}\nObjective: {obj}\nDescription: {desc}\n"
        f"Applicant eligibility: {appl_desc}\nBeneficiary eligibility: {ben_desc}\n"
        f"Application URL: {app_url}\nApplication: {app_proc_desc}\n"
        f"Award types/amounts: {'; '.join(award_parts)}\n"
        f"Approval: {approval.get('interval')} - {approval.get('description')}\n"
        f"Renewal: {renewal.get('interval')} - {renewal.get('description')}"
    )


def _profile_text(req: MatchRequest) -> str:
    parts = []
    if req.age is not None:
        parts.append(f"Age: {req.age}")
    if req.state:
        parts.append(f"State: {req.state}")
    if req.zip_code:
        parts.append(f"ZIP: {req.zip_code}")
    if req.citizenship_status:
        parts.append(f"Citizenship: {req.citizenship_status}")
    if req.ethnicity_background:
        parts.append(f"Ethnicity: {req.ethnicity_background}")
    if req.current_grade_year:
        parts.append(f"Grade/year: {req.current_grade_year}")
    if req.gpa is not None:
        parts.append(f"GPA: {req.gpa}")
    if req.major_area_of_study:
        parts.append(f"Major: {req.major_area_of_study}")
    if req.first_generation is not None:
        parts.append(f"First-generation: {req.first_generation}")
    if req.annual_household_income is not None:
        parts.append(f"Household income: {req.annual_household_income}")
    if req.employment_status:
        parts.append(f"Employment: {req.employment_status}")
    if req.fafsa_status:
        parts.append(f"FAFSA: {req.fafsa_status}")
    if req.veteran_status is not None:
        parts.append(f"Veteran: {req.veteran_status}")
    if req.disability_status is not None:
        parts.append(f"Disability: {req.disability_status}")
    if req.parental_status:
        parts.append(f"Parental status: {req.parental_status}")
    return "\n".join(parts) if parts else "No profile provided"


SYSTEM_PROMPT = """You are an eligibility analyst. You will receive a user profile (only the fields they chose to provide) and several federal assistance program summaries.

CRITICAL: Evaluate match ONLY on the attributes the user actually provided. If the user did NOT provide a piece of information (e.g. they did not list ethnicity, income, or veteran status), do NOT treat that as a disqualifier. Include the program in your results and in eligibility_met note "Check program for: [criterion]" or "May depend on: [criterion]" so they know to verify. Only exclude a program when the user explicitly does NOT meet a stated requirement (e.g. program requires US citizens only and user said they are not a citizen).

Rank programs by how well the user fits on the information provided: "Full match" (user clearly meets all criteria that can be checked from their profile), "Strong match" (meets most checkable criteria), "Partial match" (meets some; other criteria unknown or need verification). Return the TOP 10 programs that are most relevant for this user, ordered from best match to weakest. For each program output exactly these keys:
- source: program name or title
- prize: type of benefit and amount/description (e.g. "Grant, $X–Y" or "Direct Payment")
- deadline: always an empty string (do not invent or copy application due dates; catalog dates are often stale)
- eligibility_met: what the user meets based on their profile; for any program criterion the user did not provide info for, say "Check program for: [criterion]" or "May depend on: [criterion]"
- how_to_claim: application URL and brief steps to apply; do not include calendar due dates
- assistance_listing_id: the program ID from the summary
- assistance_type: award category (e.g. Grant, Loan, Direct Payment)
- timeliness: how quickly benefits can take effect (from approval/renewal info or "Varies")
- match_quality: one of "Full match", "Strong match", or "Partial match"
Output a JSON array of up to 10 objects, ordered by match quality (best first). If no programs are at all relevant, return an empty array [].
Output only valid JSON, no markdown or extra text."""


def filter_and_format(req: MatchRequest, listings: list[dict[str, Any]]) -> list[MatchItem]:
    if not listings:
        return []
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    profile = _profile_text(req)
    program_texts = "\n\n---\n\n".join(_listing_summary(L) for L in listings)
    user_content = (
        f"User profile (only these fields were provided—treat any other program criteria as unknown and do not exclude for them):\n{profile}\n\n"
        f"Programs to evaluate (return best up to 10, ranked by fit to the provided profile):\n{program_texts}"
    )
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
    )
    content = (response.choices[0].message.content or "").strip()
    # Strip markdown code block if present
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    content = content.strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(MatchItem(
                source=item.get("source") or "",
                prize=item.get("prize") or "",
                eligibility_met=item.get("eligibility_met") or "",
                how_to_claim=item.get("how_to_claim") or "",
                assistance_listing_id=item.get("assistance_listing_id"),
                assistance_type=item.get("assistance_type"),
                timeliness=item.get("timeliness"),
                match_quality=item.get("match_quality"),
            ))
        except Exception:
            continue
    return result[:10]
