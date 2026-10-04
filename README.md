# Benefit and Scholarship Eligibility AI

RAG app that helps people find federal and state assistance programs that may fit their profile

## Methodology

### Data

Program listings are loaded by `scripts/ingestion.py` (or merged in with `scripts/import_grants_csv.py`) and stored in Firebase Firestore. The same records are embedded into Pinecone for search.

Each listing is normalized to a common shape (title, overview, applicant/beneficiary eligibility text, deadlines, award types, application URLs) whether it came from SAM, Grants.gov, or California.

### Ingestion

1. Read listings from CSV and/or SAM.gov API.
2. Write or merge documents into Firestore (`listings` collection).
3. Build a text blob per program (title, objective, description, eligibility fields, assistance types).
4. Embed those blobs with OpenAI `text-embedding-3-small` (1536 dimensions).
5. Upsert vectors into the Pinecone index `benefit-eligibility` (cosine similarity).

Re-run ingestion when you want fresher data. SAM API calls are rate-limited, so the script skips the API if it already ran successfully that UTC day; CSV loads always apply immediately.

### Search pipeline (each request)

This is a two-stage (retrieve -> reason) flow, not a keyword search over the whole catalog.

```
User profile (form)
       │
       ▼
  Text query          e.g. "Age 22, state CA, veteran, major nursing"
       │
       ▼
  OpenAI embedding    same model as ingestion (text-embedding-3-small)
       │
       ▼
  Pinecone top-K      default K=25; cosine similarity picks the most
       │              semantically similar programs (not just all listings)
       ▼
  Firestore lookup    full listing JSON for those IDs
       │
       ▼
  GPT eligibility     reads profile + program summaries; returns up to 10
       │              ranked results with match quality, deadlines, how to apply
       ▼
  Cache (Firestore)   same profile + same candidate IDs → skip GPT next time
       │
       ▼
  Frontend results    shown with optional client-side re-sort
```

Vector Search narrows programs to a small candidate set based on fit (who the program serves, what it funds, eligibility language). Then pass to GPT to read candidates against only the fields the user actually filled in:

- Missing profile fields are not treated as disqualifiers; the model notes “Check program for: …” when something can’t be verified.
- A program is excluded only when the user clearly fails a stated requirement (e.g. non-citizen vs citizens-only).
- Each result gets a match quality label: Full match, Strong match, or Partial match, ordered best-first.

User profiles are not persisted; only match results are cached (keyed by profile JSON + candidate program IDs) to save API cost on repeat searches.

### Sorting results

The API returns programs in relevance order (GPT ranking). The frontend can re-order that same list without another API call:

| Sort option | Behavior |
|-------------|----------|
| Relevance (default) | Keeps GPT order — best semantic + eligibility fit first |
| Award type | Alphabetical by assistance type (Grant, Loan, Direct Payment, etc.) |
| Timeliness | Alphabetical by how quickly benefits may take effect |

Sorting does not re-run vector search or GPT; it only rearranges the matched set.

## Stack

- Ingestion: Python script → CSV (Data.gov) or GSA SAM.gov API → Firestore + Pinecone
- Backend: FastAPI, OpenAI embeddings + Pinecone search; GPT used only when needed (results cached in Firebase)
- Frontend: Next.js (TypeScript, Tailwind), form + results with sort


## Project layout

- `package.json` – local `firebase-tools` (use `npx firebase` / `npm run deploy:hosting`; avoids global npm permission errors)
- `scripts/` – ingestion script, Firebase store (listings + SAM date), Pinecone index
- `backend/` – FastAPI app, Firebase client (listings + match cache), search, LLM
- `frontend/` – Next.js app
