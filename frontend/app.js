const form = document.querySelector("#question-form");
const input = document.querySelector("#question-input");
const welcomeView = document.querySelector("#welcome-view");
const resultsView = document.querySelector("#results-view");
const loadingState = document.querySelector("#loading-state");
const followupState = document.querySelector("#followup-state");
const followupList = document.querySelector("#followup-list");
const answerContent = document.querySelector("#answer-content");
const errorState = document.querySelector("#error-state");
const submittedQuestion = document.querySelector("#submitted-question");
const answerSummary = document.querySelector("#answer-summary");
const referenceList = document.querySelector("#reference-list");
const actionList = document.querySelector("#action-list");
const uncertaintyBox = document.querySelector("#uncertainty-box");
const uncertaintyList = document.querySelector("#uncertainty-list");
const sourceList = document.querySelector("#source-list");
const sourceCount = document.querySelector("#source-count");
const answerDisclaimer = document.querySelector("#answer-disclaimer");
const errorMessage = document.querySelector("#error-message");
const aboutDialog = document.querySelector("#about-dialog");

let lastQuestion = "";
let pendingFollowups = [];
let currentLanguage = localStorage.getItem("neethi-language") === "si" ? "si" : "en";

const languageCopy = {
  en: {
    subtitle: "Sri Lankan legal guide",
    headline: "Understand your situation.<br><em>Know your next step.</em>",
    intro: "Describe what happened in your own words. Neethi searches verified Sri Lankan legislation and explains potentially relevant sections clearly.",
    label: "What happened?",
    placeholder: "For example: My phone was taken from my bag on the bus...",
    privacy: "● Avoid names, ID numbers, or private details",
    submit: "Find relevant law →",
    examplesTitle: "Try an example",
    examples: [
      ["Physical harm", "Someone attacked or injured me", "Someone hit me with a stick and I was injured. What can I do?"],
      ["Stolen property", "My phone or belongings were taken", "My phone was stolen while I was travelling on a bus."],
      ["Road incident", "An accident or driving offence", "I was involved in a road accident and the other driver left the scene."],
    ],
    newQuestion: "← New question",
    situation: "Your situation",
    brief: "Legal information brief",
    moreContext: "? A little more context",
    mayChange: "These details may change which law applies.",
    closest: "Choose the closest answer. You can select “I am not sure” where necessary.",
    continue: "Continue legal search →",
    meaning: "What the available law may mean",
    relevant: "Potentially relevant law",
    nextSteps: "Practical next steps",
    important: "Important context",
    evidence: "Evidence used",
    askAnother: "Ask another question",
    failed: "We couldn’t complete the search",
    retry: "Try again",
    loading: "Reviewing the legal sources",
    loadingText: "Searching the supplied Sri Lankan legislation for relevant sections…",
    footer: "Based on supplied Sri Lankan legislation · English and Sinhala",
    sources: "sources",
    source: "source",
    support: "source support",
    error: "Please try again in a moment.",
    serviceError: "The legal information service did not return a complete response.",
  },
  si: {
    subtitle: "ශ්‍රී ලංකා නීති මාර්ගෝපදේශය",
    headline: "ඔබේ තත්ත්වය තේරුම් ගන්න.<br><em>ඊළඟ පියවර දැනගන්න.</em>",
    intro: "සිදු වූ දේ ඔබේම වචනවලින් කියන්න. නීති සත්‍යාපිත ශ්‍රී ලංකා නීති සොයා අදාළ විය හැකි වගන්ති පැහැදිලි කරයි.",
    label: "සිදු වූයේ කුමක්ද?",
    placeholder: "උදාහරණයක්: බස් රථයේදී මගේ බෑගයෙන් දුරකථනය ගත්තා...",
    privacy: "● නම්, හැඳුනුම්පත් අංක හෝ පෞද්ගලික තොරතුරු ඇතුළත් නොකරන්න",
    submit: "අදාළ නීතිය සොයන්න →",
    examplesTitle: "උදාහරණයක් උත්සාහ කරන්න",
    examples: [
      ["ශාරීරික හානිය", "කෙනෙක් මට පහර දී තුවාල කළා", "කෙනෙක් මට පොල්ලකින් පහර දී තුවාල කළා. මට කළ හැක්කේ කුමක්ද?"],
      ["සොරකම් කළ දේපළ", "මගේ දුරකථනය හෝ බඩු ගත්තා", "බස් රථයේ ගමන් කරන අතරතුර මගේ දුරකථනය සොරකම් කළා."],
      ["මාර්ග අනතුර", "අනතුරක් හෝ රිය පැදවීමේ වරදක්", "මම මාර්ග අනතුරකට ලක්වුණා සහ අනෙක් රියදුරු එම ස්ථානයෙන් පිටව ගියා."],
    ],
    newQuestion: "← නව ප්‍රශ්නයක්",
    situation: "ඔබේ තත්ත්වය",
    brief: "නීති තොරතුරු සාරාංශය",
    moreContext: "? තවත් තොරතුරු ටිකක්",
    mayChange: "මෙම කරුණු අනුව අදාළ නීතිය වෙනස් විය හැක.",
    closest: "ආසන්නතම පිළිතුර තෝරන්න. අවශ්‍ය නම් “මට විශ්වාස නැහැ” තෝරන්න.",
    continue: "නීති සෙවුම දිගටම කරගෙන යන්න →",
    meaning: "දැනට ඇති නීතියෙන් අදහස් විය හැකි දේ",
    relevant: "අදාළ විය හැකි නීතිය",
    nextSteps: "ප්‍රායෝගික ඉදිරි පියවර",
    important: "වැදගත් පසුබිම",
    evidence: "භාවිත කළ මූලාශ්‍ර",
    askAnother: "තවත් ප්‍රශ්නයක් අසන්න",
    failed: "සෙවුම සම්පූර්ණ කිරීමට නොහැකි විය",
    retry: "නැවත උත්සාහ කරන්න",
    loading: "නීති මූලාශ්‍ර සමාලෝචනය කරමින්",
    loadingText: "සපයා ඇති ශ්‍රී ලංකා නීතිවලින් අදාළ වගන්ති සොයමින්…",
    footer: "සපයා ඇති ශ්‍රී ලංකා නීති මත පදනම් වේ · සිංහල සහ English",
    sources: "මූලාශ්‍ර",
    source: "මූලාශ්‍රය",
    support: "මූලාශ්‍ර සහාය",
    error: "මොහොතකින් නැවත උත්සාහ කරන්න.",
    serviceError: "නීති තොරතුරු සේවාවෙන් සම්පූර්ණ පිළිතුරක් නොලැබුණි.",
  },
};

