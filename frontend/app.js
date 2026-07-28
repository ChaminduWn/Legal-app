const form = document.querySelector("#question-form");
const input = document.querySelector("#question-input");
const welcomeView = document.querySelector("#welcome-view");
const resultsView = document.querySelector("#results-view");
const loadingState = document.querySelector("#loading-state");
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

function setView(name) {
  welcomeView.hidden = name !== "welcome";
  resultsView.hidden = name === "welcome";
  loadingState.hidden = name !== "loading";
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
    const [chatResponse, searchResponse] = await Promise.all([
      fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: lastQuestion }),
      }),
      fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: lastQuestion, limit: 6 }),
      }),
    ]);

    if (!chatResponse.ok || !searchResponse.ok) {
      let detail = "";
      if (!chatResponse.ok) {
        const errorPayload = await chatResponse.json().catch(() => ({}));
        detail = typeof errorPayload.detail === "string" ? errorPayload.detail : "";
      }
      throw new Error(
        detail || "The legal information service did not return a complete response.",
      );
    }

    const answer = await chatResponse.json();
    const search = await searchResponse.json();
    renderAnswer(answer, search.sources || []);
    setView("answer");
  } catch (error) {
    errorMessage.textContent =
      error instanceof Error ? error.message : "Please try again in a moment.";
    setView("error");
  }
}

function renderAnswer(answer, sources) {
  answerSummary.textContent = answer.summary;
  answerDisclaimer.textContent = answer.disclaimer;

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
  sourceCount.textContent = `${sources.length} ${sources.length === 1 ? "source" : "sources"}`;
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

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.question;
    requestLegalBrief(button.dataset.question);
  });
});

document.querySelector("#back-button").addEventListener("click", resetQuestion);
document.querySelector("#ask-another-button").addEventListener("click", resetQuestion);
document.querySelector("#retry-button").addEventListener("click", () => requestLegalBrief(lastQuestion));
document.querySelector("#about-button").addEventListener("click", () => aboutDialog.showModal());
document.querySelector("#dialog-close").addEventListener("click", () => aboutDialog.close());
aboutDialog.addEventListener("click", (event) => {
  if (event.target === aboutDialog) aboutDialog.close();
});
