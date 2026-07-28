# Sri Lankan Legal AI

An early source-grounded legal information API for Sri Lanka. It retrieves
sections from supplied legislation before asking Gemini to explain them. It is
not a substitute for a lawyer.

## Current vertical slice

- Extracts section-like units from the supplied PDFs.
- Stores section text and page provenance in SQLite.
- Provides full-text retrieval through `POST /api/search`.
- Provides grounded structured answers through `POST /api/chat`.
- Refuses to invent an answer when no source is found.
- Runs retrieval without an API key; Gemini explanation is enabled only when
  `GEMINI_API_KEY` is configured.

The default chat model is the rolling `gemini-flash-latest` alias so the app
does not depend on a retired numbered model. Override it with
`GEMINI_CHAT_MODEL` only after confirming that the replacement model is
available to your API key.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m scripts.ingest
fastapi dev app/main.py
```

Open `http://127.0.0.1:8000` to use the web interface or
`http://127.0.0.1:8000/docs` to try the API directly.

## Web interface

The responsive frontend includes:

- Guided scenario entry and sample situations.
- Clear loading, answer, insufficient-evidence, and error states.
- Structured legal references and practical next steps.
- Expandable source text with exact document and page provenance.
- Privacy reminders, legal disclaimers, keyboard support, and reduced-motion
  support.

## Local semantic search

The app uses `BAAI/bge-small-en-v1.5` locally through FastEmbed. It does not
consume Gemini quota. Build or refresh the cached semantic index after document
ingestion:

```powershell
python -m scripts.build_embeddings
```

Keyword and semantic rankings are combined with Reciprocal Rank Fusion. If the
semantic index has not been built, the application safely falls back to keyword
retrieval.

Open `http://127.0.0.1:8000/evaluation` to run the draft retrieval evaluation.
The evaluation does not call Gemini.

## Example

```json
POST /api/search
{
  "query": "My neighbour hit me and caused an injury",
  "limit": 6
}
```

## Important limitations

The current search is auditable SQLite full-text retrieval. Semantic embeddings
and hybrid re-ranking are the next milestone. Extracted sections must be
reviewed against the page images before this is used by the public. Document
effective dates and official source URLs also need legal verification.
