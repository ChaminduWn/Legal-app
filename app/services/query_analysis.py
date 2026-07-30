import re
from dataclasses import dataclass

from app.models import FollowUpQuestion, LegalSource
from app.services.language import LanguageCode, detect_language, pick


SINHALA_DOMAIN_RULES = [
    ("domestic_violence", {"ගෘහස්ථ", "සැමියා", "බිරිඳ", "සහකරු", "ආරක්ෂණ නියෝග"}, {"Prevention of Domestic Violence"}),
    ("bail", {"ඇප", "රිමාන්ඩ්", "අත්අඩංගුව"}, {"Bail Act"}),
    ("evidence", {"සාක්ෂි", "පාපොච්චාරණ", "සාක්ෂිකරු"}, {"Evidence Ordinance"}),
    ("cybercrime", {"පරිගණක", "හැක්", "අනවසර ප්‍රවේශ"}, {"Computer Crime Act"}),
    ("data_protection", {"පුද්ගලික දත්ත", "පෞද්ගලිකත්වය"}, {"Personal Data Protection Act"}),
    ("employment", {"සේවායෝජක", "වැටුප", "අතිකාල", "රැකියාවෙන්", "සේවය"}, {"Shop and Office", "Wages Boards", "Industrial Disputes"}),
    ("consumer", {"පාරිභෝගික", "දෝෂ සහිත", "මුදල් ආපසු", "වෙළෙන්දා"}, {"Consumer Affairs Authority"}),
    ("traffic", {"වාහනය", "රියදුරු", "රිය පැදවීම", "මාර්ග අනතුර", "බලපත්‍ර"}, {"Motor Traffic"}),
    ("property_offence", {"සොරකම්", "සොරකම", "ගත්තා", "දුරකථනය", "කොල්ලකෑම"}, {"Penal Code"}),
    ("physical_harm", {"පහර", "තුවාල", "තල්ලු", "වේදනාව"}, {"Penal Code"}),
]


@dataclass(frozen=True)
class QueryAssessment:
    confidence_state: str
    confidence_reason: str
    detected_domain: str
    follow_up_questions: list[FollowUpQuestion]


DOMAIN_RULES = [
    (
        "domestic_violence",
        {"domestic", "husband", "wife", "partner", "home", "protection order"},
        {"Prevention of Domestic Violence"},
    ),
    (
        "bail",
        {"bail", "remand", "custody", "released after arrest"},
        {"Bail Act"},
    ),
    (
        "evidence",
        {"evidence", "confession", "witness", "prove in court"},
        {"Evidence Ordinance"},
    ),
    (
        "cybercrime",
        {"computer", "hacked", "unauthorised access", "unauthorized access"},
        {"Computer Crime Act"},
    ),
    (
        "data_protection",
        {"personal data", "privacy", "data controller", "processing my data"},
        {"Personal Data Protection Act"},
    ),
    (
        "employment",
        {"employer", "salary", "wages", "work hours", "overtime", "fired"},
        {"Shop and Office", "Wages Boards", "Industrial Disputes"},
    ),
    (
        "consumer",
        {"consumer", "defective", "refund", "seller", "trader", "product"},
        {"Consumer Affairs Authority"},
    ),
    (
        "traffic",
        {"vehicle", "driver", "driving", "road accident", "license", "licence"},
        {"Motor Traffic"},
    ),
    (
        "property_offence",
        {"stolen", "stole", "taken", "took", "theft", "robbery", "phone"},
        {"Penal Code"},
    ),
    (
        "physical_harm",
        {"hit", "attacked", "assault", "hurt", "injured", "beaten", "pushed"},
        {"Penal Code"},
    ),
]


