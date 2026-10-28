import json

from app.config import Settings
from app.models import ChatResponse, LegalReference, LegalSource
from app.services.language import detect_language, pick
from app.services.query_analysis import assess_query


SYSTEM_INSTRUCTION = """You are a Sri Lankan legal information assistant.
Use only the supplied legal source excerpts. Never invent a law, section, penalty,
procedure, source, or factual conclusion. Explain possible relevance rather than
deciding guilt, liability, or case outcome. If sources are insufficient, say so.
Every legal reference must use a source_id from the supplied excerpts.
Labels beginning with Page are PDF page locators, never statutory section numbers.
Return concise, practical, neutral information in the requested JSON schema."""


def answer_question(
    question: str, sources: list[LegalSource], settings: Settings, as_of: str | None = None
) -> ChatResponse:
    assessment = assess_query(question, sources)
    language = detect_language(question)
    if not sources:
        return insufficient_response(
            confidence_reason=assessment.confidence_reason,
            language=language,
        )
    if not settings.gemini_api_key:
        return ChatResponse(
            summary=pick(
                language,
                "Relevant source material was retrieved, but AI explanation is disabled until GEMINI_API_KEY is configured.",
                "අදාළ මූලාශ්‍ර හමු වූ නමුත් Gemini යතුර සකසන තුරු AI පැහැදිලි කිරීම අක්‍රිය කර ඇත.",
            ),
            legal_references=[
                LegalReference(
                    source_id=source.source_id,
                    law=source.law,
                    edition_year=source.edition_year, valid_from=source.valid_from, valid_to=source.valid_to,
                    section=source.section,
                    explanation=pick(language, "Retrieved as potentially relevant; requires review.", "අදාළ විය හැකි බැවින් හමු විය; සමාලෝචනය අවශ්‍යයි."),
                )
                for source in sources[:3]
            ],
            recommended_actions=[
                pick(language, "Review the cited source text.", "උපුටා දක්වා ඇති මූලාශ්‍ර පෙළ සමාලෝචනය කරන්න."),
                pick(language, "Consult a qualified Sri Lankan lawyer for advice about your facts.", "ඔබේ කරුණු පිළිබඳ උපදෙස් සඳහා සුදුසුකම් ලත් ශ්‍රී ලාංකික නීතිඥයෙකු අමතන්න."),
            ],
            uncertainties=[pick(language, "The retrieved sections have not been interpreted by the AI.", "හමු වූ වගන්ති AI මගින් අර්ථකථනය කර නොමැත.")],
            disclaimer=pick(language, "General legal information only, not legal advice.", "මෙය සාමාන්‍ය නීති තොරතුරු පමණි; නීති උපදෙස් නොවේ."),
            grounded=True,
            confidence_state=assessment.confidence_state,
            confidence_reason=assessment.confidence_reason,
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
            "edition_year": source.edition_year,
            "valid_from": source.valid_from,
            "valid_to": source.valid_to,
        }
        for source in sources
    ]
    prompt = (
        f"{SYSTEM_INSTRUCTION}\n"
        f"Answer in {'clear, natural Sinhala' if language == 'si' else 'English'}. "
        "For Sinhala answers, retain official Act names, section numbers, and "
        "source identifiers in English where useful. Never present a translation "
        "as an official quotation.\n\n"
        f"Requested legal date: {as_of or 'today'}. Use only these editions; do not infer later amendments.\n"
        f"Question:\n{question}\n\n"
        f"Legal source excerpts:\n{json.dumps(context, ensure_ascii=False)}"
    )
    try:
        response = client.models.generate_content(
            model=settings.gemini_chat_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": ChatResponse.model_json_schema(),
            },
        )
    except Exception:
        return source_only_response(sources, assessment, language)
    result = ChatResponse.model_validate_json(response.text)
    allowed = {source.source_id for source in sources}
    if any(ref.source_id not in allowed for ref in result.legal_references):
        return insufficient_response(
            "The generated response contained an unverifiable citation.",
            confidence_reason=assessment.confidence_reason,
            language=language,
        )
    source_map = {s.source_id: s for s in sources}
    for ref in result.legal_references:
        original = source_map[ref.source_id]
        ref.law, ref.section = original.law, original.section
        ref.edition_year, ref.valid_from, ref.valid_to = original.edition_year, original.valid_from, original.valid_to
    result.grounded = True
    result.confidence_state = assessment.confidence_state
    result.confidence_reason = assessment.confidence_reason
    return result


