from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    limit: int = Field(default=6, ge=1, le=20)


class LegalSource(BaseModel):
    source_id: str
    law: str
    section: str
    heading: str | None = None
    text: str
    source_file: str
    page_start: int
    page_end: int
    score: float


class SearchResponse(BaseModel):
    query: str
    sources: list[LegalSource]
    retrieval_mode: str = "keyword"


class ChatRequest(BaseModel):
    message: str = Field(min_length=3, max_length=4000)


class LegalReference(BaseModel):
    source_id: str
    law: str
    section: str
    explanation: str


class ChatResponse(BaseModel):
    summary: str
    legal_references: list[LegalReference]
    recommended_actions: list[str]
    uncertainties: list[str]
    disclaimer: str = "General legal information only, not legal advice."
    grounded: bool


class EvaluationCaseResult(BaseModel):
    case_id: str
    question: str
    expected_sections: list[str]
    retrieved_sections: list[str]
    passed: bool
    reciprocal_rank: float


class EvaluationSummary(BaseModel):
    total: int
    passed: int
    recall_at_k: float
    mean_reciprocal_rank: float
    retrieval_mode: str
    cases: list[EvaluationCaseResult]


class DocumentSummary(BaseModel):
    source_file: str
    law: str
    section_count: int
    verified_sections: int
    status: str
    official_source_url: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    notes: str | None = None


class DocumentMetadataUpdate(BaseModel):
    official_source_url: str | None = Field(default=None, max_length=2000)
    publication_date: str | None = Field(default=None, max_length=50)
    effective_date: str | None = Field(default=None, max_length=50)
    status: str = Field(pattern="^(unreviewed|in_review|verified|superseded)$")
    notes: str | None = Field(default=None, max_length=4000)


class SectionReviewUpdate(BaseModel):
    heading: str | None = Field(default=None, max_length=500)
    text: str = Field(min_length=20, max_length=50000)
    status: str = Field(pattern="^(unreviewed|in_review|verified|needs_correction)$")
    notes: str | None = Field(default=None, max_length=2000)


class AdminSection(BaseModel):
    source_id: str
    section: str
    heading: str | None
    text: str
    page_start: int
    page_end: int
    status: str
    notes: str | None = None
    semantic_indexed: bool
