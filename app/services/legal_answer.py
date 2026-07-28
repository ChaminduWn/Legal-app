import json

from app.config import Settings
from app.models import ChatResponse, LegalReference, LegalSource


SYSTEM_INSTRUCTION = """You are a Sri Lankan legal information assistant.
Use only the supplied legal source excerpts. Never invent a law, section, penalty,
procedure, source, or factual conclusion. Explain possible relevance rather than
deciding guilt, liability, or case outcome. If sources are insufficient, say so.
Every legal reference must use a source_id from the supplied excerpts.
Return concise, practical, neutral information in the requested JSON schema."""


def answer_question(
    question: str, sources: list[LegalSource], settings: Settings
) -> ChatResponse:
    if not sources:
        return insufficient_response()
    if not settings.gemini_api_key:
        return ChatResponse(
            summary=(
                "Relevant source material was retrieved, but AI explanation is disabled "
                "until GEMINI_API_KEY is configured."
            ),
            legal_references=[
                LegalReference(
                    source_id=source.source_id,
                    law=source.law,
                    section=source.section,
                    explanation="Retrieved as potentially relevant; requires review.",
                )
                for source in sources[:3]
            ],
            recommended_actions=[
                "Review the cited source text.",
                "Consult a qualified Sri Lankan lawyer for advice about your facts.",
            ],
            uncertainties=["The retrieved sections have not been interpreted by the AI."],
            grounded=True,
        )

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=20_000),
    )
    context = [
        {
            "source_id": source.source_id,
            "law": source.law,
            "section": source.section,
            "text": source.text,
        }
        for source in sources
    ]
    prompt = (
        f"{SYSTEM_INSTRUCTION}\n\nQuestion:\n{question}\n\n"
        f"Legal source excerpts:\n{json.dumps(context, ensure_ascii=False)}"
    )
    response = client.models.generate_content(
        model=settings.gemini_chat_model,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": ChatResponse.model_json_schema(),
        },
    )
    result = ChatResponse.model_validate_json(response.text)
    allowed = {source.source_id for source in sources}
    if any(ref.source_id not in allowed for ref in result.legal_references):
        return insufficient_response(
            "The generated response contained an unverifiable citation."
        )
    result.grounded = True
    return result


def insufficient_response(reason: str | None = None) -> ChatResponse:
    uncertainties = ["The available legal sources are insufficient for a reliable answer."]
    if reason:
        uncertainties.append(reason)
    return ChatResponse(
        summary="I could not find enough verified legal material to answer reliably.",
        legal_references=[],
        recommended_actions=[
            "Consult a qualified Sri Lankan lawyer or the appropriate public authority."
        ],
        uncertainties=uncertainties,
        grounded=False,
    )
