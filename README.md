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

## Local document verification

Open `http://127.0.0.1:8000/admin/documents` to:

- Review every extracted section beside the original PDF.
- Correct headings and legal text while preserving the first extracted version
  for audit.
- Record official source URLs, publication dates, effective dates, and notes.
- Mark documents and individual sections with review statuses.
- Import selectable-text legal PDFs up to 25 MB.

Section corrections invalidate only the affected semantic vector. Run
`python -m scripts.build_embeddings` after completing a batch of corrections or
importing a new document. The administration workspace is currently intended
for local development only and must be protected with authentication before a
public deployment.

## Clarification and source support

Before generating an answer, the assistant can ask up to two deterministic
follow-up questions when missing facts may change the applicable law, such as
whether force was used during a property offence or whether physical injury
occurred.

Final answers include a server-controlled source-support state:

- `supported`: leading retrieved laws match the detected legal area.
- `limited`: potentially relevant material exists but needs more context.
- `insufficient`: retrieved material does not reliably support the question.

Gemini cannot choose or increase this state. Evaluation and clarification
preparation use local retrieval and consume no Gemini quota.

## Example

```json
POST /api/search
{
  "query": "My neighbour hit me and caused an injury",
  "limit": 6
}
```

## Important limitations

The current search combines auditable SQLite full-text retrieval with cached
local semantic embeddings. Extracted sections must still be reviewed against
the page images before this is used by the public. Document effective dates and
official source URLs also need legal verification.


## Publication review requirements

New imports remain outside public search until every extracted section is reviewed
and the administrator uses **Approve & publish**. Documents without metadata are
also excluded. Publication requires an HTTP(S) source URL, a valid publication date,
and an effective date for Acts, amendments, regulations, and gazettes. Dates must
use YYYY-MM-DD and must not be in the future. Unknown dates must be resolved before
publishing these document types; do not invent a date.

The reviewer must confirm that all original pages were compared for extraction
completeness and that source authority and dates were checked. These are human
attestations, not automated legal verification. Detected amendment relationships
remain proposals; publishing does not automatically approve or apply amendments.

Saving section reviews or changing published document details withdraws the source
until it is approved again. Keyword search updates immediately. Refresh semantic
search separately with `python -m scripts.build_embeddings`.

Upload one selectable-text PDF (25 MB maximum) at a time. Scanned PDFs need OCR
before PDF import, or their pages can be uploaded as images. Automatic current-law
version resolution and automatic semantic-index rebuilding are not implemented.
