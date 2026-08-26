"""
Daily ingestion: GSA SAM.gov API at most ONCE per day -> Firebase Firestore + Pinecone.
All listing data is stored in Firebase; SAM is only called when we have not yet fetched today (UTC).
Run from project root: python -m scripts.ingestion
"""
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Add project root and load .env from project root (so Firebase/Pinecone env vars are set)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.firebase_store import (
    _firebase_credentials_ok,
    get_all_listings_for_embedding,
    sam_already_fetched_today,
    set_sam_fetched_today,
    write_listings,
)
from scripts.csv_ingestion import get_csv_path, load_listings_from_csv

BASE_URL = "https://api.sam.gov/assistance-listings/v1/search"
PAGE_SIZE = 100  # SAM.gov API max is 100


def fetch_all_listings(api_key: str) -> tuple[list[dict], bool]:
    """Fetch from SAM.gov (pageNumber starts at 1). Returns (listings, completed_fully)."""
    import time
    all_programs: list[dict] = []
    page = 1
    completed_fully = True
    while True:
        params = {
            "api_key": api_key,
            "status": "Active",
            "pageSize": PAGE_SIZE,
            "pageNumber": page,
        }
        response = requests.get(BASE_URL, params=params, timeout=60)
        if response.status_code == 429:
            print("SAM.gov rate limit (429). Saving partial results. Retry after the time given in the error.")
            completed_fully = False
            break
        if response.status_code != 200:
            print(f"Error: {response.status_code} {response.text[:200]}")
            completed_fully = False
            break
        data = response.json()
        listings = data.get("assistanceListingsData") or []
        if not listings:
            break
        all_programs.extend(listings)
        total_records = data.get("totalRecords") or 0
        print(f"Fetched page {page}/{data.get('totalPages') or '?'}; total so far {len(all_programs)}/{total_records or len(all_programs)}")
        if len(listings) < PAGE_SIZE or (total_records and len(all_programs) >= total_records):
            break
        page += 1
        time.sleep(1.5)  # Reduce chance of 429
    return all_programs, completed_fully


def build_document_text(record: dict) -> str:
    parts = [
        record.get("title") or "",
        record.get("overview_objective") or "",
        record.get("overview_description") or "",
        record.get("applicant_description") or "",
        record.get("beneficiary_description") or "",
        record.get("assistance_types") or "",
    ]
    return " ".join(p for p in parts if p).strip() or record.get("assistance_listing_id", "")


