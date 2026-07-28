from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.evaluation import load_cases, run_evaluation
from app.models import (
    ChatRequest,
    ChatResponse,
    EvaluationSummary,
    SearchRequest,
    SearchResponse,
)
from app.rag.store import initialize, retrieval_mode, search
from app.services.legal_answer import answer_question


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize(get_settings().legal_ai_database)
    yield


app = FastAPI(
    title="Sri Lankan Legal AI API",
    version="0.1.0",
    description="Source-grounded legal information. Not legal advice.",
    lifespan=lifespan,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/evaluation", include_in_schema=False)
def evaluation_frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "evaluation.html")


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