def source_only_response(sources, assessment, language: str) -> ChatResponse:
    """Keep the legal search useful when the free Gemini service is unavailable."""
    return ChatResponse(
        summary=pick(
            language,
            "Relevant legal sections were found. The AI explanation is temporarily unavailable, so review the cited source text directly.",
            "අදාළ නීති වගන්ති හමු විය. AI පැහැදිලි කිරීම තාවකාලිකව නොලැබෙන බැවින් පහත මූලාශ්‍ර පෙළ සෘජුව සමාලෝචනය කරන්න.",
        ),
        legal_references=[
            LegalReference(
                source_id=source.source_id,
                law=source.law,
                    edition_year=source.edition_year, valid_from=source.valid_from, valid_to=source.valid_to,
                section=source.section,
                explanation=pick(
                    language,
                    "Retrieved as potentially relevant to the facts provided.",
                    "ලබාදුන් කරුණුවලට අදාළ විය හැකි බැවින් හමු වූ වගන්තියකි.",
                ),
            )
            for source in sources[:3]
        ],
        recommended_actions=[
            pick(language, "Review the source text shown beside this answer.", "මෙම පිළිතුර අසල පෙන්වන මූලාශ්‍ර පෙළ සමාලෝචනය කරන්න."),
            pick(language, "Try the AI explanation again later or consult a qualified Sri Lankan lawyer.", "AI පැහැදිලි කිරීම පසුව නැවත උත්සාහ කරන්න, නැත්නම් සුදුසුකම් ලත් ශ්‍රී ලාංකික නීතිඥයෙකු අමතන්න."),
        ],
        uncertainties=[
            pick(language, "Gemini was temporarily unavailable; no AI interpretation was used.", "Gemini තාවකාලිකව නොලැබුණු බැවින් AI අර්ථකථනයක් භාවිත කර නැත.")
        ],
        disclaimer=pick(language, "General legal information only, not legal advice.", "මෙය සාමාන්‍ය නීති තොරතුරු පමණි; නීති උපදෙස් නොවේ."),
        grounded=True,
        confidence_state=assessment.confidence_state,
        confidence_reason=assessment.confidence_reason,
    )


def insufficient_response(
    reason: str | None = None,
    confidence_reason: str = "The available legal sources are insufficient.",
    language: str = "en",
) -> ChatResponse:
    uncertainties = [pick(language, "The available legal sources are insufficient for a reliable answer.", "විශ්වාසදායක පිළිතුරක් සඳහා දැනට ඇති නීති මූලාශ්‍ර ප්‍රමාණවත් නොවේ.")]
    if reason:
        uncertainties.append(reason)
    return ChatResponse(
        summary=pick(language, "I could not find enough verified legal material to answer reliably.", "විශ්වාසදායක ලෙස පිළිතුරු දීමට ප්‍රමාණවත් සත්‍යාපිත නීතිමය තොරතුරු හමු නොවීය."),
        legal_references=[],
        recommended_actions=[
            pick(language, "Consult a qualified Sri Lankan lawyer or the appropriate public authority.", "සුදුසුකම් ලත් ශ්‍රී ලාංකික නීතිඥයෙකු හෝ අදාළ රාජ්‍ය ආයතනයක් අමතන්න.")
        ],
        uncertainties=uncertainties,
        grounded=False,
        confidence_state="insufficient",
        confidence_reason=confidence_reason,
        disclaimer=pick(language, "General legal information only, not legal advice.", "මෙය සාමාන්‍ය නීති තොරතුරු පමණි; නීති උපදෙස් නොවේ."),
    )
