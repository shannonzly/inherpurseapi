"""
FastAPI app: POST /api/match for benefit eligibility.
Run from project root: uvicorn backend.main:app --reload
"""
import json
import os
import sys
from pathlib import Path

# Project root on path for scripts.db and backend.*
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.firebase_client import get_cached_matches, get_listings_count, set_cached_matches
from backend.schemas import MatchItem, MatchRequest, MatchResponse
from backend.search import build_query, embed_query, get_listings_by_ids, vector_search
from backend.llm import filter_and_format

app = FastAPI(title="Benefit and Scholarship Eligibility API")

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_origins_raw:
    _allow_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
else:
    _allow_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://inherpurse.org",
        "https://www.inherpurse.org",
    ]

# Firebase Hosting (*.web.app, *.firebaseapp.com) and preview channels — set CORS_FIREBASE_REGEX=0 to disable
_cors_regex = os.environ.get("CORS_FIREBASE_REGEX", "1").strip().lower()
_allow_origin_regex = (
    r"https://.+\.web\.app$|https://.+\.firebaseapp\.com$"
    if _cors_regex not in ("0", "false", "no", "")
    else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/stats")
def stats():
    """Return Firebase and Pinecone counts so you can verify ingestion ran and data is present."""
    firebase_count = None
    pinecone_count = None
    try:
        firebase_count = get_listings_count()
    except Exception:
        pass
    try:
        import pinecone
        pc = pinecone.Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
        index_name = os.environ.get("PINECONE_INDEX_NAME", "benefit-eligibility")
        index = pc.Index(index_name)
        desc = index.describe_index_stats()
        pinecone_count = desc.get("total_vector_count")
    except Exception:
        pass
    return {
        "firebase_listing_count": firebase_count,
        "pinecone_vector_count": pinecone_count,
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "pinecone_configured": bool(os.environ.get("PINECONE_API_KEY")),
        "message": "Run ingestion (python3 -m scripts.ingestion) if either count is 0 or null.",
    }


def _openai_error_detail(e: Exception) -> str:
    """Return a user-friendly message for OpenAI API access issues."""
    msg = str(e).lower()
    if "api_key" in msg or "authentication" in msg or "invalid" in msg or "401" in msg:
        return "OpenAI API key invalid or missing. Set OPENAI_API_KEY in .env with a valid key from https://platform.openai.com/api-keys"
    if "rate" in msg or "quota" in msg or "429" in msg:
        return "OpenAI rate limit or quota exceeded. Check usage at https://platform.openai.com/usage"
    if "403" in msg or "access" in msg or "permission" in msg:
        return "OpenAI access denied. Check API key permissions and billing at https://platform.openai.com/account/billing"
    return f"OpenAI API error: {e}"


@app.post("/api/match", response_model=MatchResponse)
def match(request: MatchRequest):
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set. Add it to your .env file (see .env.example).",
        )
    try:
        query = build_query(request)
        vector = embed_query(query)
    except Exception as e:
        raise HTTPException(status_code=502, detail=_openai_error_detail(e))
    top_k = int(os.environ.get("MATCH_TOP_K", "25"))
    try:
        ids = vector_search(vector, top_k=top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vector search failed: {str(e)}")
    try:
        listings = get_listings_by_ids(ids) if ids else []
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Listings unavailable. Ensure Firebase is configured (GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_JSON).",
        ) from e
    # Use cache to avoid GPT call when same profile + same program set was already evaluated
    profile_json = json.dumps(request.model_dump(), sort_keys=True)
    cached = get_cached_matches(profile_json, ids)
    if cached is not None:
        matches = [MatchItem(**m) for m in cached]
        return MatchResponse(matches=matches, total=len(matches), candidates_evaluated=len(listings))
    try:
        matches = filter_and_format(request, listings)
    except Exception as e:
        raise HTTPException(status_code=502, detail=_openai_error_detail(e))
    set_cached_matches(profile_json, ids, [m.model_dump() for m in matches])
    return MatchResponse(matches=matches, total=len(matches), candidates_evaluated=len(listings))