function applyLanguage(language) {
  currentLanguage = language;
  localStorage.setItem("neethi-language", language);
  document.documentElement.lang = language;
  const t = languageCopy[language];
  document.querySelector(".brand small").textContent = t.subtitle;
  document.querySelector(".hero h1").innerHTML = t.headline;
  document.querySelector(".hero-copy").textContent = t.intro;
  document.querySelector('label[for="question-input"]').textContent = t.label;
  input.placeholder = t.placeholder;
  document.querySelector(".privacy-note").textContent = t.privacy;
  document.querySelector("#question-form .primary-button").textContent = t.submit;
  document.querySelector("#suggestions-title").textContent = t.examplesTitle;
  document.querySelectorAll(".suggestion-card").forEach((card, index) => {
    card.querySelector("strong").textContent = t.examples[index][0];
    card.querySelector("small").textContent = t.examples[index][1];
    card.dataset.question = t.examples[index][2];
  });
  document.querySelector("#back-button").textContent = t.newQuestion;
  document.querySelector(".case-label").textContent = t.brief;
  document.querySelector(".question-summary > span").textContent = t.situation;
  document.querySelector(".followup-heading .answer-kicker").textContent = t.moreContext;
  document.querySelector(".followup-heading h2").textContent = t.mayChange;
  document.querySelector(".followup-heading p").textContent = t.closest;
  document.querySelector(".followup-footer button").textContent = t.continue;
  document.querySelector(".loading-state h2").textContent = t.loading;
  document.querySelector(".loading-state p").textContent = t.loadingText;
  document.querySelector(".answer-main > h2").textContent = t.meaning;
  document.querySelectorAll(".answer-section-title h3")[0].textContent = t.relevant;
  document.querySelectorAll(".answer-section-title h3")[1].textContent = t.nextSteps;
  document.querySelector(".uncertainty-box > strong").textContent = t.important;
  document.querySelector(".source-panel-heading > span").textContent = t.evidence;
  document.querySelector("#ask-another-button").textContent = t.askAnother;
  document.querySelector(".error-state h2").textContent = t.failed;
  document.querySelector("#retry-button").textContent = t.retry;
  document.querySelector(".site-footer p:last-child").textContent = t.footer;
  document.querySelectorAll("[data-language]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.language === language));
  });
}

