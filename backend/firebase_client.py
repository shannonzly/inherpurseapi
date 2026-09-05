"""Backend Firebase client: read listings and optional match cache."""
import hashlib
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.firebase_store import get_listings_by_ids as firebase_get_listings, get_listings_count as firebase_get_listings_count

# Use Firebase for all listing reads
def get_listings_by_ids(ids: list[str]) -> list[dict]:
    unique_ids = list(dict.fromkeys(ids))
    return firebase_get_listings(unique_ids)


def _get_firestore():
    import firebase_admin
    from firebase_admin import firestore
    try:
        firebase_admin.get_app()
    except ValueError:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and os.path.isfile(cred_path):
            firebase_admin.initialize_app()
        else:
            key_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
            if key_json:
                firebase_admin.initialize_app(credential=firebase_admin.credentials.Certificate(json.loads(key_json)))
            else:
                try:
                    firebase_admin.initialize_app()
                except Exception:
                    return None
    return firestore.client()


_CACHE_COLLECTION = "match_cache"
# Bump when cached MatchItem shape/content must be regenerated (e.g. hiding stale deadlines).
_CACHE_VERSION = "v2-no-deadlines"


def _cache_key(profile_json: str, program_ids: list[str]) -> str:
    payload = _CACHE_VERSION + "|" + profile_json + "|" + ",".join(sorted(program_ids))
    return hashlib.sha256(payload.encode()).hexdigest()


def get_cached_matches(profile_json: str, program_ids: list[str]) -> list[dict] | None:
    """Return cached MatchItem list if found, else None."""
    try:
        db = _get_firestore()
        if not db:
            return None
        key = _cache_key(profile_json, program_ids)
        doc = db.collection(_CACHE_COLLECTION).document(key).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return data.get("matches")
    except Exception:
        return None


def get_listings_count() -> int | None:
    """Return number of assistance listings in Firebase, or None on error."""
    return firebase_get_listings_count()


def set_cached_matches(profile_json: str, program_ids: list[str], matches: list[dict]) -> None:
    try:
        db = _get_firestore()
        if not db:
            return
        key = _cache_key(profile_json, program_ids)
        db.collection(_CACHE_COLLECTION).document(key).set({"matches": matches}, merge=True)
    except Exception:
        pass
