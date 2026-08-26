"""
Load assistance listings from the Data.gov bulk CSV and convert to API-like dicts
for Firebase + Pinecone. No SAM.gov API or quota required.

CSV: https://s3.amazonaws.com/falextracts/Assistance%20Listings/datagov/AssistanceListings_DataGov_PUBLIC_CURRENT.csv
Place the file in project data/ or set ASSISTANCE_LISTINGS_CSV to its path.

Also supports Grants.gov / simpler.grants.gov CSV exports (columns opportunity_id,
opportunity_title, etc.) and California Grants Portal exports (PortalID, AgencyDept,
GrantURL, etc.): same nested shape as SAM listings for Firestore + embeddings.
"""
import csv
import os
import re
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Possible CSV column names (case-insensitive) -> (key in our dict, optional nested path)
# We accept the first match when normalizing headers.
COLUMN_ALIASES = [
    ("program number", "assistanceListingId"),
    ("cfda number", "assistanceListingId"),
    ("assistance listing id", "assistanceListingId"),
    ("program_number", "assistanceListingId"),
    ("program title", "title"),
    ("title", "title"),
    ("objectives", "overview.objective"),
    ("objective", "overview.objective"),
    ("program objectives", "overview.objective"),
    ("description", "overview.assistanceListingDescription"),
    ("program description", "overview.assistanceListingDescription"),
    ("applicant eligibility", "criteriaForApplying.applicant.description"),
    ("eligibility", "criteriaForApplying.applicant.description"),
    ("who may apply", "criteriaForApplying.applicant.description"),
    ("beneficiary eligibility", "criteriaForApplying.beneficiary.description"),
    ("beneficiary", "criteriaForApplying.beneficiary.description"),
    ("deadlines", "assistanceApplication.deadlines.description"),
    ("application deadline", "assistanceApplication.deadlines.description"),
    ("application procedure", "assistanceApplication.applicationProcedure.description"),
    ("how to apply", "assistanceApplication.applicationProcedure.description"),
    ("url", "assistanceApplication.applicationProcedure.url"),
    ("website", "assistanceApplication.applicationProcedure.url"),
    ("application url", "assistanceApplication.applicationProcedure.url"),
    ("program url", "programWebPage"),
    ("authorization", "authorizations.description"),
    ("federal agency", "federalOrganization.agency"),
    ("agency", "federalOrganization.agency"),
]


def _normalize_header(h: str) -> str:
    """Lowercase, replace spaces/special chars with single underscore."""
    if not h:
        return ""
    s = re.sub(r"[\s\-/]+", "_", h.strip().lower())
    return re.sub(r"_+", "_", s).strip("_")


def _build_column_map(headers: list[str]) -> dict[str, str]:
    """Map normalized header -> our target path (e.g. overview.objective)."""
    normalized_to_path: dict[str, str] = {}
    for col in headers:
        n = _normalize_header(col)
        if not n:
            continue
        for alias, path in COLUMN_ALIASES:
            if n == _normalize_header(alias) or alias.replace(" ", "_") in n or n in alias.replace(" ", "_"):
                normalized_to_path[n] = path
                break
        if n not in normalized_to_path:
            normalized_to_path[n] = n  # keep unknown columns under a flat key we can ignore or use later
    return normalized_to_path


def _strip_html_to_text(s: str) -> str:
    if not s:
        return ""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_url(value: str) -> str:
    """Pull a URL from plain links or 'url: https://...' style fields."""
    s = (value or "").strip()
    if not s:
        return ""
    m = re.search(r"https?://[^\s;,\"']+", s, flags=re.I)
    if m:
        return m.group(0).rstrip(";,.)")
    if s.lower().startswith("url:"):
        return s[4:].strip().rstrip(";,.)")
    return s if s.lower().startswith("http") else ""


def _is_grants_gov_export_csv(fieldnames: list[str] | None) -> bool:
    if not fieldnames:
        return False
    keys = {h.strip().lower() for h in fieldnames if h}
    return "opportunity_id" in keys and "opportunity_title" in keys


def _is_ca_grants_portal_csv(fieldnames: list[str] | None) -> bool:
    if not fieldnames:
        return False
    keys = {h.strip().lower() for h in fieldnames if h}
    return "portalid" in keys and "agencydept" in keys and "granturl" in keys