function setView(name) {
  welcomeView.hidden = name !== "welcome";
  resultsView.hidden = name === "welcome";
  loadingState.hidden = name !== "loading";
  followupState.hidden = name !== "followup";
  answerContent.hidden = name !== "answer";
  errorState.hidden = name !== "error";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function clearElement(element) {
  element.replaceChildren();
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function requestLegalBrief(question) {
  lastQuestion = question.trim();
  if (!lastQuestion) return;

  submittedQuestion.textContent = lastQuestion;
  setView("loading");

  try {
    const preparationResponse = await fetch("/api/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: lastQuestion }),
    });
    if (!preparationResponse.ok) {
      throw new Error("The question could not be prepared for legal search.");
    }
    const preparation = await preparationResponse.json();
    if ((preparation.follow_up_questions || []).length) {
      pendingFollowups = preparation.follow_up_questions;
      renderFollowups(pendingFollowups);
      setView("followup");
      return;
    }
    await submitLegalBrief(lastQuestion);
  } catch (error) {
    errorMessage.textContent =
      error instanceof Error ? error.message : languageCopy[currentLanguage].error;
    setView("error");
  }
}

async function submitLegalBrief(questionForSearch) {
  setView("loading");
  try {
    const [chatResponse, searchResponse] = await Promise.all([
      fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: questionForSearch }),
      }),
      fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: questionForSearch, limit: 6 }),
      }),
    ]);

    if (!chatResponse.ok || !searchResponse.ok) {
      let detail = "";
      if (!chatResponse.ok) {
        const errorPayload = await chatResponse.json().catch(() => ({}));
        detail = typeof errorPayload.detail === "string" ? errorPayload.detail : "";
      }
      throw new Error(
        detail || languageCopy[currentLanguage].serviceError,
      );
    }

    const answer = await chatResponse.json();
    const search = await searchResponse.json();
    renderAnswer(answer, search.sources || []);
    setView("answer");
  } catch (error) {
    errorMessage.textContent =
      error instanceof Error ? error.message : languageCopy[currentLanguage].error;
    setView("error");
  }
}

function renderFollowups(questions) {
  clearElement(followupList);
  questions.forEach((item, index) => {
    const fieldset = createElement("fieldset", "followup-question");
    const legend = createElement("legend", "", item.question);
    const reason = createElement("p", "", item.reason);
    const options = createElement("div", "followup-options");
    item.options.forEach((option, optionIndex) => {
      const label = createElement("label", "followup-option");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = `followup-${index}`;
      radio.value = option;
      radio.required = true;
      if (optionIndex === 0) radio.checked = true;
      label.append(radio, createElement("span", "", option));
      options.append(label);
    });
    fieldset.append(legend, reason, options);
    followupList.append(fieldset);
  });
}

