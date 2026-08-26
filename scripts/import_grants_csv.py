"""
Merge a supplemental grants CSV into Firestore, then re-embed all listings
(existing SAM/Data.gov + new rows) into Pinecone.

Supported formats:
  - Grants.gov / simpler.grants.gov (opportunity_id, opportunity_title, …)
  - California Grants Portal (PortalID, AgencyDept, GrantURL, …)

Does not update SAM "fetched today" metadata (unlike a full SAM ingestion run).

Usage (from repo root, with .env loaded):

  python3 -m scripts.import_grants_csv /path/to/export.csv

Or set GRANTS_LISTINGS_CSV to the file path and run without arguments.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from scripts.csv_ingestion import load_listings_from_csv  # noqa: E402
from scripts.firebase_store import (  # noqa: E402
    _firebase_credentials_ok,
    get_all_listings_for_embedding,
    write_listings,
)
from scripts.ingestion import index_to_pinecone  # noqa: E402


def main() -> None:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    csv_path = Path(arg).expanduser().resolve() if arg else Path(
        os.environ.get("GRANTS_LISTINGS_CSV", "")
    ).expanduser().resolve()
    if not str(csv_path):
        print("Usage: python3 -m scripts.import_grants_csv /path/to/export.csv")
        print("Or set GRANTS_LISTINGS_CSV in .env.")
        sys.exit(1)
    if not csv_path.is_file():
        print(f"File not found: {csv_path}")
        sys.exit(1)
    if not _firebase_credentials_ok():
        print("Firebase credentials missing or GOOGLE_APPLICATION_CREDENTIALS is not a readable file.")
        print("Fix: set GOOGLE_APPLICATION_CREDENTIALS in .env to the absolute path of your")
        print("service account JSON (no stray quotes/spaces), or set FIREBASE_SERVICE_ACCOUNT_JSON.")
        env_file = ROOT / ".env"
        if env_file.is_file():
            try:
                from dotenv import dotenv_values

                raw = (dotenv_values(env_file).get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
                if raw:
                    p = Path(raw.strip("'\"")).expanduser()
                    print(f"  .env lists: {p} → exists: {p.is_file()}")
            except Exception:
                pass
        sys.exit(1)

    print(f"Loading grants CSV: {csv_path}")
    listings = load_listings_from_csv(csv_path)
    if not listings:
        print("No rows parsed (wrong format?).")
        sys.exit(1)
    sources = {x.get("sourceSystem") for x in listings}
    allowed = {"Grants.gov", "California Grants Portal"}
    if not sources <= allowed:
        print(
            "Unrecognized CSV format. Expected Grants.gov "
            "(opportunity_id, opportunity_title) or California Grants Portal "
            "(PortalID, AgencyDept, GrantURL)."
        )
        sys.exit(1)
    label = ", ".join(sorted(sources))
    print(f"Detected source(s): {label} ({len(listings)} rows).")

    n = write_listings(listings)
    print(f"Merged {n} grant opportunities into Firestore (assistance_listings).")

    listings_for_embed = get_all_listings_for_embedding()
    index_name = os.environ.get("PINECONE_INDEX_NAME", "benefit-eligibility")
    index_to_pinecone(listings_for_embed, index_name)
    print(f"Pinecone refresh complete ({len(listings_for_embed)} total vectors).")


if __name__ == "__main__":
    main()