def _grants_row_to_listing(row: dict[str, str | None]) -> dict[str, Any] | None:
    oid = (row.get("opportunity_id") or "").strip()
    if not oid:
        return None
    aid = f"GRANTS-{oid}"
    title = (row.get("opportunity_title") or "").strip()
    opp_num = (row.get("opportunity_number") or "").strip()
    agency = (row.get("agency_name") or row.get("top_level_agency_name") or "").strip()
    summary = _strip_html_to_text(row.get("summary_description") or "")

    funding_bits: list[str] = []
    for label, key in (
        ("Total program funding", "estimated_total_program_funding"),
        ("Award floor", "award_floor"),
        ("Award ceiling", "award_ceiling"),
    ):
        v = (row.get(key) or "").strip()
        if v:
            funding_bits.append(f"{label}: {v}")
    inst = (row.get("funding_instruments") or "").strip()
    if inst:
        funding_bits.append(f"Funding instruments: {inst}")
    cats = (row.get("funding_category_description") or row.get("funding_categories") or "").strip()
    if cats:
        funding_bits.append(f"Categories: {cats}")

    desc_parts: list[str] = []
    if summary:
        desc_parts.append(summary)
    st = (row.get("opportunity_status") or "").strip()
    if st:
        desc_parts.append(f"Status: {st}.")
    pd = (row.get("post_date") or "").strip()
    if pd:
        desc_parts.append(f"Posted: {pd}.")
    desc_parts.extend(funding_bits)
    description = " ".join(desc_parts)

    objective = " · ".join(p for p in (opp_num, agency) if p) or agency or opp_num

    applicant = (row.get("applicant_eligibility_description") or "").strip()
    types = (row.get("applicant_types") or "").strip().replace(";", ", ")
    if types:
        applicant = f"Applicant types: {types}. {applicant}".strip()

    close_d = (row.get("close_date") or "").strip()
    close_desc = (row.get("close_date_description") or "").strip()
    deadline_desc = " ".join(x for x in (close_d, close_desc) if x)

    main_url = (row.get("url") or "").strip()
    info_url = (row.get("additional_info_url") or "").strip()
    app_url = main_url or info_url

    obligations: list[dict[str, Any]] = []
    floor = (row.get("award_floor") or "").strip()
    ceil = (row.get("award_ceiling") or "").strip()
    if floor or ceil:
        obligations.append({
            "assistanceType": {"name": "Award range (Grants.gov)"},
            "values": [{"actual": f"floor {floor} · ceiling {ceil}".strip()}],
        })

    listing: dict[str, Any] = {
        "assistanceListingId": aid,
        "programId": aid,
        "title": title or opp_num or aid,
        "programWebPage": info_url or main_url or "",
        "sourceSystem": "Grants.gov",
        "grantsGovOpportunityId": oid,
        "overview": {
            "objective": objective,
            "assistanceListingDescription": description,
        },
        "federalOrganization": {"agency": agency},
        "criteriaForApplying": {
            "applicant": {"description": applicant},
            "beneficiary": {"description": ""},
        },
        "assistanceApplication": {
            "deadlines": {"description": deadline_desc, "value": close_d},
            "applicationProcedure": {
                "url": app_url,
                "URL": app_url,
                "description": (row.get("additional_info_url_description") or "").strip(),
            },
        },
        "status": st or "posted",
    }
    if obligations:
        listing["financialInformation"] = {"obligations": obligations}
    return listing


def _load_grants_gov_csv(reader: csv.DictReader) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in reader:
        rec = _grants_row_to_listing(row)
        if rec:
            out.append(rec)
    return out