def _contains(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def assess_query(question: str, sources: list[LegalSource]) -> QueryAssessment:
    normalized = " ".join(question.lower().split())
    language = detect_language(question)
    domain = "general"
    expected_laws: set[str] = set()
    for candidate, phrases, laws in DOMAIN_RULES:
        if _contains(normalized, phrases):
            domain = candidate
            expected_laws = laws
            break
    if domain == "general":
        for candidate, phrases, laws in SINHALA_DOMAIN_RULES:
            if _contains(normalized, phrases):
                domain, expected_laws = candidate, laws
                break

    if not sources:
        confidence = "insufficient"
        reason = "No sufficiently relevant section was found in the current collection."
    elif not expected_laws:
        confidence = "limited"
        reason = (
            "Relevant text was found, but the legal area could not be identified "
            "reliably from the facts provided."
        )
    else:
        expected_ranks = [
            index
            for index, source in enumerate(sources)
            if any(expected in source.law for expected in expected_laws)
        ]
        if expected_ranks and min(expected_ranks) < 3:
            confidence = "supported"
            reason = (
                "The leading retrieved sections come from legislation matching "
                "the described legal area."
            )
        elif expected_ranks:
            confidence = "limited"
            reason = (
                "Potentially relevant legislation was found, but it was not among "
                "the strongest retrieval matches."
            )
        else:
            confidence = "insufficient"
            reason = (
                "The retrieved sources do not match the legal area suggested by "
                "the question."
            )

    if language == "si":
        reason = {
            "supported": "ප්‍රධාන වශයෙන් හමු වූ වගන්ති විස්තර කළ නීති ක්ෂේත්‍රයට ගැළපෙන පනත්වලින් වේ.",
            "limited": "අදාළ නීති පෙළ හමු වූ නමුත් ප්‍රතිඵල තවදුරටත් සමාලෝචනය කළ යුතුය.",
            "insufficient": "දැනට ඇති නීති එකතුවෙන් ප්‍රමාණවත් ලෙස අදාළ වගන්තියක් හමු නොවීය.",
        }[confidence]

    follow_ups = _follow_up_questions(domain, normalized, language)
    return QueryAssessment(confidence, reason, domain, follow_ups[:2])


def _follow_up_questions(
    domain: str, text: str, language: LanguageCode = "en"
) -> list[FollowUpQuestion]:
    questions = []
    if domain == "property_offence" and not _contains(
        text, {"force", "threat", "weapon", "violence", "without force"}
    ):
        questions.append(
            FollowUpQuestion(
                question_id="force_or_threat",
                question=pick(language, "Was force, violence, or a threat used when the property was taken?", "දේපළ ගන්නා විට බලය, ප්‍රචණ්ඩත්වය හෝ තර්ජනයක් භාවිත කළාද?"),
                reason=pick(language, "This may distinguish theft from robbery or another offence.", "මෙය සොරකම සහ කොල්ලකෑම වැනි වැරදි වෙන්කර හඳුනා ගැනීමට වැදගත් වේ."),
                options=[pick(language, "No", "නැහැ"), pick(language, "Yes", "ඔව්"), pick(language, "I am not sure", "මට විශ්වාස නැහැ")],
            )
        )
    if domain in {"physical_harm", "domestic_violence"} and not _contains(
        text, {"injured", "injury", "pain", "wound", "medical"}
    ):
        questions.append(
            FollowUpQuestion(
                question_id="physical_injury",
                question=pick(language, "Did anyone suffer physical injury, bodily pain, or require medical care?", "කිසිවෙකුට ශාරීරික තුවාලයක් හෝ වේදනාවක් ඇතිවුණාද, නැත්නම් වෛද්‍ය ප්‍රතිකාර අවශ්‍ය වුණාද?"),
                reason=pick(language, "The nature of any injury can change which Penal Code sections are relevant.", "තුවාලයේ ස්වභාවය අනුව අදාළ දණ්ඩ නීති සංග්‍රහයේ වගන්ති වෙනස් විය හැක."),
                options=[pick(language, "No physical injury", "ශාරීරික තුවාලයක් නැහැ"), pick(language, "Yes", "ඔව්"), pick(language, "I am not sure", "මට විශ්වාස නැහැ")],
            )
        )
    if domain == "domestic_violence" and "immediate danger" not in text:
        questions.append(
            FollowUpQuestion(
                question_id="immediate_danger",
                question=pick(language, "Are you currently in immediate danger?", "ඔබ මේ මොහොතේ වහාම අනතුරක සිටිනවාද?"),
                reason=pick(language, "Urgent safety needs should be handled before general legal information.", "සාමාන්‍ය නීති තොරතුරුවලට පෙර හදිසි ආරක්ෂාව සලකා බැලිය යුතුය."),
                options=[pick(language, "No", "නැහැ"), pick(language, "Yes", "ඔව්"), pick(language, "I am not sure", "මට විශ්වාස නැහැ")],
            )
        )
    if domain == "employment" and not _contains(
        text, {"shop", "office", "factory", "estate", "government"}
    ):
        questions.append(
            FollowUpQuestion(
                question_id="workplace_type",
                question=pick(language, "Which type of workplace best describes your employment?", "ඔබ සේවය කරන ස්ථානයේ වර්ගය කුමක්ද?"),
                reason=pick(language, "Different employment laws cover different categories of work.", "විවිධ රැකියා වර්ග සඳහා වෙනස් කම්කරු නීති අදාළ වේ."),
                options=[pick(language, "Shop or office", "වෙළඳසැලක් හෝ කාර්යාලයක්"), pick(language, "Factory or industrial workplace", "කර්මාන්තශාලාවක් හෝ කාර්මික ස්ථානයක්"), pick(language, "Other / unsure", "වෙනත් / විශ්වාස නැහැ")],
            )
        )
    if domain == "traffic" and not _contains(
        text, {"injured", "injury", "damage", "police", "left the scene"}
    ):
        questions.append(
            FollowUpQuestion(
                question_id="traffic_consequences",
                question=pick(language, "Did the incident cause injury, property damage, or involve a driver leaving the scene?", "සිද්ධියෙන් තුවාලයක්, දේපළ හානියක් සිදුවුණාද, නැත්නම් රියදුරෙකු එම ස්ථානයෙන් පිටව ගියාද?"),
                reason=pick(language, "These facts affect the traffic provisions and practical next steps.", "මෙම කරුණු අදාළ රථවාහන නීති සහ ඉදිරි පියවර කෙරෙහි බලපායි."),
                options=[pick(language, "No", "නැහැ"), pick(language, "Yes", "ඔව්"), pick(language, "I am not sure", "මට විශ්වාස නැහැ")],
            )
        )
    return questions


def append_clarifications(
    original_question: str, answers: list[tuple[str, str]]
) -> str:
    safe_answers = [
        (re.sub(r"\s+", " ", question).strip(), re.sub(r"\s+", " ", answer).strip())
        for question, answer in answers
        if answer.strip()
    ]
    if not safe_answers:
        return original_question
    details = "\n".join(f"- {question}: {answer}" for question, answer in safe_answers)
    return f"{original_question}\n\nClarifying details supplied by the user:\n{details}"
