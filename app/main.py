from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.admin import (
    document_sections,
    initialize_admin,
    list_documents,
    resolve_pdf,
    save_document_metadata,
    save_section_review,
)
from app.config import get_settings
from app.evaluation import load_cases, run_evaluation
from app.models import (
    AdminSection,
    ChatRequest,
    ChatResponse,
    DocumentMetadataUpdate,
    DocumentSummary,
    EvaluationSummary,
    PrepareRequest,
    PrepareResponse,
    SearchRequest,
    SearchResponse,
    SectionReviewUpdate,
)
from app.rag.extract import extract_pdf
from app.rag.store import (
    initialize,
    replace_document_chunks,
    retrieval_mode,
    search,
)
from app.services.legal_answer import answer_question
from app.services.language import detect_language
from app.services.query_analysis import assess_query


@asynccontextmanager
async def lifespan(_: FastAPI):
    database = get_settings().legal_ai_database
    initialize(database)
    initialize_admin(database)
    yield


app = FastAPI(
    title="Sri Lankan Legal AI API",
    version="0.1.0",
    description="Source-grounded legal information. Not legal advice.",
    lifespan=lifespan,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/evaluation", include_in_schema=False)
def evaluation_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "evaluation.html")


@app.get("/admin/documents", include_in_schema=False)
def document_admin_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "documents.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def legal_search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    sources = search(settings.legal_ai_database, request.query, request.limit)
    return SearchResponse(
        query=request.query,
        sources=sources,
        retrieval_mode=retrieval_mode(settings.legal_ai_database),
    )


@app.get("/api/evaluations/cases")
def evaluation_cases() -> list[dict]:
    return load_cases()


@app.post("/api/evaluations/run", response_model=EvaluationSummary)
def evaluate_retrieval() -> EvaluationSummary:
    return run_evaluation(get_settings().legal_ai_database)


@app.get("/api/admin/documents", response_model=list[DocumentSummary])
def admin_documents() -> list[DocumentSummary]:
    return list_documents(get_settings().legal_ai_database)


@app.get(
    "/api/admin/documents/{source_file:path}/sections",
    response_model=list[AdminSection],
)
def admin_document_sections(source_file: str) -> list[AdminSection]:
    sections = document_sections(get_settings().legal_ai_database, source_file)
    if not sections:
        raise HTTPException(status_code=404, detail="Document not found.")
    return sections


@app.put("/api/admin/documents/{source_file:path}")
def update_document_metadata(
    source_file: str, update: DocumentMetadataUpdate
) -> dict[str, str]:
    try:
        save_document_metadata(get_settings().legal_ai_database, source_file, update)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    return {"status": "saved"}


@app.put("/api/admin/sections/{source_id}")
def update_section_review(
    source_id: str, update: SectionReviewUpdate
) -> dict[str, str]:
    try:
        save_section_review(get_settings().legal_ai_database, source_id, update)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Section not found.") from exc
    return {"status": "saved", "semantic_index": "refresh_required"}


@app.get("/api/admin/documents/{source_file:path}/pdf")
def admin_document_pdf(source_file: str) -> FileResponse:
    known = document_sections(get_settings().legal_ai_database, source_file)
    path = resolve_pdf(WORKSPACE_DIR, source_file)
    if not known or path is None:
        raise HTTPException(status_code=404, detail="PDF not found.")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{source_file}"'},
    )


@app.post("/api/admin/documents/upload", response_model=DocumentSummary)
async def upload_document(
    title: str = Form(min_length=3, max_length=300),
    document: UploadFile = File(),
) -> DocumentSummary:
    filename = Path(document.filename or "").name
    if not filename.lower().endswith(".pdf") or filename != document.filename:
        raise HTTPException(status_code=400, detail="Choose a valid PDF file.")
    if resolve_pdf(WORKSPACE_DIR, filename):
        raise HTTPException(
            status_code=409, detail="A document with this filename already exists."
        )

    upload_dir = WORKSPACE_DIR / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / filename
    size = 0
    header = b""
    try:
        with destination.open("wb") as stream:
            while chunk := await document.read(1024 * 1024):
                if not header:
                    header = chunk[:5]
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise ValueError("PDF exceeds the 25 MB local import limit.")
                stream.write(chunk)
        if header != b"%PDF-":
            raise ValueError("The uploaded file is not a valid PDF.")
        chunks = extract_pdf(destination, title.strip())
        if not chunks:
            raise ValueError("No numbered legal sections could be extracted.")
        replace_document_chunks(
            get_settings().legal_ai_database,
            filename,
            chunks,
        )
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail="The PDF could not be extracted. It may require OCR or manual review.",
        ) from exc
    finally:
        await document.close()

    documents = list_documents(get_settings().legal_ai_database)
    return next(item for item in documents if item.source_file == filename)


@app.post("/api/chat", response_model=ChatResponse)
def legal_chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()
    sources = search(settings.legal_ai_database, request.message, settings.legal_ai_top_k)
    try:
        return answer_question(request.message, sources, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="The grounded answer service is temporarily unavailable."
        ) from exc


@app.post("/api/prepare", response_model=PrepareResponse)
def prepare_legal_question(request: PrepareRequest) -> PrepareResponse:
    settings = get_settings()
    sources = search(
        settings.legal_ai_database,
        request.message,
        settings.legal_ai_top_k,
    )
    assessment = assess_query(request.message, sources)
    return PrepareResponse(
        confidence_state=assessment.confidence_state,
        confidence_reason=assessment.confidence_reason,
        detected_domain=assessment.detected_domain,
        detected_language=detect_language(request.message),
        follow_up_questions=assessment.follow_up_questions,
        retrieval_mode=retrieval_mode(settings.legal_ai_database),
    )
