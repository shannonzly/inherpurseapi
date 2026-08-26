"""
Firebase Firestore storage for GSA assistance listings.
SAM fetch runs at most once per day; all listing data is read from Firestore.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Project root for imports
_ROOT = Path(__file__).resolve().parent.parent

_FIRESTORE_LISTINGS = "assistance_listings"
_FIRESTORE_META = "ingestion_metadata"
_META_DOC_ID = "sam_fetch"
_DATE_FMT = "%Y-%m-%d"


def _safe_id(listing_id: str) -> str:
    """Firestore doc IDs cannot contain '.' or '/'; use underscore."""
    return (listing_id or "").replace(".", "_").replace("/", "_").strip() or "unknown"


def _strip_env_path(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


def _resolve_service_account_json_path() -> str | None:
    """
    Return an absolute path to a service account JSON file, or None.
    Checks GOOGLE_APPLICATION_CREDENTIALS (after strip/quotes) and the same key
    from project .env so scripts work even when the shell blocks dotenv (e.g. empty export).
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        s = _strip_env_path(raw)
        if not s:
            return
        try:
            p = Path(s).expanduser()
            key = str(p.resolve())
        except OSError:
            return
        if key not in seen:
            seen.add(key)
            candidates.append(s)

    add(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    env_file = _ROOT / ".env"
    if env_file.is_file():
        try:
            from dotenv import dotenv_values

            dv = dotenv_values(env_file)
            add(dv.get("GOOGLE_APPLICATION_CREDENTIALS"))
        except ImportError:
            pass

    for s in candidates:
        try:
            resolved = Path(s).expanduser().resolve()
        except OSError:
            continue
        if resolved.is_file():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)
            return str(resolved)
    return None


def _firebase_credentials_ok() -> bool:
    """Return True if Firebase credentials are set and usable (without initializing)."""
    if _resolve_service_account_json_path():
        return True
    if _strip_env_path(os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")):
        return True
    return False


def _get_firestore():
    import firebase_admin
    from firebase_admin import firestore

    try:
        firebase_admin.get_app()
    except ValueError:
        cred_path = _resolve_service_account_json_path()
        if cred_path:
            firebase_admin.initialize_app()
        else:
            # Allow passing JSON key via env (e.g. for CI)
            key_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
            if key_json:
                import json as _json
                firebase_admin.initialize_app(credential=firebase_admin.credentials.Certificate(_json.loads(key_json)))
            else:
                try:
                    # Cloud Run / GCE: Application Default Credentials (runtime service account)
                    firebase_admin.initialize_app()
                except Exception as e:
                    raise RuntimeError(
                        "Firebase not configured. For local dev set GOOGLE_APPLICATION_CREDENTIALS or "
                        "FIREBASE_SERVICE_ACCOUNT_JSON. On Cloud Run, grant the service account Firestore access."
                    ) from e
    return firestore.client()


def get_listings_count() -> int | None:
    """Return number of documents in assistance_listings, or None on error."""
    try:
        db = _get_firestore()
        coll = db.collection(_FIRESTORE_LISTINGS)
        return sum(1 for _ in coll.limit(50000).stream())
    except Exception:
        return None


def get_last_sam_fetch_date_utc() -> str | None:
    """Return the UTC date (YYYY-MM-DD) of the last SAM fetch, or None if never."""
    try:
        db = _get_firestore()
        doc = db.collection(_FIRESTORE_META).document(_META_DOC_ID).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return data.get("last_sam_fetch_date")
    except Exception:
        return None


def set_sam_fetched_today() -> None:
    """Record that SAM was fetched today (UTC)."""
    today = datetime.now(timezone.utc).strftime(_DATE_FMT)
    try:
        db = _get_firestore()
        db.collection(_FIRESTORE_META).document(_META_DOC_ID).set(
            {"last_sam_fetch_date": today, "updated_at": datetime.now(timezone.utc).isoformat()},
            merge=True,
        )
    except Exception as e:
        raise RuntimeError(f"Failed to write ingestion metadata: {e}") from e


def sam_already_fetched_today() -> bool:
    today = datetime.now(timezone.utc).strftime(_DATE_FMT)
    last = get_last_sam_fetch_date_utc()
    return last == today


def write_listings(listings: list[dict[str, Any]]) -> int:
    """Write all listings to Firestore. Each listing is one document keyed by safe assistance_listing_id."""
    if not listings:
        return 0
    db = _get_firestore()
    coll = db.collection(_FIRESTORE_LISTINGS)
    count = 0
    for listing in listings:
        aid = listing.get("assistanceListingId") or listing.get("programId") or ""
        if not aid:
            continue
        doc_id = _safe_id(aid)
        # Firestore doesn't allow nested dots in field names; store raw as JSON string
        data = {"assistance_listing_id": aid, "raw_json": json.dumps(listing, default=str)}
        coll.document(doc_id).set(data, merge=True)
        count += 1
    return count


def get_listings_by_ids(ids: list[str]) -> list[dict]:
    """Load full listing payloads from Firestore by assistance_listing_id."""
    if not ids:
        return []
    db = _get_firestore()
    coll = db.collection(_FIRESTORE_LISTINGS)
    out = []
    for aid in ids:
        doc_id = _safe_id(aid)
        doc = coll.document(doc_id).get()
        if not doc.exists:
            continue
        data = doc.to_dict() or {}
        raw = data.get("raw_json")
        if isinstance(raw, str):
            out.append(json.loads(raw))
        elif isinstance(raw, dict):
            out.append(raw)
    return out


def get_all_listings_for_embedding() -> list[dict]:
    """Return all listings (for embedding). Each dict has assistance_listing_id, title, overview_*, etc. and raw_json."""
    db = _get_firestore()
    coll = db.collection(_FIRESTORE_LISTINGS)
    out = []
    for doc in coll.stream():
        data = doc.to_dict() or {}
        raw = data.get("raw_json")
        if isinstance(raw, str):
            listing = json.loads(raw)
        elif isinstance(raw, dict):
            listing = raw
        else:
            continue
        aid = listing.get("assistanceListingId") or listing.get("programId") or ""
        overview = listing.get("overview") or {}
        criteria = listing.get("criteriaForApplying") or {}
        applicant = criteria.get("applicant") or {}
        beneficiary = criteria.get("beneficiary") or {}
        fin = listing.get("financialInformation") or {}
        obligations = (fin.get("obligations") or []) if isinstance(fin, dict) else []
        type_names = []
        for ob in obligations:
            if isinstance(ob, dict):
                at = ob.get("assistanceType") or {}
                if isinstance(at, dict) and at.get("name"):
                    type_names.append(at["name"])
        out.append({
            "assistance_listing_id": aid,
            "title": listing.get("title") or "",
            "overview_objective": (overview.get("objective") or "") if isinstance(overview, dict) else "",
            "overview_description": (overview.get("assistanceListingDescription") or "") if isinstance(overview, dict) else "",
            "applicant_description": (applicant.get("description") or "") if isinstance(applicant, dict) else "",
            "beneficiary_description": (beneficiary.get("description") or "") if isinstance(beneficiary, dict) else "",
            "assistance_types": "; ".join(type_names) if type_names else "",
            "raw_json": listing,
        })
    return out