def index_to_pinecone(listings_for_embed: list[dict], index_name: str) -> None:
    try:
        from openai import OpenAI
        from pinecone import Pinecone, ServerlessSpec
    except ImportError as e:
        print("Skipping Pinecone: install openai and pinecone-client.", e)
        return

    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("PINECONE_API_KEY not set; skipping vector index.")
        return

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("OPENAI_API_KEY not set; skipping vector index.")
        return

    EMBED_DIM = 1536  # text-embedding-3-small
    pc = Pinecone(api_key=api_key)
    if index_name not in [idx.name for idx in pc.list_indexes()]:
        pc.create_index(
            name=index_name,
            dimension=EMBED_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
    index = pc.Index(index_name)
    try:
        stats = index.describe_index_stats()
        existing_dim = stats.get("dimension")
        if existing_dim is not None and existing_dim != EMBED_DIM:
            print(
                f"Pinecone index '{index_name}' has dimension {existing_dim}, but embeddings are {EMBED_DIM}. "
                f"Create a new index with dimension {EMBED_DIM} in the Pinecone console and set PINECONE_INDEX_NAME to it, "
                f"or delete the index '{index_name}' so this script can recreate it."
            )
            return
    except Exception:
        pass

    client = OpenAI(api_key=openai_key)
    embed_model = "text-embedding-3-small"
    batch_size = 50
    vectors = []
    for i in range(0, len(listings_for_embed), batch_size):
        batch = listings_for_embed[i : i + batch_size]
        texts = [build_document_text(r) for r in batch]
        resp = None
        for attempt in range(6):
            try:
                resp = client.embeddings.create(input=texts, model=embed_model)
                break
            except Exception as e:
                err_name = type(e).__name__
                if err_name not in ("RateLimitError", "APIConnectionError") or attempt >= 5:
                    raise
                time.sleep(min(2 ** attempt, 30))
        if resp is None:
            continue
        time.sleep(0.5)
        for j, emb in enumerate(resp.data):
            rec = batch[j]
            aid = rec["assistance_listing_id"]
            vectors.append({
                "id": aid.replace(".", "_").replace(" ", "_"),
                "values": emb.embedding,
                "metadata": {
                    "assistance_listing_id": aid,
                    "title": (rec.get("title") or "")[:1000],
                },
            })
    if vectors:
        try:
            for chunk in (vectors[k : k + 100] for k in range(0, len(vectors), 100)):
                index.upsert(vectors=chunk)
            print(f"Upserted {len(vectors)} vectors to Pinecone index {index_name}.")
        except Exception as e:
            err = str(e).lower()
            if "dimension" in err or "1536" in err or "1024" in err:
                print(
                    "Pinecone dimension mismatch: this app uses 1536-dim vectors (OpenAI text-embedding-3-small). "
                    f"Your index has a different dimension. Create a new index in Pinecone with dimension 1536, "
                    f"set PINECONE_INDEX_NAME to that index name, and run ingestion again."
                )
            raise


def main() -> None:
    csv_path = get_csv_path()
    use_csv = os.environ.get("ASSISTANCE_LISTINGS_CSV") or csv_path.is_file()

    if use_csv:
        # Bulk CSV: no SAM.gov API or quota needed.
        if not _firebase_credentials_ok():
            print("Firebase credentials not set. Add to .env (copy from .env.example if needed):")
            print("  GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your-firebase-service-account.json")
            print("Get the JSON from Firebase Console → Project Settings → Service Accounts → Generate new private key.")
            sys.exit(1)
        print(f"Loading from CSV: {csv_path}")
        try:
            listings = load_listings_from_csv(csv_path)
        except FileNotFoundError as e:
            print(e)
            print("Set ASSISTANCE_LISTINGS_CSV in .env to the CSV path, or place the file at data/AssistanceListings_DataGov_PUBLIC_CURRENT.csv")
            sys.exit(1)
        if not listings:
            print("No rows in CSV.")
            sys.exit(1)
        print(f"Loaded {len(listings)} listings from CSV.")
        n = write_listings(listings)
        # Grants.gov merges are additive; do not block the next SAM.gov fetch.
        merge_sources = {"Grants.gov", "California Grants Portal"}
        grants_csv = bool(
            listings
            and all((x.get("sourceSystem") in merge_sources) for x in listings)
        )
        if not grants_csv:
            set_sam_fetched_today()
        print(f"Wrote {n} listings to Firebase.")
    else:
        # SAM API path (once per day).
        if sam_already_fetched_today():
            print("SAM already fetched today (UTC). Skipping SAM API call; refreshing Pinecone from Firebase...")
            try:
                listings_for_embed = get_all_listings_for_embedding()
                if listings_for_embed:
                    index_name = os.environ.get("PINECONE_INDEX_NAME", "benefit-eligibility")
                    index_to_pinecone(listings_for_embed, index_name)
                    print(f"Refreshed Pinecone with {len(listings_for_embed)} listings from Firebase.")
                else:
                    print("No listings in Firebase. Run ingestion on a new day to fetch from SAM, or use CSV (see ASSISTANCE_LISTINGS_CSV).")
            except Exception as e:
                print(f"Could not refresh Pinecone from Firebase: {e}")
            sys.exit(0)

        api_key = os.environ.get("SAM_GOV_API_KEY")
        if not api_key:
            print("Set SAM_GOV_API_KEY in .env (or use CSV: set ASSISTANCE_LISTINGS_CSV and place the bulk CSV file).")
            sys.exit(1)

        print("Fetching from GSA SAM API (once today)...")
        listings, completed_fully = fetch_all_listings(api_key)
        if not listings:
            print("No listings fetched.")
            sys.exit(1)

        print("Writing listings to Firebase...")
        n = write_listings(listings)
        if completed_fully:
            set_sam_fetched_today()
        else:
            print("Partial run (e.g. rate limited). Not marking SAM as fetched today so next run will retry.")
        print(f"Wrote {n} listings to Firebase.")

    listings_for_embed = get_all_listings_for_embedding()
    index_name = os.environ.get("PINECONE_INDEX_NAME", "benefit-eligibility")
    index_to_pinecone(listings_for_embed, index_name)
    print("Ingestion complete.")


if __name__ == "__main__":
    main()