function renderAnswer(answer, sources) {
  answerSummary.textContent = answer.summary;
  answerDisclaimer.textContent = answer.disclaimer;
  const confidenceName = answer.confidence_state || "limited";
  document.querySelector("#confidence-label").textContent =
    currentLanguage === "si"
      ? `${confidenceName} ${languageCopy.si.support}`
      : `${confidenceName.charAt(0).toUpperCase()}${confidenceName.slice(1)} source support`;
  document.querySelector("#confidence-reason").textContent =
    answer.confidence_reason || "The available sources require further review.";

  clearElement(referenceList);
  const sourceById = new Map(sources.map((source) => [source.source_id, source]));
  for (const reference of answer.legal_references || []) {
    const card = createElement("article", "reference-card");
    const header = document.createElement("header");
    const source = sourceById.get(reference.source_id);
    header.append(
      createElement("strong", "", reference.law),
      createElement("span", "section-pill", `Section ${reference.section}`),
    );
    card.append(header, createElement("p", "", reference.explanation));
    if (source?.heading && source.heading.length < 120) {
      card.setAttribute("aria-label", `${reference.law}, section ${reference.section}`);
    }
    referenceList.append(card);
  }

  if (!(answer.legal_references || []).length) {
    referenceList.append(
      createElement(
        "p",
        "answer-summary",
        "No sufficiently relevant section was identified in the current collection.",
      ),
    );
  }

  clearElement(actionList);
  for (const action of answer.recommended_actions || []) {
    actionList.append(createElement("li", "", action));
  }

  const uncertainties = answer.uncertainties || [];
  uncertaintyBox.hidden = uncertainties.length === 0;
  clearElement(uncertaintyList);
  for (const uncertainty of uncertainties) {
    uncertaintyList.append(createElement("li", "", uncertainty));
  }

  clearElement(sourceList);
  sourceCount.textContent = `${sources.length} ${sources.length === 1 ? languageCopy[currentLanguage].source : languageCopy[currentLanguage].sources}`;
  sources.forEach((source, index) => {
    const details = createElement("details", "source-item");
    if (index === 0) details.open = true;
    const summary = document.createElement("summary");
    summary.append(
      createElement("strong", "", `${source.law} · Section ${source.section}`),
      createElement(
        "small",
        "",
        `${source.source_file} · ${formatPages(source.page_start, source.page_end)}`,
      ),
    );
    details.append(summary, createElement("div", "source-text", source.text));
    sourceList.append(details);
  });
}

function formatPages(start, end) {
  return start === end ? `Page ${start}` : `Pages ${start}–${end}`;
}

function resetQuestion() {
  form.reset();
  setView("welcome");
  window.setTimeout(() => input.focus(), 100);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  requestLegalBrief(input.value);
});

followupState.addEventListener("submit", (event) => {
  event.preventDefault();
  const answers = pendingFollowups.map((item, index) => {
    const selected = followupState.querySelector(`input[name="followup-${index}"]:checked`);
    return `- ${item.question}: ${selected?.value || "I am not sure"}`;
  });
  const combined = `${lastQuestion}\n\nClarifying details supplied by the user:\n${answers.join("\n")}`;
  submitLegalBrief(combined);
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.question;
    requestLegalBrief(button.dataset.question);
  });
});

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => applyLanguage(button.dataset.language));
});

document.querySelector("#back-button").addEventListener("click", resetQuestion);
document.querySelector("#ask-another-button").addEventListener("click", resetQuestion);
document.querySelector("#retry-button").addEventListener("click", () => requestLegalBrief(lastQuestion));
document.querySelector("#about-button").addEventListener("click", () => aboutDialog.showModal());
document.querySelector("#dialog-close").addEventListener("click", () => aboutDialog.close());
aboutDialog.addEventListener("click", (event) => {
  if (event.target === aboutDialog) aboutDialog.close();
});

applyLanguage(currentLanguage);
