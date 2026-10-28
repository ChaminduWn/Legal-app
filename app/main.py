from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from uuid import uuid4
from dataclasses import replace
from app.storage import store_file, register_asset, resolve_asset
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.admin import (
    document_sections,
    initialize_admin,
    list_documents,
    resolve_pdf,
    save_document_metadata,
    save_section_review,
    register_import,
    approve_document,
    relationships,
    audit_events,
)
from app.config import get_settings
from app.evaluation import load_cases, run_evaluation
from app.models import (
    AdminSection,
    PublicationApproval,
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
    LegalRelationship,
    AuditEvent,
    ChangePasswordRequest,
)
from app.rag.extract import extract_pdf, extract_images
from app.rag.store import (
    initialize,
    connect,
    replace_document_chunks,
    retrieval_mode,
    search,
)
from app.services.legal_answer import answer_question
from app.services.language import detect_language
from app.services.query_analysis import assess_query
from app.auth import COOKIE_NAME, create_session, valid_credentials, valid_session, update_admin_password
from app.site_content import get_site_content, save_site_content, reset_site_content


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


@app.middleware("http")
async def protect_admin(request: Request, call_next):
    path = request.url.path
    protected = path == "/admin/documents" or path.startswith("/api/admin/")
    if protected and not valid_session(request.cookies.get(COOKIE_NAME), get_settings()):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Admin sign-in required."}, status_code=401)
        return RedirectResponse("/admin/login", status_code=303)
    response = await call_next(request)
    if protected or path in {"/static/documents.js", "/static/documents.css"}:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/evaluation", include_in_schema=False)
def evaluation_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "evaluation.html")


@app.get("/admin/documents", include_in_schema=False)
def document_admin_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "documents.html")


@app.get("/admin/login", include_in_schema=False)
def admin_login_frontend(request: Request):
    if valid_session(request.cookies.get(COOKIE_NAME), get_settings()):
        return RedirectResponse("/admin/documents", status_code=303)
    return FileResponse(FRONTEND_DIR / "admin-login.html")


@app.post("/admin/login", include_in_schema=False)
def admin_login(username: str = Form(), password: str = Form()):
    settings = get_settings()
    if not valid_credentials(username, password, settings, database=settings.legal_ai_database):
        return RedirectResponse("/admin/login?error=1", status_code=303)
    response = RedirectResponse("/admin/documents", status_code=303)
    response.set_cookie(COOKIE_NAME, create_session(username, settings), max_age=settings.admin_session_hours * 3600, httponly=True, samesite="strict", secure=settings.environment == "production", path="/")
    return response


@app.post("/admin/logout", include_in_schema=False)
def admin_logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def legal_search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    sources = search(settings.legal_ai_database, request.query, request.limit, request.as_of.isoformat() if request.as_of else None)
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
        with connect(get_settings().legal_ai_database) as connection:
            known = connection.execute("SELECT 1 FROM document_records WHERE source_file=?",(source_file,)).fetchone()
        if not known:
            raise HTTPException(status_code=404, detail="Document not found.")
    return sections


@app.put("/api/admin/documents/{source_file:path}")
def update_document_metadata(
    source_file: str, update: DocumentMetadataUpdate
) -> dict[str, str]:
    try:
        save_document_metadata(get_settings().legal_ai_database, source_file, update)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    settings = get_settings()
    with connect(settings.legal_ai_database) as connection:
        known = connection.execute("SELECT 1 FROM document_records WHERE source_file=?",(source_file,)).fetchone()
    path = resolve_asset(settings.legal_ai_database, settings.legal_ai_storage_root, source_file) or resolve_pdf(WORKSPACE_DIR, source_file)
    if not known or path is None:
        raise HTTPException(status_code=404, detail="PDF not found.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=Path(source_file).name,
        content_disposition_type="inline",
    )


@app.get("/api/admin/documents/{source_file:path}/original")
def admin_document_original(source_file: str, page: int = 1) -> FileResponse:
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be positive.")
    settings = get_settings()
    path = resolve_asset(settings.legal_ai_database, settings.legal_ai_storage_root, source_file, page)
    if path is None:
        # Compatibility for pre-migration image originals, only registered records.
        if not document_sections(settings.legal_ai_database, source_file):
            raise HTTPException(status_code=404, detail="Original file not found.")
        path = (WORKSPACE_DIR / "data" / "uploads" / Path(source_file).name).resolve()
        if not path.is_relative_to((WORKSPACE_DIR / "data" / "uploads").resolve()) or not path.is_file():
            raise HTTPException(status_code=404, detail="Original file not found.")
    return FileResponse(path)


