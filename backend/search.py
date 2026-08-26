"""Build profile query, embed, and search Pinecone + DB."""
import os
import sys
from pathlib import Path

# Ensure project root on path when running backend
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from backend.schemas import MatchRequest


def build_query(req: MatchRequest) -> str:
    parts = []
    if req.age is not None:
        parts.append(f"Age {req.age}")
    if req.state:
        parts.append(f"state {req.state}")
    if req.zip_code:
        parts.append(f"zip {req.zip_code}")
    if req.citizenship_status:
        parts.append(f"citizenship {req.citizenship_status}")
    if req.ethnicity_background:
        parts.append(f"ethnicity {req.ethnicity_background}")
    if req.current_grade_year:
        parts.append(f"grade {req.current_grade_year}")
    if req.gpa is not None:
        parts.append(f"GPA {req.gpa}")
    if req.major_area_of_study:
        parts.append(f"major {req.major_area_of_study}")
    if req.first_generation is True:
        parts.append("first-generation")
    if req.annual_household_income is not None:
        parts.append(f"household income {req.annual_household_income}")
    if req.employment_status:
        parts.append(f"employment {req.employment_status}")
    if req.fafsa_status:
        parts.append(f"FAFSA {req.fafsa_status}")
    if req.veteran_status is True:
        parts.append("veteran")
    if req.disability_status is True:
        parts.append("disability")
    if req.parental_status:
        parts.append(f"parental status {req.parental_status}")
    if req.free_text:
        parts.append(req.free_text)
    return ". ".join(parts) if parts else "benefits and assistance programs"


def embed_query(query: str) -> list[float]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    resp = client.embeddings.create(input=[query], model="text-embedding-3-small")
    return resp.data[0].embedding


def vector_search(vector: list[float], top_k: int = 10) -> list[str]:
    import pinecone
    pc = pinecone.Pinecone(api_key=os.environ.get("PINECONE_API_KEY"))
    index_name = os.environ.get("PINECONE_INDEX_NAME", "benefit-eligibility")
    index = pc.Index(index_name)
    result = index.query(vector=vector, top_k=top_k, include_metadata=True)
    ids = []
    for match in (result.get("matches") or []):
        mid = match.get("id")
        if mid:
            # We stored id with dots/space replaced by underscore
            aid = (match.get("metadata") or {}).get("assistance_listing_id") or mid.replace("_", ".")
            ids.append(aid)
    return ids


def get_listings_by_ids(ids: list[str]) -> list[dict]:
    from backend.firebase_client import get_listings_by_ids as fb_get
    return fb_get(ids)
