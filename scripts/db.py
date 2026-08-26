"""SQLite schema and upsert for GSA assistance listings."""
import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "listings.db"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS assistance_listings (
            assistance_listing_id TEXT PRIMARY KEY,
            version TEXT,
            status TEXT,
            published_date TEXT,
            title TEXT,
            popular_long_name TEXT,
            program_web_page TEXT,
            overview_objective TEXT,
            overview_description TEXT,
            applicant_description TEXT,
            beneficiary_description TEXT,
            deadlines_value TEXT,
            deadlines_description TEXT,
            application_url TEXT,
            application_procedure_description TEXT,
            approval_interval TEXT,
            approval_description TEXT,
            renewal_interval TEXT,
            renewal_description TEXT,
            assistance_types TEXT,
            beneficiary_type_codes TEXT,
            applicant_type_codes TEXT,
            raw_json TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_listings_status ON assistance_listings(status);
    """)
    conn.commit()


def _extract(listing: dict[str, Any], *path: str) -> str | None:
    v = listing
    for key in path:
        if not isinstance(v, dict):
            return None
        v = v.get(key)
    if v is None or (isinstance(v, (list, dict)) and not v):
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v) if v else None
    return str(v)


def _join_names(items: list[dict] | None) -> str:
    if not items or not isinstance(items, list):
        return ""
    return " ".join(
        (x.get("name") or x.get("code") or "")
        for x in items
        if isinstance(x, dict)
    ).strip()


def listing_to_row(listing: dict[str, Any]) -> tuple:
    aid = listing.get("assistanceListingId") or listing.get("programId") or ""
    overview = listing.get("overview") or {}
    criteria = listing.get("criteriaForApplying") or {}
    app = listing.get("assistanceApplication") or {}
    appl_proc = (app.get("applicationProcedure") or {}) if isinstance(app, dict) else {}
    deadlines = (app.get("deadlines") or {}) if isinstance(app, dict) else {}
    applicant = criteria.get("applicant") or {}
    beneficiary = criteria.get("beneficiary") or {}
    fin = listing.get("financialInformation") or {}
    obligations = fin.get("obligations") or [] if isinstance(fin, dict) else []
    type_names = []
    for ob in obligations:
        if isinstance(ob, dict):
            at = ob.get("assistanceType") or {}
            if isinstance(at, dict) and at.get("name"):
                type_names.append(at["name"])
    assistance_types_str = "; ".join(type_names) if type_names else _extract(listing, "financialInformation", "obligations")
    applicant_types = applicant.get("types") if isinstance(applicant, dict) else []
    beneficiary_types = beneficiary.get("types") if isinstance(beneficiary, dict) else []
    applicant_codes = " ".join(
        (t.get("code") or "") for t in (applicant_types or []) if isinstance(t, dict)
    )
    beneficiary_codes = " ".join(
        (t.get("code") or "") for t in (beneficiary_types or []) if isinstance(t, dict)
    )
    app_url = appl_proc.get("url") or appl_proc.get("URL")
    return (
        aid,
        listing.get("version"),
        listing.get("status"),
        _extract(listing, "publishedDate"),
        listing.get("title"),
        listing.get("popularLongName"),
        listing.get("programWebPage"),
        overview.get("objective"),
        overview.get("assistanceListingDescription"),
        applicant.get("description") if isinstance(applicant, dict) else None,
        beneficiary.get("description") if isinstance(beneficiary, dict) else None,
        deadlines.get("value") if isinstance(deadlines, dict) else None,
        deadlines.get("description") if isinstance(deadlines, dict) else None,
        app_url,
        appl_proc.get("description") if isinstance(appl_proc, dict) else None,
        (app.get("approval") or {}).get("interval") if isinstance(app.get("approval"), dict) else None,
        (app.get("approval") or {}).get("description") if isinstance(app.get("approval"), dict) else None,
        (app.get("renewal") or {}).get("interval") if isinstance(app.get("renewal"), dict) else None,
        (app.get("renewal") or {}).get("description") if isinstance(app.get("renewal"), dict) else None,
        assistance_types_str if isinstance(assistance_types_str, str) else None,
        beneficiary_codes,
        applicant_codes,
        json.dumps(listing, default=str),
    )


def upsert_listings(conn: sqlite3.Connection, listings: list[dict[str, Any]]) -> int:
    init_schema(conn)
    rows = []
    for listing in listings:
        try:
            rows.append(listing_to_row(listing))
        except Exception:
            continue
    conn.executemany("""
        INSERT INTO assistance_listings (
            assistance_listing_id, version, status, published_date, title, popular_long_name,
            program_web_page, overview_objective, overview_description, applicant_description,
            beneficiary_description, deadlines_value, deadlines_description, application_url,
            application_procedure_description, approval_interval, approval_description,
            renewal_interval, renewal_description, assistance_types, beneficiary_type_codes,
            applicant_type_codes, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(assistance_listing_id) DO UPDATE SET
            version=excluded.version, status=excluded.status, published_date=excluded.published_date,
            title=excluded.title, popular_long_name=excluded.popular_long_name,
            program_web_page=excluded.program_web_page, overview_objective=excluded.overview_objective,
            overview_description=excluded.overview_description, applicant_description=excluded.applicant_description,
            beneficiary_description=excluded.beneficiary_description, deadlines_value=excluded.deadlines_value,
            deadlines_description=excluded.deadlines_description, application_url=excluded.application_url,
            application_procedure_description=excluded.application_procedure_description,
            approval_interval=excluded.approval_interval, approval_description=excluded.approval_description,
            renewal_interval=excluded.renewal_interval, renewal_description=excluded.renewal_description,
            assistance_types=excluded.assistance_types, beneficiary_type_codes=excluded.beneficiary_type_codes,
            applicant_type_codes=excluded.applicant_type_codes, raw_json=excluded.raw_json,
            updated_at=datetime('now')
    """, rows)
    conn.commit()
    return len(rows)


def get_listing_by_id(conn: sqlite3.Connection, assistance_listing_id: str) -> dict | None:
    row = conn.execute(
        "SELECT raw_json FROM assistance_listings WHERE assistance_listing_id = ?",
        (assistance_listing_id,),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def get_listings_by_ids(conn: sqlite3.Connection, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT raw_json FROM assistance_listings WHERE assistance_listing_id IN ({placeholders})",
        ids,
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def get_all_listings_for_embedding(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT assistance_listing_id, title, overview_objective, overview_description, "
        "applicant_description, beneficiary_description, assistance_types, raw_json FROM assistance_listings WHERE status = 'Active'"
    ).fetchall()
    return [
        {
            "assistance_listing_id": r[0],
            "title": r[1] or "",
            "overview_objective": r[2] or "",
            "overview_description": r[3] or "",
            "applicant_description": r[4] or "",
            "beneficiary_description": r[5] or "",
            "assistance_types": r[6] or "",
            "raw_json": json.loads(r[7]) if r[7] else {},
        }
        for r in rows
    ]
