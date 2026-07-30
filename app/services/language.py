import re
from typing import Literal


LanguageCode = Literal["en", "si"]
SINHALA_CHARACTERS = re.compile(r"[\u0D80-\u0DFF]")
SINHALA_LEGAL_GLOSSARY = {
    "සොරකම්": "theft stolen property",
    "සොරකම": "theft stolen property",
    "දුරකථනය": "phone mobile",
    "බෑගයෙන්": "from bag",
    "පාපොච්චාරණ": "confession",
    "පොලිස් නිලධාරි": "police officer",
    "සාක්ෂි": "evidence",
    "චූදිත": "accused person",
    "ගෘහස්ථ ප්‍රචණ්ඩත්ව": "domestic violence prevention",
    "ආරක්ෂණ නියෝග": "application for a protection order",
    "අවසරයකින් තොරව": "unauthorised",
    "අවසර නොමැතිව": "unauthorised",
    "පරිගණක": "computer",
    "පද්ධති": "system",
    "පිවිස": "access",
    "ඇතුළු": "access",
    "වෙළඳසැල": "shop",
    "කාර්යාල": "office",
    "සේවක": "employee",
    "වැඩ කරන පැය": "working hours",
    "මාර්ග අනතුර": "road accident",
    "රියදුරු": "driver",
    "පහර": "hit assault",
    "තුවාල": "injury bodily pain",
    "ඇප": "bail",
    "රිමාන්ඩ්": "remand custody",
    "පුද්ගලික දත්ත": "personal data",
    "පාරිභෝගික": "consumer",
    "දෝෂ සහිත": "defective goods",
}


def detect_language(text: str) -> LanguageCode:
    """Detect whether the user's message contains Sinhala script."""
    return "si" if SINHALA_CHARACTERS.search(text) else "en"


def pick(language: LanguageCode, english: str, sinhala: str) -> str:
    return sinhala if language == "si" else english


def retrieval_query(text: str) -> str:
    """Append legal English equivalents while preserving the user's Sinhala."""
    if detect_language(text) != "si":
        return text
    equivalents = [
        english
        for sinhala, english in SINHALA_LEGAL_GLOSSARY.items()
        if sinhala in text
    ]
    return f"{text} {' '.join(equivalents)}".strip()
