import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_SITE_CONTENT = {
    "en": {
        "appName": "Neethi",
        "subtitle": "Sri Lankan legal guide",
        "eyebrow": "Source-grounded legal information",
        "headline": "Understand your situation.<br><em>Know your next step.</em>",
        "intro": "Describe what happened in your own words. Neethi searches verified Sri Lankan legislation and explains potentially relevant sections clearly.",
        "questionLabel": "What happened?",
        "placeholder": "For example: My phone was taken from my bag on the bus...",
        "privacyNotice": "Avoid names, ID numbers, or private details",
        "submitButton": "Find relevant law →",
        "examplesTitle": "Try an example",
        "examplesSubtitle": "Common situations",
        "examples": [
            {
                "title": "Physical harm",
                "desc": "Someone attacked or injured me",
                "query": "Someone hit me with a stick and I was injured. What can I do?",
            },
            {
                "title": "Stolen property",
                "desc": "My phone or belongings were taken",
                "query": "My phone was stolen while I was travelling on a bus.",
            },
            {
                "title": "Road incident",
                "desc": "An accident or driving offence",
                "query": "I was involved in a road accident and the other driver left the scene.",
            },
        ],
        "aboutTitle": "About Neethi",
        "aboutText": "Neethi retrieves sections from supplied Sri Lankan legal documents before generating an explanation. It does not decide whether an offence occurred, predict a court outcome, or replace a qualified lawyer.",
        "emergencyTitle": "For emergencies",
        "emergencyText": "Contact the appropriate local emergency service or public authority directly.",
        "footerDisclaimer": "General legal information, not legal advice.",
        "footerSources": "Based on supplied Sri Lankan legislation · English and Sinhala",
    },
    "si": {
        "appName": "නීති",
        "subtitle": "ශ්‍රී ලංකා නීති මාර්ගෝපදේශය",
        "eyebrow": "මූලාශ්‍ර පදනම් කරගත් නීති තොරතුරු",
        "headline": "ඔබේ තත්ත්වය තේරුම් ගන්න.<br><em>ඊළඟ පියවර දැනගන්න.</em>",
        "intro": "සිදු වූ දේ ඔබේම වචනවලින් කියන්න. නීති සත්‍යාපිත ශ්‍රී ලංකා නීති සොයා අදාළ විය හැකි වගන්ති පැහැදිලි කරයි.",
        "questionLabel": "සිදු වූයේ කුමක්ද?",
        "placeholder": "උදාහරණයක්: බස් රථයේදී මගේ බෑගයෙන් දුරකථනය ගත්තා...",
        "privacyNotice": "නම්, හැඳුනුම්පත් අංක හෝ පුද්ගලික තොරතුරු ඇතුළත් නොකරන්න",
        "submitButton": "අදාළ නීතිය සොයන්න →",
        "examplesTitle": "උදාහරණයක් බලන්න",
        "examplesSubtitle": "පොදු තත්ත්වයන්",
        "examples": [
            {
                "title": "ශාරීරික හානිය",
                "desc": "යමෙකු මට පහර දුන්නා හෝ තුවාල කළා",
                "query": "කෙනෙක් මට පොල්ලකින් පහර දී තුවාල කළා. මට කුමක් කළ හැකිද?",
            },
            {
                "title": "දේපළ සොරකම",
                "desc": "මගේ දුරකථනය හෝ බඩු බාහිරාදිය ගත්තා",
                "query": "බස් රථයක ගමන් කරමින් සිටියදී මගේ දුරකථනය සොරකම් කරන ලදී.",
            },
            {
                "title": "මාර්ග අනතුර",
                "desc": "අනතුරක් හෝ රිය පැදවීමේ වරදක්",
                "query": "මම මාර්ග අනතුරකට ලක් වූ අතර අනෙක් රියදුරු එම ස්ථානයෙන් පලා ගියේය.",
            },
        ],
        "aboutTitle": "නීති ගැන",
        "aboutText": "නීති මගින් පැහැදිලි කිරීමක් ඉදිරිපත් කිරීමට පෙර ලබා දී ඇති ශ්‍රී ලංකා නීති ලේඛනවලින් අදාළ වගන්ති ලබා ගනී. එය වරදක් සිදුවී ඇත්දැයි තීරණය නොකරයි, අධිකරණ තීන්දුවක් ප්‍රකාශ නොකරයි, හෝ නීතිඥයෙකුට ආදේශකයක් නොවේ.",
        "emergencyTitle": "හදිසි අවස්ථා සඳහා",
        "emergencyText": "කරුණාකර ප්‍රාදේශීය හදිසි සේවා හෝ පොලිස් ස්ථානය සෘජුවම අමතන්න.",
        "footerDisclaimer": "සාමාන්‍ය නීති තොරතුරු පමණි, නීති උපදෙසක් නොවේ.",
        "footerSources": "සපයන ලද ශ්‍රී ලංකා නීති මත පදනම් වේ · ඉංග්‍රීසි සහ සිංහල",
    },
}


def initialize_site_content(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS site_content (
            id TEXT PRIMARY KEY,
            content_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    row = connection.execute("SELECT 1 FROM site_content WHERE id = 'active'").fetchone()
    if not row:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "INSERT INTO site_content (id, content_json, updated_at) VALUES ('active', ?, ?)",
            (json.dumps(DEFAULT_SITE_CONTENT, ensure_ascii=False), now),
        )


def get_site_content(database: Path) -> dict:
    from app.rag.store import connect

    try:
        with connect(database) as connection:
            initialize_site_content(connection)
            row = connection.execute("SELECT content_json FROM site_content WHERE id = 'active'").fetchone()
            if row:
                stored = json.loads(row[0])
                # Ensure structure integrity with defaults
                merged = {
                    "en": {**DEFAULT_SITE_CONTENT["en"], **stored.get("en", {})},
                    "si": {**DEFAULT_SITE_CONTENT["si"], **stored.get("si", {})},
                }
                return merged
    except Exception:
        pass
    return DEFAULT_SITE_CONTENT


def save_site_content(database: Path, content: dict) -> dict:
    from app.rag.store import connect

    now = datetime.now(UTC).isoformat()
    json_data = json.dumps(content, ensure_ascii=False)
    with connect(database) as connection:
        initialize_site_content(connection)
        connection.execute(
            """
            INSERT INTO site_content (id, content_json, updated_at)
            VALUES ('active', ?, ?)
            ON CONFLICT(id) DO UPDATE SET content_json = excluded.content_json, updated_at = excluded.updated_at
            """,
            (json_data, now),
        )
    return content


def reset_site_content(database: Path) -> dict:
    return save_site_content(database, DEFAULT_SITE_CONTENT)