def _ca_grants_row_to_listing(row: dict[str, str | None]) -> dict[str, Any] | None:
    portal_id = (row.get("PortalID") or "").strip()
    if not portal_id:
        return None
    aid = f"CA-GRANT-{portal_id}"
    grant_id = (row.get("GrantID") or "").strip()
    title = (row.get("Title") or "").strip()
    agency = (row.get("AgencyDept") or "").strip()
    purpose = (row.get("Purpose") or "").strip()
    description_body = (row.get("Description") or "").strip()
    categories = (row.get("Categories") or row.get("CategorySuggestion") or "").strip()
    grant_type = (row.get("Type") or "").strip()
    status = (row.get("Status") or "").strip()

    desc_parts: list[str] = []
    if description_body:
        desc_parts.append(description_body)
    if categories:
        desc_parts.append(f"Categories: {categories}.")
    if grant_type:
        desc_parts.append(f"Type: {grant_type}.")
    if status:
        desc_parts.append(f"Status: {status}.")
    geo = (row.get("Geography") or "").strip()
    if geo:
        desc_parts.append(f"Geography: {geo}.")
    for label, key in (
        ("Estimated available funds", "EstAvailFunds"),
        ("Estimated awards", "EstAwards"),
        ("Estimated amounts", "EstAmounts"),
        ("Funding source", "FundingSource"),
        ("Funding method", "FundingMethod"),
        ("Matching funds", "MatchingFunds"),
    ):
        v = (row.get(key) or "").strip()
        if v:
            desc_parts.append(f"{label}: {v}.")
    for label, key in (
        ("Funding source notes", "FundingSourceNotes"),
        ("Funding method notes", "FundingMethodNotes"),
        ("Matching funds notes", "MatchingFundsNotes"),
    ):
        v = (row.get(key) or "").strip()
        if v:
            desc_parts.append(f"{label}: {v}")
    contact = (row.get("ContactInfo") or "").strip()
    if contact:
        desc_parts.append(f"Contact: {contact}.")
    description = " ".join(desc_parts)

    objective = " · ".join(p for p in (agency, purpose) if p) or agency or purpose or title

    applicant_types = (row.get("ApplicantType") or "").strip().replace(";", ", ")
    applicant_notes = (row.get("ApplicantTypeNotes") or "").strip()
    applicant = " ".join(x for x in (
        f"Applicant types: {applicant_types}." if applicant_types else "",
        applicant_notes,
    ) if x)

    open_date = (row.get("OpenDate") or "").strip()
    deadline = (row.get("ApplicationDeadline") or "").strip()
    award_period = (row.get("AwardPeriod") or "").strip()
    deadline_desc = " ".join(x for x in (open_date, deadline, award_period) if x)

    grant_url = _extract_url(row.get("GrantURL") or "")
    agency_url = _extract_url(row.get("AgencyURL") or "")
    elec_url = _extract_url(row.get("ElecSubmission") or "")
    app_url = elec_url or grant_url or agency_url

    obligations: list[dict[str, Any]] = []
    amounts = (row.get("EstAmounts") or "").strip()
    avail = (row.get("EstAvailFunds") or "").strip()
    if amounts or avail:
        obligations.append({
            "assistanceType": {"name": "California grant funding"},
            "values": [{"actual": " · ".join(x for x in (avail, amounts) if x)}],
        })

    listing: dict[str, Any] = {
        "assistanceListingId": aid,
        "programId": aid,
        "title": title or grant_id or aid,
        "programWebPage": grant_url or agency_url or "",
        "sourceSystem": "California Grants Portal",
        "caGrantsPortalId": portal_id,
        "overview": {
            "objective": objective,
            "assistanceListingDescription": description,
        },
        "federalOrganization": {"agency": agency},
        "criteriaForApplying": {
            "applicant": {"description": applicant},
            "beneficiary": {"description": geo},
        },
        "assistanceApplication": {
            "deadlines": {"description": deadline_desc, "value": deadline or open_date},
            "applicationProcedure": {
                "url": app_url,
                "URL": app_url,
                "description": (row.get("LOI") or "").strip(),
            },
        },
        "status": status or "active",
    }
    if grant_id:
        listing["caGrantId"] = grant_id
    if obligations:
        listing["financialInformation"] = {"obligations": obligations}
    return listing


def _load_ca_grants_portal_csv(reader: csv.DictReader) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in reader:
        rec = _ca_grants_row_to_listing(row)
        if rec:
            out.append(rec)
    return out


def _set_nested(d: dict, path: str, value: Any) -> None:
    if not path or value is None or (isinstance(value, str) and not value.strip()):
        return
    parts = path.split(".")
    cur = d
    for i, p in enumerate(parts[:-1]):
        if p not in cur:
            cur[p] = {}
        cur = cur[p]
    key = parts[-1]
    if key in ("url", "URL") and "applicationProcedure" in path:
        cur["url"] = value
        cur["URL"] = value
    else:
        cur[key] = value


def load_listings_from_csv(csv_path: str | Path) -> list[dict[str, Any]]:
    """Read CSV and return list of dicts shaped like SAM API assistance listings."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")

    listings: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        if _is_grants_gov_export_csv(headers):
            return _load_grants_gov_csv(reader)
        if _is_ca_grants_portal_csv(headers):
            return _load_ca_grants_portal_csv(reader)

        col_map = _build_column_map(list(headers))

        for row in reader:
            record: dict[str, Any] = {}
            for raw_header, value in row.items():
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                n = _normalize_header(raw_header)
                path_key = col_map.get(n)
                if path_key and "." in path_key and not path_key.startswith("_"):
                    _set_nested(record, path_key, value.strip())
                elif path_key == "assistanceListingId":
                    record["assistanceListingId"] = value.strip()
                elif path_key == "title":
                    record["title"] = value.strip()
                elif path_key == "programWebPage":
                    record["programWebPage"] = value.strip()

            # Ensure required id; backend may expect programId as well
            aid = record.get("assistanceListingId") or record.get("programId")
            if not aid:
                for k, v in row.items():
                    if _normalize_header(k) in ("program_number", "cfda_number"):
                        v = str(v).strip()
                        if v:
                            record["assistanceListingId"] = v
                            record["programId"] = v
                            break
            if not record.get("assistanceListingId"):
                record["assistanceListingId"] = f"row_{len(listings)}"
            if record.get("assistanceListingId") and "programId" not in record:
                record["programId"] = record["assistanceListingId"]
            record["status"] = "Active"
            listings.append(record)

    return listings


def get_csv_path() -> Path:
    """Resolve CSV path from env or default under project data/."""
    env_path = os.environ.get("ASSISTANCE_LISTINGS_CSV")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return ROOT / "data" / "AssistanceListings_DataGov_PUBLIC_CURRENT.csv"
