from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.models import ChatRequest, ChatResponse, SearchRequest, SearchResponse
from app.rag.store import initialize, search
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
def legal_search(request: SearchRequest) -> SearchResponse:
    settings = get_settings()
    sources = search(settings.legal_ai_database, request.query, request.limit)
    return SearchResponse(query=request.query, sources=sources)


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