@app.get("/api/admin/documents/{source_file:path}/relationships", response_model=list[LegalRelationship])
def admin_relationships(source_file: str) -> list[LegalRelationship]:
    return relationships(get_settings().legal_ai_database, source_file)


@app.get("/api/admin/documents/{source_file:path}/audit", response_model=list[AuditEvent])
def admin_audit(source_file: str) -> list[AuditEvent]:
    return audit_events(get_settings().legal_ai_database, source_file)


@app.post("/api/admin/documents/{source_file:path}/approve")
def admin_approve(source_file: str, approval: PublicationApproval) -> dict[str, str]:
    try:
        approve_document(get_settings().legal_ai_database, source_file, extraction_complete=approval.extraction_complete, authority_checked=approval.authority_checked)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "published"}


@app.get("/api/admin/library")
def admin_library_status():
    settings = get_settings()
    with connect(settings.legal_ai_database) as connection:
        scalar = lambda sql: connection.execute(sql).fetchone()[0]
        has_archive = connection.execute("SELECT 1 FROM sqlite_master WHERE name='archived_metadata'").fetchone()
        has_collections = connection.execute("SELECT 1 FROM sqlite_master WHERE name='collection_files'").fetchone()
        archived_pending = 0
        if has_archive:
            archived_pending = scalar("SELECT COUNT(*) FROM archived_metadata a WHERE NOT EXISTS (SELECT 1 FROM collection_files cf WHERE cf.origin_path=a.source_file OR cf.origin_path='step 001 pdf folder/' || a.source_file)") if has_collections else scalar("SELECT COUNT(*) FROM archived_metadata")
        return {
            "publication_policy": {"mode": settings.environment, "require_source_details": settings.environment == "production", "require_edition": settings.environment == "production"},
            "documents": scalar("SELECT COUNT(*) FROM document_records"),
            "sections": scalar("SELECT COUNT(*) FROM legal_chunks"),
            "dated_editions": scalar("SELECT COUNT(*) FROM document_versions"),
            "original_files": scalar("SELECT COUNT(*) FROM document_assets"),
            "embeddings": scalar("SELECT COUNT(*) FROM legal_embeddings"),
            "archived_inventory": archived_pending,
            "collection_files": scalar("SELECT COUNT(*) FROM collection_files") if has_collections else 0,
        }


@app.get("/api/admin/topics")
def admin_topics():
    with connect(get_settings().legal_ai_database) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM legal_topics ORDER BY name")]


@app.get("/api/site-content")
def read_site_content():
    return get_site_content(get_settings().legal_ai_database)


