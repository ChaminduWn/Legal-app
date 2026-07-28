const state = {
  documents: [],
  activeDocument: null,
  sections: [],
  activeSection: null,
};

const documentList = document.querySelector("#document-list");
const sectionList = document.querySelector("#section-list");
const workspaceEmpty = document.querySelector("#workspace-empty");
const workspaceContent = document.querySelector("#workspace-content");
const editorEmpty = document.querySelector("#editor-empty");
const sectionForm = document.querySelector("#section-form");
const metadataDialog = document.querySelector("#metadata-dialog");
const importDialog = document.querySelector("#import-dialog");

function routePart(value) {
  return encodeURIComponent(value);
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "The request could not be completed.");
  }
  return response.json();
}

async function loadDocuments() {
  state.documents = await request("/api/admin/documents");
  document.querySelector("#document-count").textContent =
    `${state.documents.length} documents`;
  renderDocuments();
}

function renderDocuments() {
  const filter = document.querySelector("#document-filter").value.toLowerCase();
  documentList.replaceChildren();
  state.documents
    .filter((item) => `${item.law} ${item.source_file}`.toLowerCase().includes(filter))
    .forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `document-item${state.activeDocument?.source_file === item.source_file ? " active" : ""}`;
      const title = document.createElement("strong");
      title.textContent = item.law;
      const file = document.createElement("small");
      file.textContent = item.source_file;
      const meta = document.createElement("div");
      meta.className = "document-meta";
      const count = document.createElement("small");
      count.textContent = `${item.verified_sections}/${item.section_count} verified`;
      const status = document.createElement("span");
      status.className = `status-chip ${item.status}`;
      status.textContent = item.status.replace("_", " ");
      meta.append(count, status);
      button.append(title, file, meta);
      button.addEventListener("click", () => selectDocument(item));
      documentList.append(button);
    });
}

async function selectDocument(documentRecord) {
  state.activeDocument = documentRecord;
  state.activeSection = null;
  renderDocuments();
  workspaceEmpty.hidden = true;
  workspaceContent.hidden = false;
  editorEmpty.hidden = false;
  sectionForm.hidden = true;

  document.querySelector("#document-filename").textContent = documentRecord.source_file;
  document.querySelector("#document-title").textContent = documentRecord.law;
  document.querySelector("#document-progress").textContent =
    `${documentRecord.verified_sections} of ${documentRecord.section_count} sections verified · ${documentRecord.status.replace("_", " ")}`;

  state.sections = await request(
    `/api/admin/documents/${routePart(documentRecord.source_file)}/sections`,
  );
  document.querySelector("#section-count").textContent = `${state.sections.length} sections`;
  document.querySelector("#pdf-frame").src =
    `/api/admin/documents/${routePart(documentRecord.source_file)}/pdf`;
  renderSections();
}

function renderSections() {
  const filter = document.querySelector("#section-filter").value.toLowerCase();
  sectionList.replaceChildren();
  state.sections
    .filter((item) => `${item.section} ${item.heading || ""}`.toLowerCase().includes(filter))
    .forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `section-item${state.activeSection?.source_id === item.source_id ? " active" : ""}`;
      const label = document.createElement("span");
      label.innerHTML = `<strong>§ ${escapeHtml(item.section)}</strong>`;
      const dot = document.createElement("span");
      dot.className = `review-dot ${item.status}`;
      dot.title = item.status.replace("_", " ");
      button.append(label, dot);
      button.addEventListener("click", () => selectSection(item));
      sectionList.append(button);
    });
}

function selectSection(section) {
  state.activeSection = section;
  renderSections();
  editorEmpty.hidden = true;
  sectionForm.hidden = false;
  document.querySelector("#editing-section").textContent = `Section ${section.section}`;
  document.querySelector("#section-heading").value = section.heading || "";
  document.querySelector("#section-text").value = section.text;
  document.querySelector("#section-status").value = section.status;
  document.querySelector("#section-notes").value = section.notes || "";
  document.querySelector("#section-pages").textContent =
    section.page_start === section.page_end
      ? `Original PDF page ${section.page_start}`
      : `Original PDF pages ${section.page_start}–${section.page_end}`;
  document.querySelector("#pdf-page-label").textContent = `Page ${section.page_start}`;
  document.querySelector("#index-state").textContent =
    section.semantic_indexed ? "Semantic index current" : "Re-index required";
  document.querySelector("#pdf-frame").src =
    `/api/admin/documents/${routePart(state.activeDocument.source_file)}/pdf#page=${section.page_start}`;
}

sectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const update = {
    heading: document.querySelector("#section-heading").value.trim() || null,
    text: document.querySelector("#section-text").value.trim(),
    status: document.querySelector("#section-status").value,
    notes: document.querySelector("#section-notes").value.trim() || null,
  };
  await request(`/api/admin/sections/${routePart(state.activeSection.source_id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  Object.assign(state.activeSection, update, { semantic_indexed: false });
  renderSections();
  selectSection(state.activeSection);
  await loadDocuments();
  showToast("Section review saved. Rebuild the local index when reviews are complete.");
});

document.querySelector("#metadata-button").addEventListener("click", () => {
  const item = state.activeDocument;
  document.querySelector("#official-source-url").value = item.official_source_url || "";
  document.querySelector("#publication-date").value = item.publication_date || "";
  document.querySelector("#effective-date").value = item.effective_date || "";
  document.querySelector("#document-status").value = item.status;
  document.querySelector("#document-notes").value = item.notes || "";
  metadataDialog.showModal();
});

document.querySelector("#metadata-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const update = {
    official_source_url: document.querySelector("#official-source-url").value.trim() || null,
    publication_date: document.querySelector("#publication-date").value.trim() || null,
    effective_date: document.querySelector("#effective-date").value.trim() || null,
    status: document.querySelector("#document-status").value,
    notes: document.querySelector("#document-notes").value.trim() || null,
  };
  await request(`/api/admin/documents/${routePart(state.activeDocument.source_file)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  Object.assign(state.activeDocument, update);
  metadataDialog.close();
  await loadDocuments();
  showToast("Document record saved.");
});

document.querySelector("#import-button").addEventListener("click", () => importDialog.showModal());
document.querySelector("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#import-message");
  message.textContent = "Extracting numbered legal sections…";
  try {
    const imported = await request("/api/admin/documents/upload", {
      method: "POST",
      body: new FormData(form),
    });
    form.reset();
    importDialog.close();
    await loadDocuments();
    await selectDocument(imported);
    showToast("PDF imported. Review it before marking it verified.");
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => document.querySelector(`#${button.dataset.close}`).close());
});
document.querySelector("#document-filter").addEventListener("input", renderDocuments);
document.querySelector("#section-filter").addEventListener("input", renderSections);

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character]);
}

loadDocuments().catch((error) => showToast(error.message));

