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


def test_admin_change_password_flow(isolated_database):
    with TestClient(app) as client:
        # Unauthenticated change password fails
        assert client.post('/api/admin/change-password', json={
            'current_password': 'test',
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }).status_code == 401

        # Sign in
        login_res = client.post('/admin/login', data={
            'username': isolated_database.admin_username,
            'password': isolated_database.admin_password
        }, follow_redirects=False)
        assert login_res.status_code == 303

        # Mismatched passwords
        mismatch_res = client.post('/api/admin/change-password', json={
            'current_password': isolated_database.admin_password,
            'new_password': 'newpassword123',
            'confirm_password': 'differentpassword123'
        })
        assert mismatch_res.status_code == 400

        # Wrong current password
        wrong_res = client.post('/api/admin/change-password', json={
            'current_password': 'incorrect-current-pass',
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        })
        assert wrong_res.status_code == 400

        # Successful change
        success_res = client.post('/api/admin/change-password', json={
            'current_password': isolated_database.admin_password,
            'new_password': 'brand-new-secure-password-2026',
            'confirm_password': 'brand-new-secure-password-2026'
        })
        assert success_res.status_code == 200
        assert success_res.json()['status'] == 'ok'

        # Logout
        client.post('/admin/logout')

        # Old password must now fail
        old_login = client.post('/admin/login', data={
            'username': isolated_database.admin_username,
            'password': isolated_database.admin_password
        }, follow_redirects=False)
        assert old_login.status_code == 303
        assert 'error=1' in old_login.headers.get('location', '')

        # New password must succeed
        new_login = client.post('/admin/login', data={
            'username': isolated_database.admin_username,
            'password': 'brand-new-secure-password-2026'
        }, follow_redirects=False)
        assert new_login.status_code == 303
        assert '/admin/documents' in new_login.headers.get('location', '')


def test_site_content_dynamic_endpoints(isolated_database):
    with TestClient(app) as client:
        # Public endpoint
        initial = client.get('/api/site-content')
        assert initial.status_code == 200
        data = initial.json()
        assert 'en' in data and 'si' in data
        assert data['en']['appName'] == 'Neethi'

        # Admin update requires authentication
        assert client.put('/api/admin/site-content', json={'en': {}, 'si': {}}).status_code == 401

        # Sign in
        client.post('/admin/login', data={
            'username': isolated_database.admin_username,
            'password': isolated_database.admin_password
        })

        # Update content
        data['en']['appName'] = 'Custom Sri Lankan Legal AI'
        data['en']['headline'] = 'New Dynamic Headline'
        update_res = client.put('/api/admin/site-content', json=data)
        assert update_res.status_code == 200
        assert update_res.json()['status'] == 'ok'

        # Verify public endpoint reflects updated content
        updated = client.get('/api/site-content').json()
        assert updated['en']['appName'] == 'Custom Sri Lankan Legal AI'
        assert updated['en']['headline'] == 'New Dynamic Headline'

        # Reset to defaults
        reset_res = client.post('/api/admin/site-content/reset')
        assert reset_res.status_code == 200
        reset_data = client.get('/api/site-content').json()
        assert reset_data['en']['appName'] == 'Neethi'


def test_admin_login_redirect_when_already_authenticated(isolated_database):
    with TestClient(app) as client:
        # When not signed in, /admin/login returns login HTML
        unauthed = client.get('/admin/login')
        assert unauthed.status_code == 200
        assert 'Admin sign in' in unauthed.text

        # Sign in
        client.post('/admin/login', data={
            'username': isolated_database.admin_username,
            'password': isolated_database.admin_password
        })

        # When already signed in, /admin/login redirects to /admin/documents
        authed = client.get('/admin/login', follow_redirects=False)
        assert authed.status_code == 303
        assert authed.headers.get('location') == '/admin/documents'
