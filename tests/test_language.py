from app.services.language import detect_language, retrieval_query


def test_sinhala_computer_crime_terms_are_expanded_for_retrieval() -> None:
    query = "පරිගණක පද්ධතියකට අවසර නොමැතිව ඇතුළු වුණා"

    assert detect_language(query) == "si"
    expanded = retrieval_query(query)
    assert "computer" in expanded
    assert "system" in expanded
    assert "unauthorised" in expanded
    assert "access" in expanded


def test_english_query_is_not_changed() -> None:
    query = "My phone was stolen"

    assert detect_language(query) == "en"
    assert retrieval_query(query) == query