@app.post("/api/admin/change-password")
def admin_change_password(request: ChangePasswordRequest, req: Request):
    settings = get_settings()
    token = req.cookies.get(COOKIE_NAME)
    if not valid_session(token, settings):
        raise HTTPException(status_code=401, detail="Admin sign-in required.")
    if request.new_password != request.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    try:
        update_admin_password(
            settings.legal_ai_database,
            settings.admin_username,
            request.current_password,
            request.new_password,
            settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": "Password changed successfully."}


@app.put("/api/admin/site-content")
def update_admin_site_content(content: dict):
    if not isinstance(content, dict) or "en" not in content or "si" not in content:
        raise HTTPException(status_code=400, detail="Invalid site content format. Both 'en' and 'si' are required.")
    saved = save_site_content(get_settings().legal_ai_database, content)
    return {"status": "ok", "content": saved}


@app.post("/api/admin/site-content/reset")
def reset_admin_site_content():
    saved = reset_site_content(get_settings().legal_ai_database)
    return {"status": "ok", "content": saved}


def commit_import(settings, source_file, chunks, document_type, method, confidence, warnings, assets):
    if document_type not in {"act", "amendment", "regulation", "gazette", "judgment", "book", "other"}:
        raise ValueError("Unknown document type.")
    initialize_admin(settings.legal_ai_database)
    with connect(settings.legal_ai_database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM document_records WHERE source_file=?", (source_file,)).fetchone():
            raise ValueError("This document name already exists. Use a distinct filename for each edition.")
        replace_document_chunks(settings.legal_ai_database, source_file, chunks, connection=connection)
        register_import(settings.legal_ai_database, source_file, document_type, method, confidence, warnings, chunks, connection=connection)
        for page, asset in assets:
            register_asset(connection, source_file, page, asset)


@app.post("/api/admin/documents/upload", response_model=DocumentSummary)
async def upload_document(
    title: str = Form(min_length=3, max_length=300),
    document_type: str = Form(default="other"),
    document: UploadFile = File(),
) -> DocumentSummary:
    filename = Path(document.filename or "").name
    if not filename.lower().endswith(".pdf") or filename != document.filename or any(c in filename for c in ('/', '\\', ':')):
        raise HTTPException(status_code=400, detail="Choose a valid PDF file.")
    settings = get_settings()
    try:
        with TemporaryDirectory(prefix="neethi-import-") as temporary:
            destination = Path(temporary) / filename
            size = 0
            header = b""
            with destination.open("wb") as stream:
                while chunk := await document.read(1024 * 1024):
                    if not header: header = chunk[:5]
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        raise ValueError("PDF exceeds the 25 MB import limit.")
                    stream.write(chunk)
            if header != b"%PDF-":
                raise ValueError("The uploaded file is not a valid PDF.")
            chunks = extract_pdf(destination, title.strip())
            if not chunks:
                raise ValueError("No legal sections could be extracted. Prepare a searchable PDF or upload page images for OCR.")
            asset = store_file(settings.legal_ai_storage_root, destination)
            commit_import(settings, filename, chunks, document_type, "pdf_text", None, [], [(0, asset)])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The PDF could not be imported; no document was published.") from exc
    finally:
        await document.close()
    return next(item for item in list_documents(settings.legal_ai_database) if item.source_file == filename)


@app.post("/api/admin/documents/scan", response_model=DocumentSummary)
async def scan_document(
    title: str = Form(min_length=3, max_length=300),
    document_type: str = Form(default="book"),
    images: list[UploadFile] = File(),
) -> DocumentSummary:
    if not images or len(images) > 30:
        raise HTTPException(status_code=400, detail="Choose between 1 and 30 page images.")
    settings = get_settings()
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    source_file = f"scan-{uuid4().hex}.jpg"
    try:
        with TemporaryDirectory(prefix="neethi-scan-") as temporary:
            saved = []
            for index, image in enumerate(images, 1):
                suffix = allowed.get(image.content_type or "")
                if not suffix: raise ValueError("Only JPG, PNG, and WebP images are accepted.")
                path = Path(temporary) / f"page-{index:03d}{suffix}"
                size = 0
                with path.open("wb") as stream:
                    while chunk := await image.read(1024 * 1024):
                        size += len(chunk)
                        if size > 12 * 1024 * 1024: raise ValueError("Each image must be 12 MB or smaller.")
                        stream.write(chunk)
                saved.append(path)
            chunks, confidence, warnings = extract_images(saved, title.strip(), WORKSPACE_DIR / "data" / "ocr" / "tessdata")
            if not chunks: raise ValueError("No readable text was found in these images.")
            chunks = [replace(item, source_file=source_file, source_id=f"{source_file}-{i}") for i,item in enumerate(chunks)]
            assets = [(i, store_file(settings.legal_ai_storage_root, path)) for i,path in enumerate(saved, 1)]
            commit_import(settings, source_file, chunks, document_type, "ocr", confidence, warnings, assets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        for image in images: await image.close()
    return next(item for item in list_documents(settings.legal_ai_database) if item.source_file == source_file)


@app.post("/api/chat", response_model=ChatResponse)
def legal_chat(request: ChatRequest) -> ChatResponse:
    settings = get_settings()
    sources = search(settings.legal_ai_database, request.message, settings.legal_ai_top_k, request.as_of.isoformat() if request.as_of else None)
    try:
        return answer_question(request.message, sources, settings, request.as_of.isoformat() if request.as_of else None)
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
        request.as_of.isoformat() if request.as_of else None,
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
