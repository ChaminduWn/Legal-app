# Using the review workspace

Open `/admin/documents` after signing in. The overview shows documents awaiting review, originals with extraction issues, documents with all text checked, and published sources.

## Work through the queue

- Filter by review status, extraction issues, missing source details, or legal topic. Sort by attention needed, progress, title, or edition year. The list is paginated.
- Select a document or choose **Start next review**. The latter can resume the last position in the current browser tab, provided the document still matches the queue.
- The first passage still requiring review opens automatically. Verified passages can be found using **All passages** or **Verified** in the passage filter.
- Compare the text with the PDF. **Open original** opens it in a separate tab at the current page; the embedded preview is also available on smaller screens. Extraction warnings include links to flagged pages where page numbers are known.

## Save and advance

- **Save** saves this passage and keeps it open.
- **Save & next** preserves the selected review status and opens the next passage needing review.
- **Verify & next** saves this passage as verified and opens the next passage needing review. It does not verify other passages or publish the document.
- Failed saves preserve the edited text in the current page. Unsaved changes are indicated and navigation asks before discarding them. This is not automatic saving: save successfully before closing the browser.

## Complete source details

Use **Check source details** for the official link, publication and effective dates, document type, topics, and notes. Save partial source details as a draft when information is still unknown; do not invent a date.

The edition and amendment section lets you choose laws by name or add a law name. Reference IDs are generated internally. Existing edition dates and amendment relationships continue to be validated by the server.

Development mode explains that source details are optional for publishing in that mode. Production mode lists the required source information and dated edition in the publication checklist. The server enforces the final requirements.

## Publish separately

After every extracted passage has been verified, check source details and compare all original pages, including any pages omitted by extraction. Confirm both publication attestations, then choose **Approve & publish**. Until then, new material stays outside public search.

Editing a published passage withdraws that document for another review. After a passage save or source-detail change, the publication confirmations must be checked again.
