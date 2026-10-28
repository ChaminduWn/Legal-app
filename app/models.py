from typing import Literal
from datetime import date

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    limit: int = Field(default=6, ge=1, le=20)
    as_of: date | None = None


class LegalSource(BaseModel):
    source_id: str
    law: str
    section: str
    heading: str | None = None
    text: str
    source_file: str
    page_start: int
    page_end: int
    edition_year: int | None = None
    instrument_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    score: float


class SearchResponse(BaseModel):
    query: str
    sources: list[LegalSource]
    retrieval_mode: str = "keyword"


class ChatRequest(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    as_of: date | None = None


class LegalReference(BaseModel):
    edition_year: int | None = None
    valid_from: str | None = None
    valid_to: str | None = None
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
    confidence_state: Literal["supported", "limited", "insufficient"] = "limited"
    confidence_reason: str = "The available sources require further review."


class PrepareRequest(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    as_of: date | None = None


class FollowUpQuestion(BaseModel):
    question_id: str
    question: str
    reason: str
    options: list[str]


class PrepareResponse(BaseModel):
    confidence_state: Literal["supported", "limited", "insufficient"]
    confidence_reason: str
    detected_domain: str
    detected_language: Literal["en", "si"]
    follow_up_questions: list[FollowUpQuestion]
    retrieval_mode: str


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


class DocumentVersionUpdate(BaseModel):
    instrument_id: str = Field(pattern="^[a-z0-9][a-z0-9-]{1,119}$")
    instrument_title: str = Field(min_length=3, max_length=300)
    edition_year: int = Field(ge=1500, le=9999)
    version_kind: Literal["original", "consolidated", "amendment", "reference"]
    valid_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    valid_to: str | None = None
    supersedes_source: str | None = None
    target_instrument_id: str | None = None
    relation_type: Literal["amends", "repeals", "references"] = "references"
    relationship_evidence: str | None = Field(default=None, max_length=4000)


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
    document_type: str = "other"
    extraction_method: str = "text"
    extraction_confidence: float | None = None
    extraction_warnings: list[str] = Field(default_factory=list)
    relationship_count: int = 0
    instrument_id: str | None = None
    instrument_title: str | None = None
    edition_year: int | None = None
    version_kind: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    supersedes_source: str | None = None
    target_instrument_id: str | None = None
    relation_type: str | None = None
    relationship_evidence: str | None = None
    topic_ids: list[str] = Field(default_factory=list)


class DocumentMetadataUpdate(BaseModel):
    version: DocumentVersionUpdate | None = None
    topic_ids: list[str] | None = None
    official_source_url: str | None = Field(default=None, max_length=2000)
    publication_date: str | None = Field(default=None, max_length=50)
    effective_date: str | None = Field(default=None, max_length=50)
    status: str = Field(pattern="^(unreviewed|in_review|verified|superseded)$")
    notes: str | None = Field(default=None, max_length=4000)
    document_type: str = Field(default="other", pattern="^(act|amendment|regulation|gazette|judgment|book|other)$")


class PublicationApproval(BaseModel):
    extraction_complete: bool = False
    authority_checked: bool = False


class LegalRelationship(BaseModel):
    id: int
    source_file: str
    relation_type: str
    target_law: str
    target_section: str | None = None
    evidence: str
    status: str


class AuditEvent(BaseModel):
    id: int
    action: str
    actor: str
    details: str | None
    created_at: str


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
