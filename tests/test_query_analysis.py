from app.models import LegalSource
from app.services.language import detect_language, retrieval_query
from app.services.query_analysis import assess_query


def source(law: str, section: str) -> LegalSource:
    return LegalSource(
        source_id=f"{law}-{section}",
        law=law,
        section=section,
        text="Relevant legal text",
        source_file="law.pdf",
        page_start=1,
        page_end=1,
        score=1,
    )


def test_property_question_requests_force_clarification() -> None:
    assessment = assess_query(
        "My phone was taken from my bag",
        [source("Penal Code", "366")],
    )
    assert assessment.confidence_state == "supported"
    assert assessment.detected_domain == "property_offence"
    assert assessment.follow_up_questions[0].question_id == "force_or_threat"


def test_mismatched_sources_are_insufficient() -> None:
    assessment = assess_query(
        "My employer did not pay my wages",
        [source("Motor Traffic Act", "152A")],
    )
    assert assessment.confidence_state == "insufficient"


def test_domestic_violence_questions_are_limited_to_two() -> None:
    assessment = assess_query(
        "My husband threatens me at home",
        [source("Prevention of Domestic Violence Act", "2")],
    )
    assert len(assessment.follow_up_questions) <= 2
    assert {item.question_id for item in assessment.follow_up_questions} == {
        "physical_injury",
        "immediate_danger",
    }


def test_sinhala_question_gets_sinhala_clarification() -> None:
    assessment = assess_query(
        "බස් රථයේදී මගේ දුරකථනය සොරකම් කළා.",
        [source("Penal Code", "366")],
    )
    assert detect_language("මගේ දුරකථනය") == "si"
    assert assessment.detected_domain == "property_offence"
    assert "තර්ජනයක්" in assessment.follow_up_questions[0].question


def test_sinhala_retrieval_query_adds_english_legal_vocabulary() -> None:
    expanded = retrieval_query("අවසරයකින් තොරව මගේ පරිගණකයට ප්‍රවේශ වුණා.")
    assert "unauthorised" in expanded
    assert "computer" in expanded
