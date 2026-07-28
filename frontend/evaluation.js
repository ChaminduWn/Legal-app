const runButton = document.querySelector("#run-evaluation");
const emptyState = document.querySelector("#evaluation-empty");
const tableWrap = document.querySelector("#table-wrap");
const resultsBody = document.querySelector("#evaluation-results");
const statusLabel = document.querySelector("#evaluation-status");

function token(section, expected = false) {
  const element = document.createElement("span");
  element.className = `section-token${expected ? " expected" : ""}`;
  element.textContent = section;
  return element;
}

function setMetric(id, value) {
  document.querySelector(id).textContent = value;
}

async function runEvaluation() {
  runButton.disabled = true;
  runButton.textContent = "Running…";
  statusLabel.textContent = "Testing local retrieval";

  try {
    const response = await fetch("/api/evaluations/run", { method: "POST" });
    if (!response.ok) throw new Error("Evaluation service unavailable.");
    const result = await response.json();

    setMetric("#metric-mode", result.retrieval_mode);
    setMetric("#metric-recall", `${Math.round(result.recall_at_k * 100)}%`);
    setMetric("#metric-mrr", result.mean_reciprocal_rank.toFixed(2));
    setMetric("#metric-passed", result.passed);
    setMetric("#metric-total", `of ${result.total} scenarios`);

    resultsBody.replaceChildren();
    for (const item of result.cases) {
      const row = document.createElement("tr");
      const question = document.createElement("td");
      question.textContent = item.question;
      const expected = document.createElement("td");
      item.expected_sections.forEach((section) => expected.append(token(section, true)));
      const retrieved = document.createElement("td");
      item.retrieved_sections.forEach((section) => retrieved.append(token(section)));
      const outcome = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `result-badge ${item.passed ? "pass" : "fail"}`;
      badge.textContent = item.passed ? "Pass" : "Review";
      outcome.append(badge);
      row.append(question, expected, retrieved, outcome);
      resultsBody.append(row);
    }

    emptyState.hidden = true;
    tableWrap.hidden = false;
    statusLabel.textContent = `Completed · ${result.retrieval_mode} retrieval`;
  } catch (error) {
    statusLabel.textContent = error instanceof Error ? error.message : "Evaluation failed";
  } finally {
    runButton.disabled = false;
    runButton.innerHTML = 'Run again <span aria-hidden="true">→</span>';
  }
}

runButton.addEventListener("click", runEvaluation);

