"use strict";
const $ = id => document.getElementById(id);
const state = { documents: [], topics: [], library: {}, activeDocument: null, sections: [], activeSection: null, sectionSnapshot: "", metadataSnapshot: "", busy: false, loading: false, loadError: false, selection: 0, controller: null, page: 1, pageSize: 40, draftLawId: "", publicationError: "" };
let toastTimer;
const statusNames = { unreviewed: "Not started", in_review: "In review", verified: "Published", superseded: "Withdrawn" };
const passageStatus = { unreviewed: "Not reviewed", in_review: "In review", verified: "Verified", needs_correction: "Needs correction" };
const pending = d => !["verified", "superseded"].includes(d.status);
const textComplete = d => d.section_count > 0 && d.verified_sections === d.section_count;
const extractionIssue = d => !d.section_count || (d.extraction_confidence != null && d.extraction_confidence < .8) || (d.extraction_warnings || []).some(w => /blank|unreadable|failed|missing|below|need(?:s)? OCR|no readable/i.test(w));
const sourceMissing = d => !d.official_source_url || !d.publication_date || !d.effective_date || !d.instrument_id;
const locator = value => /^page[ -]?\d/i.test(value) ? value.replace(/^page[ -]?/i, "Page ") : `Section ${value}`;
const routePart = encodeURIComponent;
const node = (tag, text, className) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (className) e.className = className; return e; };
function errorMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(errorMessage).join(" ");
  if (detail?.msg) return `${(detail.loc || []).filter(x => x !== "body").join(" → ")}: ${detail.msg}`;
  return detail?.detail ? errorMessage(detail.detail) : "The request could not be completed. Try again.";
}
function showToast(message) { clearTimeout(toastTimer); $("toast").textContent = errorMessage(message); $("toast").hidden = false; toastTimer = setTimeout(() => { $("toast").hidden = true; }, 8000); }
async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    if (response.status === 401) { $("session-warning").hidden = false; throw new Error("Your session expired. Sign in in a new tab, then retry here."); }
    const payload = await response.json().catch(() => ({})); throw new Error(errorMessage(payload.detail || payload));
  }
  return response.json();
}
function sectionValue() { return { heading: $("section-heading").value.trim() || null, text: $("section-text").value.trim(), status: $("section-status").value, notes: $("section-notes").value.trim() || null }; }
function formSnapshot(form) { return JSON.stringify(Array.from(form.elements).filter(e => e.id && !["submit", "button"].includes(e.type)).map(e => [e.id, e.type === "checkbox" ? e.checked : e.multiple ? Array.from(e.selectedOptions, o => o.value) : e.value])); }
const sectionDirty = () => !!state.activeSection && JSON.stringify(sectionValue()) !== state.sectionSnapshot;
const metadataDirty = () => $("metadata-dialog").open && formSnapshot($("metadata-form")) !== state.metadataSnapshot;
function allowLeave() { if (state.busy) { showToast("Please wait for the current save to finish."); return false; } return !(sectionDirty() || metadataDirty()) || window.confirm("Discard your unsaved changes? Saved reviews will be kept."); }
window.addEventListener("beforeunload", event => { if (state.busy || sectionDirty() || metadataDirty()) { event.preventDefault(); event.returnValue = ""; } });
function setBusy(value) {
  state.busy = value;
  document.querySelectorAll("#section-form input, #section-form textarea, #section-form select, #section-form button, #metadata-form input, #metadata-form textarea, #metadata-form select, #metadata-form button, #import-form input, #import-form select, #import-form button, [data-lock], [data-close]").forEach(e => {
    if (value) { e.dataset.previousDisabled = String(e.disabled); e.disabled = true; }
    else if (e.dataset.previousDisabled != null) { e.disabled = e.dataset.previousDisabled === "true"; delete e.dataset.previousDisabled; }
  });
  if (!value) { renderDocuments(); if (state.activeDocument && !state.loading) renderSections(); }
  document.body.classList.toggle("is-saving", value); $("save-state").textContent = value ? "Saving…" : sectionDirty() ? "Unsaved changes" : "Saved"; renderChecklist();
}
function filteredDocuments() {
  const query = $("document-filter").value.trim().toLowerCase(), status = $("status-filter").value, attention = $("attention-filter").value, topic = $("topic-filter").value;
  const results = state.documents.filter(d => (!query || `${d.law} ${d.source_file} ${d.edition_year || ""}`.toLowerCase().includes(query)) && (status === "all" || (status === "pending" ? pending(d) : status === "text_complete" ? pending(d) && textComplete(d) : d.status === status)) && (attention === "all" || (attention === "extraction" ? extractionIssue(d) : sourceMissing(d))) && (!topic || (d.topic_ids || []).includes(topic)));
  const order = $("sort-filter").value;
  return results.sort((a, b) => {
    if (order === "priority") { const score = d => (extractionIssue(d) ? 2 : 0) + (pending(d) && d.verified_sections > 0 ? 1 : 0); const diff = score(b) - score(a); if (diff) return diff; }
    if (order === "progress") { const diff = b.verified_sections / (b.section_count || 1) - a.verified_sections / (a.section_count || 1); if (diff) return diff; }
    if (order === "year" && a.edition_year !== b.edition_year) return (b.edition_year || 0) - (a.edition_year || 0);
    return a.law.localeCompare(b.law) || a.source_file.localeCompare(b.source_file);
  });
}
function renderOverview() {
  $("count-pending").textContent = state.documents.filter(pending).length; $("count-attention").textContent = state.documents.filter(extractionIssue).length;
  $("count-complete").textContent = state.documents.filter(d => pending(d) && textComplete(d)).length; $("count-published").textContent = state.documents.filter(d => d.status === "verified").length; $("document-count").textContent = `${state.documents.length} documents`;
}
function renderDocuments() {
  const documents = filteredDocuments(), totalPages = Math.max(1, Math.ceil(documents.length / state.pageSize)); state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * state.pageSize; $("document-list").replaceChildren();
  for (const d of documents.slice(start, start + state.pageSize)) {
    const button = node("button", null, `document-item${state.activeDocument?.source_file === d.source_file ? " active" : ""}`); button.type = "button"; button.disabled = state.busy; button.setAttribute("aria-pressed", String(state.activeDocument?.source_file === d.source_file));
    button.append(node("strong", d.law), node("small", `${d.edition_year || "Year to check"} · ${d.section_count} passages`));
    const row = node("div", null, "document-meta"); row.append(node("span", `${d.verified_sections}/${d.section_count} checked`), node("span", statusNames[d.status] || d.status, `status-chip ${d.status}`)); button.append(row);
    if (extractionIssue(d)) button.append(node("small", "Extraction needs attention", "attention-label"));
    button.addEventListener("click", () => selectDocument(d)); $("document-list").append(button);
  }
  $("queue-message").textContent = documents.length ? `${start + 1}–${Math.min(start + state.pageSize, documents.length)} of ${documents.length} matching documents` : "No documents match these filters. Try resetting them.";
  $("page-label").textContent = `${state.page} / ${totalPages}`; $("previous-page").disabled = state.page === 1; $("next-page").disabled = state.page === totalPages; $("start-review").disabled = state.busy || !documents.some(pending);
}
async function loadCollection() {
  const [documents, library, topics] = await Promise.all([request("/api/admin/documents"), request("/api/admin/library"), request("/api/admin/topics").catch(() => null)]);
  state.documents = documents; state.library = library; state.topics = topics;
  const selected = $("topic-filter").value; $("topic-filter").replaceChildren(new Option("All topics", "")); (topics || []).forEach(t => $("topic-filter").add(new Option(t.name, t.topic_id))); $("topic-filter").value = selected; $("topic-filter").disabled = topics === null;
  renderOverview(); renderDocuments();
}
function originalUrl(d, page = 1) { const base = `/api/admin/documents/${routePart(d.source_file)}`; return d.extraction_method === "ocr" ? `${base}/original?page=${page}` : `${base}/pdf#page=${page}`; }
function showOriginal(page = 1) { if (!state.activeDocument) return; const url = originalUrl(state.activeDocument, page); $("pdf-frame").src = url; $("open-original").href = url; $("pdf-open-link").href = url; $("pdf-page-label").textContent = `Original page ${page}`; }
function rememberPosition() { try { sessionStorage.setItem("neethi-review-position", JSON.stringify({ document: state.activeDocument?.source_file, section: state.activeSection?.source_id })); } catch {} }
async function selectDocument(d, options = {}) {
  if (!options.skipGuard && !allowLeave()) return;
  clearTimeout(toastTimer); $("toast").hidden = true;
  const token = ++state.selection; state.controller?.abort(); state.controller = new AbortController();
  state.activeDocument = d; state.activeSection = null; state.sections = []; state.sectionSnapshot = ""; state.loading = true; state.loadError = false; state.publicationError = "";
  $("workspace-empty").hidden = true; $("workspace-content").hidden = false; $("section-form").hidden = true; $("editor-empty").hidden = false; $("editor-empty").textContent = "Loading passages…";
  $("document-title").textContent = d.law; $("document-filename").textContent = d.source_file; $("section-filter").value = ""; $("section-status-filter").value = "pending";
  $("document-load-error").hidden = true; $("extraction-complete").checked = false; $("authority-checked").checked = false; $("publication-panel").open = false; $("metadata-button").disabled = true; $("section-list").replaceChildren(); $("section-count").textContent = "Loading…";
  showOriginal(); renderDocuments(); renderDocumentHeader(); renderChecklist(); renderWarnings(d, [], []);
  $("review-workspace").scrollIntoView({ block: "start", behavior: "instant" });
  if (matchMedia("(max-width: 760px)").matches) $("queue-details").open = false;
  try {
    const base = `/api/admin/documents/${routePart(d.source_file)}`, signal = state.controller.signal;
    const sections = await request(`${base}/sections`, { signal }); if (token !== state.selection) return;
    state.sections = sections; state.loading = false; $("section-status-filter").value = sections.some(s => s.status !== "verified") ? "pending" : "all"; $("metadata-button").disabled = state.busy;
    const chosen = sections.find(s => s.source_id === options.sectionId && s.status !== "verified") || sections.find(s => s.status !== "verified") || sections[0];
    selectSection(chosen || null, true); renderDocumentHeader(); renderChecklist();
    Promise.all([request(`${base}/relationships`, { signal }).catch(() => []), request(`${base}/audit`, { signal }).catch(() => [])]).then(([relations, audit]) => { if (token === state.selection) renderWarnings(d, relations, audit); });
    rememberPosition();
  } catch (error) {
    if (token !== state.selection || error.name === "AbortError") return;
    state.loading = false; state.loadError = true; $("metadata-button").disabled = false; $("document-load-error").hidden = false; $("document-error-text").textContent = error.message; $("editor-empty").textContent = "Passages could not be loaded. Your original is still available above."; renderChecklist();
  }
}
function renderDocumentHeader() {
  const d = state.activeDocument; if (!d) return;
  $("document-progress").textContent = `${d.verified_sections} of ${d.section_count} passages checked · ${statusNames[d.status] || d.status} · ${d.document_type}`; $("document-progress-bar").value = d.section_count ? d.verified_sections / d.section_count * 100 : 0; $("review-complete").hidden = state.loading || !textComplete(d) || d.status === "verified";
}
function renderWarnings(d, relations, audit) {
  const warnings = d.extraction_warnings || []; $("extraction-warnings").replaceChildren(...warnings.map(w => node("li", w))); $("extraction-summary").textContent = extractionIssue(d) ? "Extraction needs attention — view notes" : "Extraction notes & history"; $("extraction-details").open = extractionIssue(d);
  $("analysis-summary").textContent = `${relations.length} proposed law link(s) need independent review.${audit[0] ? ` Latest activity: ${audit[0].action.replaceAll("_", " ")}.` : ""}`;
  const pages = new Set();
  for (const warning of warnings) { const match = warning.match(/Pages still blank\/unreadable after OCR:\s*([\d, ]+)/i); if (match) match[1].split(",").map(Number).filter(n => Number.isInteger(n) && n > 0).forEach(n => pages.add(n)); }
  $("flagged-pages").replaceChildren();
  for (const page of Array.from(pages).slice(0, 12)) { const link = node("a", `Page ${page} ↗`); link.href = originalUrl(d, page); link.target = "_blank"; link.rel = "noopener"; $("flagged-pages").append(link); }
}
function renderSections() {
  const query = $("section-filter").value.trim().toLowerCase(), filter = $("section-status-filter").value;
  const sections = state.sections.filter(s => (!query || `${s.section} ${s.heading || ""} ${s.text}`.toLowerCase().includes(query)) && (filter === "all" || (filter === "pending" ? s.status !== "verified" : s.status === filter)));
  $("section-count").textContent = `${state.sections.filter(s => s.status !== "verified").length} still to review`; $("section-list").replaceChildren();
  for (const s of sections) { const button = node("button", null, `section-item${state.activeSection?.source_id === s.source_id ? " active" : ""}`); button.type = "button"; button.disabled = state.busy; button.setAttribute("aria-pressed", String(state.activeSection?.source_id === s.source_id)); button.append(node("strong", locator(s.section)), node("small", passageStatus[s.status] || s.status, `passage-state ${s.status}`)); button.addEventListener("click", () => selectSection(s)); $("section-list").append(button); }
  if (!sections.length) $("section-list").append(node("p", "No passages match this filter.", "list-empty"));
}
function selectSection(s, skipGuard = false) {
  if (!skipGuard && !allowLeave()) return;
  state.activeSection = s; state.sectionSnapshot = ""; $("section-form").hidden = !s; $("editor-empty").hidden = !!s;
  if (s) { $("editing-section").textContent = locator(s.section); $("section-heading").value = s.heading || ""; $("section-text").value = s.text; $("section-status").value = s.status; $("section-notes").value = s.notes || ""; $("section-pages").textContent = s.page_start === s.page_end ? `Original PDF page ${s.page_start}` : `Original PDF pages ${s.page_start}–${s.page_end}`; state.sectionSnapshot = JSON.stringify(sectionValue()); $("save-state").textContent = "Saved"; showOriginal(s.page_start); }
  else $("editor-empty").textContent = textComplete(state.activeDocument) ? "Text review complete. Check the source details before publishing." : "No extracted passages. Check the original and the extraction notes.";
  renderSections(); rememberPosition();
}
function sourceBlockers(d) {
  const policy = state.library.publication_policy || {};
  if (!policy.require_source_details && !policy.require_edition) return [];
  const missing = [];
  if (policy.require_source_details) { if (!d.official_source_url) missing.push("Add the official source link."); if (!d.publication_date) missing.push("Record the publication date."); if (!d.effective_date) missing.push("Record the effective date."); }
  if (policy.require_edition && !d.instrument_id) missing.push("Record the law edition and its effective period.");
  if (d.document_type === "amendment" && (!d.target_instrument_id || !["amends", "repeals"].includes(d.relation_type))) missing.push("Link the amendment to the affected law.");
  return missing;
}
function renderChecklist() {
  const d = state.activeDocument; if (!d) return;
  const complete = textComplete(d), blockers = sourceBlockers(d), published = d.status === "verified";
  $("step-text").classList.toggle("complete", complete); $("step-source").classList.toggle("complete", !sourceMissing(d)); $("step-publish").classList.toggle("complete", published);
  $("step-text-state").textContent = complete ? "Checked" : `${Math.max(0, d.section_count - d.verified_sections)} remaining`; $("step-source-state").textContent = sourceMissing(d) ? "Details to check" : "Recorded"; $("step-publish-state").textContent = published ? "Published" : "Not published";
  if (!d.section_count) blockers.unshift("Recover the missing text from the original before publication.");
  else if (!complete) blockers.unshift(`Verify all ${d.section_count - d.verified_sections} remaining passages against the original.`);
  if (d.status === "superseded") blockers.push("Change availability to In review before publishing.");
  if (sectionDirty()) blockers.push("Save the passage changes before publishing.");
  if (state.loadError) blockers.push("Reload the passages before publishing.");
  const checked = $("extraction-complete").checked && $("authority-checked").checked;
  $("publication-blockers").replaceChildren(...blockers.map(b => node("li", b))); $("publication-readiness").textContent = published ? "Published" : blockers.length ? `${blockers.length} item(s) to complete` : checked ? "Ready to publish" : "Confirm both checks";
  $("publication-message").textContent = published ? "This source is available in public legal search." : state.library.publication_policy?.mode === "production" ? "Source details and a dated edition are required for publication. You can save an incomplete draft." : "Development mode: source dates and edition details are recommended here and required for production. Never guess a date.";
  if (state.publicationError) $("publication-message").textContent = `Not published: ${state.publicationError}`;
  $("publication-message").classList.toggle("error", !!state.publicationError);
  $("publication-confirmations").hidden = published; $("approve-button").hidden = published; $("approve-button").disabled = state.busy || state.loading || blockers.length > 0 || !checked;
}
$("section-form").addEventListener("input", () => { $("save-state").textContent = sectionDirty() ? "Unsaved changes" : "Saved"; renderChecklist(); });
$("section-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.busy || !state.activeSection || !event.currentTarget.reportValidity()) return;
  const mode = event.submitter?.value || "save", current = state.activeSection, d = state.activeDocument, update = sectionValue(); if (mode === "verify") update.status = "verified";
  setBusy(true);
  try {
    await request(`/api/admin/sections/${routePart(current.source_id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) });
    state.publicationError = ""; $("extraction-complete").checked = false; $("authority-checked").checked = false;
    Object.assign(current, update, { semantic_indexed: false }); d.verified_sections = state.sections.filter(s => s.status === "verified").length;
    if (d.status === "verified") { d.status = "in_review"; $("extraction-complete").checked = false; $("authority-checked").checked = false; }
    selectSection(current, true);
    if (mode !== "save") {
      const index = state.sections.indexOf(current), after = state.sections.slice(index + 1).concat(state.sections.slice(0, index)), next = after.find(s => s.status !== "verified");
      if (next) { selectSection(next, true); $("editing-section").focus({ preventScroll: true }); }
      else if (textComplete(d)) { selectSection(null, true); $("publication-panel").open = true; }
    }
    renderOverview(); renderDocuments(); renderDocumentHeader(); renderChecklist();
    showToast(textComplete(d) ? "Text review complete. Check source details before publishing." : mode === "save" ? "Review saved." : `Saved. ${d.section_count - d.verified_sections} passages still need review.`);
  } catch (error) { showToast(error.message); }
  finally { setBusy(false); }
});
async function nextDocument(resume = false) {
  const queue = filteredDocuments().filter(pending); let chosen;
  if (resume) { try { const last = JSON.parse(sessionStorage.getItem("neethi-review-position")); chosen = queue.find(d => d.source_file === last?.document); if (chosen) return selectDocument(chosen, { sectionId: last.section }); } catch {} chosen = queue[0]; }
  else { const index = queue.findIndex(d => d.source_file === state.activeDocument?.source_file); chosen = queue[(index + 1) % queue.length]; if (chosen?.source_file === state.activeDocument?.source_file) chosen = null; }
  if (chosen) await selectDocument(chosen); else showToast("No other document awaiting review matches these filters.");
}
function instruments() { return Array.from(new Map(state.documents.filter(d => d.instrument_id).map(d => [d.instrument_id, d.instrument_title || d.law])).entries()); }
function updateEditionFields() {
  const enabled = $("has-edition").checked, isNew = $("instrument-choice").value === "__new__"; $("edition-fields").disabled = !enabled; $("new-law-field").hidden = !isNew;
  ["instrument-choice", "edition-year", "valid-from"].forEach(id => { $(id).required = enabled; }); $("instrument-title").required = enabled && isNew;
}
function updatePreviousEditions(selected = "") {
  const id = $("instrument-choice").value; $("supersedes-source").replaceChildren(new Option("None", ""));
  state.documents.filter(d => d.source_file !== state.activeDocument.source_file && d.instrument_id === id && ["original", "consolidated"].includes(d.version_kind)).forEach(d => $("supersedes-source").add(new Option(`${d.edition_year} · ${d.law}`, d.source_file))); $("supersedes-source").value = selected;
}
function openMetadata() {
  if (state.busy || !state.activeDocument) return;
  const d = state.activeDocument; state.draftLawId = "";
  const values = { "official-source-url": d.official_source_url, "publication-date": d.publication_date, "effective-date": d.effective_date, "document-type": d.document_type, "document-status": d.status === "verified" ? "in_review" : d.status, "instrument-title": d.instrument_title || d.law, "instrument-id": d.instrument_id, "edition-year": d.edition_year, "version-kind": d.version_kind || "original", "valid-from": d.valid_from, "valid-to": d.valid_to, "relation-type": d.relation_type || "references", "relationship-evidence": d.relationship_evidence, "document-notes": d.notes };
  Object.entries(values).forEach(([id, value]) => { $(id).value = value ?? ""; });
  $("instrument-choice").replaceChildren(new Option("Choose a law…", ""), ...instruments().map(([id, title]) => new Option(title, id)), new Option("Add a law…", "__new__")); $("instrument-choice").value = d.instrument_id || "";
  $("target-instrument-id").replaceChildren(new Option("None", ""), ...instruments().map(([id, title]) => new Option(title, id))); $("target-instrument-id").value = d.target_instrument_id || "";
  $("has-edition").checked = !!d.instrument_id; $("has-edition").disabled = !!d.instrument_id; $("edition-details").open = !!d.instrument_id;
  updateEditionFields(); updatePreviousEditions(d.supersedes_source || "");
  $("document-topics").replaceChildren(...(state.topics || []).map(t => new Option(t.name, t.topic_id, false, (d.topic_ids || []).includes(t.topic_id)))); $("document-topics").disabled = state.topics === null;
  $("metadata-policy").textContent = state.library.publication_policy?.mode === "production" ? "To publish: add the official link, publication and effective dates, and a dated law edition. You may save partial source details as a draft." : "Save what you have verified. Source dates and edition details are optional in development and required for production publication.";
  $("edition-policy").textContent = state.library.publication_policy?.require_edition ? "Required to publish" : "When verified";
  $("metadata-error").hidden = true; $("metadata-dialog").showModal(); $("metadata-dialog").scrollTop = 0; state.metadataSnapshot = formSnapshot($("metadata-form"));
}
$("has-edition").addEventListener("change", updateEditionFields);
$("instrument-choice").addEventListener("change", () => { state.draftLawId = ""; updateEditionFields(); updatePreviousEditions(); });
function newLawId(title) {
  const match = instruments().find(([, name]) => name.toLowerCase() === title.toLowerCase()); if (match) return match[0];
  if (!state.draftLawId) { const slug = title.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 100), suffix = crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36); state.draftLawId = `${slug.length >= 2 ? slug : "law"}-${suffix}`; }
  return state.draftLawId;
}
$("metadata-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.busy || !event.currentTarget.reportValidity()) return;
  const d = state.activeDocument, value = id => $(id).value.trim() || null;
  const update = { official_source_url: value("official-source-url"), publication_date: value("publication-date"), effective_date: value("effective-date"), document_type: value("document-type"), status: value("document-status"), notes: value("document-notes"), version: null };
  if (state.topics !== null) update.topic_ids = Array.from($("document-topics").selectedOptions, o => o.value);
  if ($("has-edition").checked) {
    const choice = $("instrument-choice").value, title = choice === "__new__" ? value("instrument-title") : instruments().find(([id]) => id === choice)?.[1];
    update.version = { instrument_id: choice === "__new__" ? newLawId(title) : choice, instrument_title: title, edition_year: Number(value("edition-year")), version_kind: value("version-kind"), valid_from: value("valid-from"), valid_to: value("valid-to"), supersedes_source: value("supersedes-source"), target_instrument_id: value("target-instrument-id"), relation_type: value("relation-type"), relationship_evidence: value("relationship-evidence") };
  }
  setBusy(true); $("metadata-error").hidden = true;
  try {
    await request(`/api/admin/documents/${routePart(d.source_file)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) });
    state.publicationError = "";
    Object.assign(d, update); delete d.version; if (update.version) Object.assign(d, update.version);
    $("extraction-complete").checked = false; $("authority-checked").checked = false; $("metadata-dialog").close(); renderOverview(); renderDocuments(); renderDocumentHeader(); renderChecklist(); showToast("Source details saved. The document still needs explicit publication approval.");
  } catch (error) { $("metadata-error").textContent = error.message; $("metadata-error").hidden = false; }
  finally { setBusy(false); }
});
$("approve-button").addEventListener("click", async () => {
  if (sectionDirty()) { showToast("Save the passage changes before publishing."); return; }
  if (state.busy || !allowLeave() || $("approve-button").disabled) return;
  const d = state.activeDocument; setBusy(true); $("approve-button").textContent = "Publishing…";
  try {
    await request(`/api/admin/documents/${routePart(d.source_file)}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extraction_complete: $("extraction-complete").checked, authority_checked: $("authority-checked").checked }) });
    d.status = "verified"; renderOverview(); renderDocuments(); renderDocumentHeader(); renderChecklist(); showToast("Document approved and published to public search.");
  } catch (error) { state.publicationError = error.message; showToast(error.message); }
  finally { setBusy(false); $("approve-button").textContent = "Approve & publish"; }
});
$("import-button").addEventListener("click", () => { if (allowLeave()) $("import-dialog").showModal(); });
$("import-form").addEventListener("submit", async event => {
  event.preventDefault(); if (state.busy) return;
  const form = event.currentTarget, payload = new FormData(form), files = payload.getAll("files"); payload.delete("files");
  try {
    const pdfs = files.filter(f => f.type === "application/pdf" || /\.pdf$/i.test(f.name)), isPdf = pdfs.length === 1;
    if (pdfs.length && files.length !== 1) throw new Error("Choose one PDF at a time, without other files.");
    if (isPdf && files[0].size > 25 * 1024 * 1024) throw new Error("PDF must be 25 MB or smaller.");
    if (!isPdf && (files.length > 30 || files.some(f => f.size > 12 * 1024 * 1024))) throw new Error("Choose up to 30 images, each 12 MB or smaller.");
    if (isPdf) payload.append("document", files[0]); else files.forEach(f => payload.append("images", f));
    setBusy(true); $("import-message").textContent = "Extracting text. Keep this page open…";
    const imported = await request(isPdf ? "/api/admin/documents/upload" : "/api/admin/documents/scan", { method: "POST", body: payload });
    form.reset(); $("import-dialog").close(); await loadCollection(); await selectDocument(state.documents.find(d => d.source_file === imported.source_file) || imported, { skipGuard: true }); showToast("Original saved and text extracted. This document is in review.");
  } catch (error) { $("import-message").textContent = error.message; }
  finally { setBusy(false); }
});
function closeDialog(dialog) {
  if (state.busy) return;
  const dirty = dialog.id === "metadata-dialog" ? metadataDirty() : dialog.id === "import-dialog" ? Array.from($("import-form").elements).some(e => e.type === "file" ? e.files.length : e.tagName === "INPUT" && e.value) : false;
  if (!dirty || window.confirm("Discard the unsaved information in this dialog?")) dialog.close();
}
document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("cancel", event => { event.preventDefault(); closeDialog(dialog); }));
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => closeDialog($(button.dataset.close))));
document.querySelectorAll("a[data-leave]").forEach(link => link.addEventListener("click", event => { if (!allowLeave()) event.preventDefault(); }));
document.querySelectorAll("form[data-leave]").forEach(form => form.addEventListener("submit", event => { if (!allowLeave()) event.preventDefault(); }));
$("start-review").addEventListener("click", () => nextDocument(true)); $("next-document").addEventListener("click", () => nextDocument()); $("retry-document").addEventListener("click", () => selectDocument(state.activeDocument));
["metadata-button", "complete-details"].forEach(id => $(id).addEventListener("click", openMetadata));
["document-filter", "status-filter", "attention-filter", "topic-filter", "sort-filter"].forEach(id => $(id).addEventListener(id === "document-filter" ? "input" : "change", () => { state.page = 1; renderDocuments(); }));
["section-filter", "section-status-filter"].forEach(id => $(id).addEventListener(id === "section-filter" ? "input" : "change", renderSections));
["extraction-complete", "authority-checked"].forEach(id => $(id).addEventListener("change", renderChecklist));
$("previous-page").addEventListener("click", () => { state.page--; renderDocuments(); }); $("next-page").addEventListener("click", () => { state.page++; renderDocuments(); });
function resetFilters() { $("document-filter").value = ""; $("status-filter").value = "pending"; $("attention-filter").value = "all"; $("topic-filter").value = ""; $("sort-filter").value = "priority"; state.page = 1; }
$("clear-filters").addEventListener("click", () => { resetFilters(); renderDocuments(); });
document.querySelectorAll("[data-queue]").forEach(button => button.addEventListener("click", () => { resetFilters(); if (button.dataset.queue === "attention") { $("status-filter").value = "all"; $("attention-filter").value = "extraction"; } else $("status-filter").value = button.dataset.queue; $("queue-details").open = true; renderDocuments(); }));
$("refresh-button").addEventListener("click", async () => {
  if (!allowLeave()) return;
  const source = state.activeDocument?.source_file, sectionId = state.activeSection?.source_id;
  try { await loadCollection(); const d = state.documents.find(d => d.source_file === source); if (d) await selectDocument(d, { skipGuard: true, sectionId }); showToast("Collection refreshed."); }
  catch (error) { showToast(error.message); }
});

// Change Password functionality
$("change-password-button").addEventListener("click", () => {
  if (!allowLeave()) return;
  $("change-password-form").reset();
  const msg = $("password-message");
  msg.hidden = true;
  msg.textContent = "";
  msg.className = "form-message";
  $("change-password-dialog").showModal();
});

$("change-password-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.busy) return;
  const current_password = $("current-password").value;
  const new_password = $("new-password").value;
  const confirm_password = $("confirm-password").value;
  const msg = $("password-message");

  if (new_password !== confirm_password) {
    msg.textContent = "New passwords do not match.";
    msg.className = "form-message error";
    msg.hidden = false;
    return;
  }
  if (new_password.length < 8) {
    msg.textContent = "New password must be at least 8 characters long.";
    msg.className = "form-message error";
    msg.hidden = false;
    return;
  }

  try {
    setBusy(true);
    $("change-password-submit").textContent = "Updating…";
    const res = await request("/api/admin/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password, new_password, confirm_password }),
    });
    $("change-password-dialog").close();
    $("change-password-form").reset();
    showToast(res.message || "Password changed successfully.");
  } catch (error) {
    msg.textContent = error.message;
    msg.className = "form-message error";
    msg.hidden = false;
  } finally {
    setBusy(false);
    $("change-password-submit").textContent = "Update password";
  }
});

// Site Content customization
const siteContentState = {
  data: null,
  lang: "en",
};

function readSiteContentFromForm(lang) {
  if (!siteContentState.data) return;
  siteContentState.data[lang] = {
    ...siteContentState.data[lang],
    appName: $("sc-appName").value.trim(),
    subtitle: $("sc-subtitle").value.trim(),
    eyebrow: $("sc-eyebrow").value.trim(),
    headline: $("sc-headline").value.trim(),
    intro: $("sc-intro").value.trim(),
    questionLabel: $("sc-questionLabel").value.trim(),
    submitButton: $("sc-submitButton").value.trim(),
    placeholder: $("sc-placeholder").value.trim(),
    privacyNotice: $("sc-privacyNotice").value.trim(),
    examplesTitle: $("sc-examplesTitle").value.trim(),
    examplesSubtitle: $("sc-examplesSubtitle").value.trim(),
    examples: [
      {
        title: $("sc-ex1-title").value.trim(),
        desc: $("sc-ex1-desc").value.trim(),
        query: $("sc-ex1-query").value.trim(),
      },
      {
        title: $("sc-ex2-title").value.trim(),
        desc: $("sc-ex2-desc").value.trim(),
        query: $("sc-ex2-query").value.trim(),
      },
      {
        title: $("sc-ex3-title").value.trim(),
        desc: $("sc-ex3-desc").value.trim(),
        query: $("sc-ex3-query").value.trim(),
      },
    ],
    aboutTitle: $("sc-aboutTitle").value.trim(),
    aboutText: $("sc-aboutText").value.trim(),
    emergencyTitle: $("sc-emergencyTitle").value.trim(),
    emergencyText: $("sc-emergencyText").value.trim(),
    footerDisclaimer: $("sc-footerDisclaimer").value.trim(),
    footerSources: $("sc-footerSources").value.trim(),
  };
}

function populateSiteContentForm(lang) {
  if (!siteContentState.data || !siteContentState.data[lang]) return;
  const c = siteContentState.data[lang];
  $("sc-appName").value = c.appName || "";
  $("sc-subtitle").value = c.subtitle || "";
  $("sc-eyebrow").value = c.eyebrow || "";
  $("sc-headline").value = c.headline || "";
  $("sc-intro").value = c.intro || "";
  $("sc-questionLabel").value = c.questionLabel || "";
  $("sc-submitButton").value = c.submitButton || "";
  $("sc-placeholder").value = c.placeholder || "";
  $("sc-privacyNotice").value = c.privacyNotice || "";
  $("sc-examplesTitle").value = c.examplesTitle || "";
  $("sc-examplesSubtitle").value = c.examplesSubtitle || "";
  
  const ex = c.examples || [];
  $("sc-ex1-title").value = ex[0]?.title || "";
  $("sc-ex1-desc").value = ex[0]?.desc || "";
  $("sc-ex1-query").value = ex[0]?.query || "";

  $("sc-ex2-title").value = ex[1]?.title || "";
  $("sc-ex2-desc").value = ex[1]?.desc || "";
  $("sc-ex2-query").value = ex[1]?.query || "";

  $("sc-ex3-title").value = ex[2]?.title || "";
  $("sc-ex3-desc").value = ex[2]?.desc || "";
  $("sc-ex3-query").value = ex[2]?.query || "";

  $("sc-aboutTitle").value = c.aboutTitle || "";
  $("sc-aboutText").value = c.aboutText || "";
  $("sc-emergencyTitle").value = c.emergencyTitle || "";
  $("sc-emergencyText").value = c.emergencyText || "";
  $("sc-footerDisclaimer").value = c.footerDisclaimer || "";
  $("sc-footerSources").value = c.footerSources || "";

  document.querySelectorAll(".site-content-tabs .tab-button").forEach(btn => {
    const active = btn.dataset.tabLang === lang;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });
}

async function openSiteContentDialog() {
  const msg = $("site-content-message");
  msg.hidden = true;
  msg.textContent = "";
  msg.className = "form-message";
  try {
    setBusy(true);
    const content = await request("/api/site-content");
    siteContentState.data = content;
    siteContentState.lang = "en";
    populateSiteContentForm("en");
    $("site-content-dialog").showModal();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

$("site-content-button").addEventListener("click", () => {
  if (allowLeave()) openSiteContentDialog();
});

document.querySelectorAll(".site-content-tabs .tab-button").forEach(btn => {
  btn.addEventListener("click", () => {
    const targetLang = btn.dataset.tabLang;
    if (targetLang === siteContentState.lang) return;
    readSiteContentFromForm(siteContentState.lang);
    siteContentState.lang = targetLang;
    populateSiteContentForm(targetLang);
  });
});

$("site-content-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.busy) return;
  readSiteContentFromForm(siteContentState.lang);
  const msg = $("site-content-message");
  msg.hidden = true;
  try {
    setBusy(true);
    $("site-content-save").textContent = "Saving…";
    const res = await request("/api/admin/site-content", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(siteContentState.data),
    });
    siteContentState.data = res.content || siteContentState.data;
    populateSiteContentForm(siteContentState.lang);
    msg.textContent = "Site content saved successfully! Changes are live on the user pages.";
    msg.className = "form-message success";
    msg.hidden = false;
    showToast("Site content updated successfully.");
  } catch (error) {
    msg.textContent = error.message;
    msg.className = "form-message error";
    msg.hidden = false;
  } finally {
    setBusy(false);
    $("site-content-save").textContent = "Save site content";
  }
});

$("site-content-reset").addEventListener("click", async () => {
  if (state.busy) return;
  if (!window.confirm("Reset all site content back to the system defaults? Your current modifications will be replaced.")) return;
  const msg = $("site-content-message");
  msg.hidden = true;
  try {
    setBusy(true);
    const res = await request("/api/admin/site-content/reset", { method: "POST" });
    siteContentState.data = res.content;
    populateSiteContentForm(siteContentState.lang);
    msg.textContent = "Site content reset to default values.";
    msg.className = "form-message success";
    msg.hidden = false;
    showToast("Site content restored to defaults.");
  } catch (error) {
    msg.textContent = error.message;
    msg.className = "form-message error";
    msg.hidden = false;
  } finally {
    setBusy(false);
  }
});

$("admin-brand").addEventListener("click", event => {
  if (!allowLeave()) {
    event.preventDefault();
    return;
  }
  if (window.location.pathname === "/admin/documents") {
    event.preventDefault();
    state.activeDocument = null;
    state.activeSection = null;
    state.sectionSnapshot = "";
    $("workspace-content").hidden = true;
    $("workspace-empty").hidden = false;
    resetFilters();
    renderDocuments();
  }
});

loadCollection().catch(error => { $("queue-message").textContent = `Could not load the collection: ${error.message} Use Refresh to retry.`; showToast(error.message); });

const topbar = document.querySelector(".review-topbar");
if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => {
  document.documentElement.style.setProperty("--review-topbar-height", `${topbar.getBoundingClientRect().height}px`);
}).observe(topbar);

