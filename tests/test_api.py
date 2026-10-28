import pytest
from app.rag.schema import LegalChunk
from app.rag.store import replace_chunks, connect

@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    database = tmp_path / "api.sqlite3"
    settings = Settings(legal_ai_database=database, legal_ai_storage_root=tmp_path / "storage", gemini_api_key=None)
    replace_chunks(database, [LegalChunk("test-hurt", "Penal Code", "314", "Hurt", "Whoever causes bodily pain, injury or infirmity causes hurt. A person using criminal force commits assault.", "test.pdf", 1, 1)])
    with connect(database) as connection:
        connection.execute("INSERT INTO document_metadata(source_file,status) VALUES ('test.pdf','verified')")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return settings

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_health_and_local_search() -> None:
    with TestClient(app) as client:
        frontend = client.get("/")
        assert frontend.status_code == 200
        assert "Neethi" in frontend.text
        evaluation = client.get("/evaluation")
        assert evaluation.status_code == 200
        assert "Retrieval evaluation" in evaluation.text

        health = client.get("/health")
        assert health.status_code == 200

        response = client.post(
            "/api/search",
            json={"query": "My neighbour hit me and caused an injury", "limit": 6},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["sources"]
        assert all(source["source_id"] for source in payload["sources"])

        preparation = client.post(
            "/api/prepare",
            json={"message": "My phone was taken from my bag"},
        )
        assert preparation.status_code == 200
        prepared = preparation.json()
        assert prepared["detected_domain"] == "property_offence"
        assert prepared["follow_up_questions"]


def test_chat_without_key_returns_retrieved_citations(monkeypatch, isolated_database) -> None:
    settings = isolated_database
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"message": "My neighbour hit me and caused an injury"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["grounded"] is True
        assert payload["legal_references"]


def test_admin_import_review_publish_flow(tmp_path, monkeypatch, isolated_database):
    monkeypatch.setattr("app.main.WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr("app.main.extract_pdf", lambda path, title: [
        LegalChunk("upload-1", title, "1", "Theft", "Whoever dishonestly takes movable property commits theft.", path.name, 1, 1)])
    approval = {"extraction_complete": True, "authority_checked": True}
    with TestClient(app) as client:
        assert client.get("/api/admin/documents").status_code == 401
        assert client.post("/admin/login", data={"username": isolated_database.admin_username,
            "password": isolated_database.admin_password}, follow_redirects=False).status_code == 303
        response = client.post("/api/admin/documents/upload", data={"title": "Test Act", "document_type": "act"},
            files={"document": ("upload.pdf", b"%PDF-1.4 test fixture", "application/pdf")})
        assert response.status_code == 200
        assert response.json()["status"] == "in_review"
        route = "/api/admin/documents/upload.pdf"
        assert client.get(route + "/pdf").status_code == 200
        assert client.post(route + "/approve", json=approval).status_code == 409
        assert client.put(route, json={"status": "verified"}).status_code == 409
        review = {"text": "Whoever dishonestly takes movable property commits theft.", "status": "verified"}
        assert client.put("/api/admin/sections/upload-1", json=review).status_code == 200
        assert client.post(route + "/approve", json={}).status_code == 409
        assert client.post(route + "/approve", json=approval).status_code == 200
        assert client.put(route, json={"status": "in_review", "document_type": "act",
            "official_source_url": "https://example.gov/act.pdf", "publication_date": "2024-01-01",
            "effective_date": "2024-02-01"}).status_code == 200
        assert client.post(route + "/approve", json={}).status_code == 409
        assert client.post(route + "/approve", json=approval).status_code == 200
        def uploaded_is_searchable():
            return any(item["source_id"] == "upload-1" for item in client.post("/api/search",
                json={"query": "theft"}).json()["sources"])
        assert uploaded_is_searchable()
        review["text"] += " Reviewed correction."
        assert client.put("/api/admin/sections/upload-1", json=review).status_code == 200
        assert not uploaded_is_searchable()


def test_admin_review_page_loads_fresh_assets(isolated_database):
    with TestClient(app) as client:
        client.post("/admin/login", data={
            "username": isolated_database.admin_username,
            "password": isolated_database.admin_password,
        }, follow_redirects=False)
        page = client.get("/admin/documents")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        for filename in ("documents.js", "documents.css"):
            assert f"/static/{filename}?v=20261028-guided-review" in page.text
            asset = client.get(f"/static/{filename}?v=20261028-guided-review")
            assert asset.status_code == 200
            assert asset.headers["cache-control"] == "no-store"
        assert client.get("/api/admin/documents").headers["cache-control"] == "no-store"


def test_unicode_pdf_with_pending_extraction_is_visible(isolated_database,tmp_path):
    from urllib.parse import quote
    from app.storage import store_file, register_asset
    source='collection/නීතිය.pdf'
    original=tmp_path/'original.pdf';original.write_bytes(b'%PDF-original')
    asset=store_file(isolated_database.legal_ai_storage_root,original)
    with connect(isolated_database.legal_ai_database) as connection:
        connection.execute('INSERT INTO document_records VALUES (?,?)',(source,'Pending scan'))
        register_asset(connection,source,0,asset)
    with TestClient(app) as client:
        client.post('/admin/login',data={'username':isolated_database.admin_username,'password':isolated_database.admin_password})
        base='/api/admin/documents/'+quote(source,safe='')
        assert client.get(base+'/sections').json()==[]
        pdf=client.get(base+'/pdf')
        assert pdf.status_code==200 and pdf.content==b'%PDF-original'
        assert "filename*=utf-8" in pdf.headers['content-disposition']
        assert any(d['source_file']==source and d['section_count']==0 for d in client.get('/api/admin/documents').json())


def test_admin_library_exposes_publication_requirements_not_credentials(isolated_database):
    with TestClient(app) as client:
        client.post('/admin/login',data={'username':isolated_database.admin_username,'password':isolated_database.admin_password})
        response=client.get('/api/admin/library')
        assert response.status_code==200
        assert response.json()['publication_policy']=={'mode':'development','require_source_details':False,'require_edition':False}
        assert 'admin_password' not in response.text
        assert 'session_secret' not in response.text
