# Admin browser checks

The browser test starts a local FastAPI fixture with synthetic documents in a new directory under `tmp`. It does not use the real library, `.env`, Gemini, or external PDF storage. Its fixture server is stopped when the test exits.

Run Python tests with `python -m pytest`. For the optional browser test, use Node.js with Playwright available (`npm install --no-save playwright`), then run:

```
node tests/admin_review.browser.cjs
```

Windows uses installed Microsoft Edge. On other platforms install the Playwright Chromium browser (`npx playwright install chromium`). Set `NEETHI_BROWSER_CHANNEL` to override the browser channel, or `NEETHI_PYTHON` to use a Python interpreter other than `.venv`. The interpreter needs the project's dependencies. Screenshots and a result summary are written to `tmp/admin-ui-results`.

The checks cover queue pagination and filtering, unsaved-change guards, preserving edits on failed saves, single-passage verification, Save & next, production publication requirements, law selection by name, explicit publication, and mobile access to original PDFs.
