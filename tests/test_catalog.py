import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.admin import initialize_admin, save_document_metadata, save_section_review, approve_document, list_documents
from app.models import DocumentMetadataUpdate, DocumentVersionUpdate, SectionReviewUpdate
from app.rag.schema import LegalChunk
from app.rag.store import replace_document_chunks, connect, search, initialize, _semantic_search
from app.storage import store_file, register_asset, resolve_asset
from scripts.maintain_library import audit


def add(database, name):
    initialize_admin(database)
    replace_document_chunks(database, name, [LegalChunk(name+'-1','Test Law','1',None,'Theft of movable property is covered by this provision.',name,1,1)])


def version(database, name, start, end=None, **extra):
    save_document_metadata(database,name,DocumentMetadataUpdate(status='in_review',
        official_source_url='https://example.gov/test.pdf',publication_date='2020-01-01',effective_date=start,
        version=DocumentVersionUpdate(instrument_id='test-law',instrument_title='Test Law',edition_year=int(start[:4]),
            version_kind='original',valid_from=start,valid_to=end,**extra)))
    save_section_review(database,name+'-1',SectionReviewUpdate(text='Theft of movable property is covered by this provision.',status='verified'))
    approve_document(database,name,extraction_complete=True,authority_checked=True)


def test_historical_boundary_and_undated_exclusion(tmp_path):
    db=tmp_path/'test.db'
    for name in ['old.pdf','new.pdf','undated.pdf']: add(db,name)
    version(db,'old.pdf','2020-01-01','2025-01-01')
    save_document_metadata(db,'new.pdf',DocumentMetadataUpdate(status='in_review',version=DocumentVersionUpdate(
        instrument_id='test-law',instrument_title='Test Law',edition_year=2025,version_kind='consolidated',
        valid_from='2025-01-01',supersedes_source='old.pdf')))
    with connect(db) as c:
        c.execute("UPDATE document_metadata SET status='verified'")
    assert [s.source_file for s in search(db,'theft',as_of='2024-12-31')] == ['old.pdf']
    current=search(db,'theft',as_of='2025-01-01')
    assert [s.source_file for s in current] == ['new.pdf']
    assert current[0].edition_year == 2025
    assert not search(db,'theft',as_of='2019-01-01')


def test_overlap_rejected_and_metadata_transaction_rolls_back(tmp_path):
    db=tmp_path/'test.db'
    add(db,'old.pdf'); add(db,'new.pdf')
    version(db,'old.pdf','2020-01-01')
    with pytest.raises(ValueError,match='overlapping'):
        version(db,'new.pdf','2025-01-01')
    with connect(db) as c:
        assert not c.execute("SELECT 1 FROM document_versions WHERE source_file='new.pdf'").fetchone()
        assert not c.execute("SELECT 1 FROM document_metadata WHERE source_file='new.pdf'").fetchone()


def test_amendments_cannot_replace_complete_law(tmp_path):
    db=tmp_path/'test.db'
    add(db,'old.pdf'); add(db,'amend.pdf')
    version(db,'old.pdf','2020-01-01','2025-01-01')
    with connect(db) as c, pytest.raises(ValueError,match='complete consolidated'):
        from app.catalog import save_version
        save_version(c,'amend.pdf',DocumentVersionUpdate(instrument_id='test-law',instrument_title='Test Law',edition_year=2025,
            version_kind='amendment',valid_from='2025-01-01',supersedes_source='old.pdf'))


def test_enforced_document_and_section_foreign_keys(tmp_path):
    db=tmp_path/'test.db'; add(db,'one.pdf')
    with connect(db) as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM document_records WHERE source_file='one.pdf'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO legal_chunks VALUES ('bad','Law','1',NULL,'Text','missing.pdf',1,1)")


def test_reimport_invalidates_publication_and_old_reviews(tmp_path):
    db=tmp_path/'test.db'; add(db,'one.pdf'); version(db,'one.pdf','2020-01-01')
    add(db,'one.pdf')
    with connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM section_reviews').fetchone()[0] == 0
        assert c.execute('SELECT status FROM document_metadata').fetchone()[0] == 'in_review'
    assert not search(db,'theft')


def test_content_addressed_storage_and_traversal(tmp_path):
    db=tmp_path/'test.db';add(db,'one.pdf')
    path=tmp_path/'one.pdf';path.write_bytes(b'%PDF-original')
    root=tmp_path/'private';asset=store_file(root,path)
    assert store_file(root,path) == asset
    with connect(db) as c: register_asset(c,'one.pdf',0,asset)
    assert resolve_asset(db,root,'one.pdf').read_bytes() == b'%PDF-original'
    with connect(db) as c: c.execute("UPDATE document_assets SET storage_key='../one.pdf'")
    assert resolve_asset(db,root,'one.pdf') is None


def test_amendment_relationship_has_real_target(tmp_path):
    db=tmp_path/'test.db';add(db,'base.pdf');add(db,'amend.pdf');version(db,'base.pdf','2020-01-01')
    save_document_metadata(db,'amend.pdf',DocumentMetadataUpdate(status='in_review',version=DocumentVersionUpdate(
        instrument_id='amending-act',instrument_title='Amending Act',edition_year=2025,version_kind='amendment',
        valid_from='2025-01-01',target_instrument_id='test-law',relation_type='amends',relationship_evidence='Section 2 amends Test Law.')))
    record=next(d for d in list_documents(db) if d.source_file=='amend.pdf')
    assert record.target_instrument_id == 'test-law'
    with connect(db) as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM legal_instruments WHERE instrument_id='test-law'")


def test_schema_upgrade_preserves_reviews(tmp_path):
    db=tmp_path/'legacy.db'
    with sqlite3.connect(db) as c:
        c.executescript("""CREATE TABLE legal_chunks(source_id TEXT PRIMARY KEY,law TEXT,section TEXT,heading TEXT,text TEXT,source_file TEXT,page_start INTEGER,page_end INTEGER);
        INSERT INTO legal_chunks VALUES ('one','Law','1',NULL,'Text','one.pdf',1,1);
        CREATE TABLE section_reviews(source_id TEXT PRIMARY KEY REFERENCES legal_chunks(source_id),original_heading TEXT,original_text TEXT,status TEXT,notes TEXT,updated_at TEXT);
        INSERT INTO section_reviews VALUES('one',NULL,'Original','verified',NULL,'2025-01-01');""")
    initialize_admin(db); initialize_admin(db)
    with connect(db) as c:
        assert c.execute('PRAGMA foreign_key_list(legal_chunks)').fetchall()
        assert c.execute('SELECT original_text FROM section_reviews').fetchone()[0]=='Original'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_semantic_retrieval_uses_same_temporal_filter(tmp_path,monkeypatch):
    import numpy as np
    db=tmp_path/'test.db';add(db,'old.pdf');add(db,'new.pdf')
    version(db,'old.pdf','2020-01-01','2025-01-01');version(db,'new.pdf','2025-01-01')
    monkeypatch.setattr('app.rag.store.get_settings',lambda:SimpleNamespace(legal_ai_embedding_model='test',environment='development'))
    monkeypatch.setattr('app.rag.store._embedding_model',lambda:SimpleNamespace(query_embed=lambda q:iter([[1.,0.]])))
    with connect(db) as c:
        for name in ['old.pdf','new.pdf']:
            c.execute('INSERT INTO legal_embeddings VALUES (?,?,?,?)',(name+'-1','test',2,np.array([1.,0.],dtype=np.float32).tobytes()))
    assert [s.source_file for s in _semantic_search(db,'theft',6,'2024-01-01')]==['old.pdf']
    assert [s.source_file for s in _semantic_search(db,'theft',6,'2025-01-01')]==['new.pdf']


def test_relocation_verifies_originals_and_preserves_config_secrets(tmp_path):
    from scripts.relocate_data import relocate, update_config
    db=tmp_path/'test.db';add(db,'one.pdf')
    root=tmp_path/'storage';original=tmp_path/'one.pdf';original.write_bytes(b'%PDF-hello')
    asset=store_file(root,original)
    with connect(db) as c: register_asset(c,'one.pdf',0,asset)
    new_db,new_storage,count=relocate(db,root,tmp_path/'separate')
    assert count==1 and db.exists() and original.exists()
    assert resolve_asset(new_db,new_storage,'one.pdf').read_bytes()==b'%PDF-hello'
    env=tmp_path/'.env';env.write_text('GEMINI_API_KEY=do-not-touch\nLEGAL_AI_DATABASE=old.db\n')
    update_config(env,new_db,new_storage)
    assert 'GEMINI_API_KEY=do-not-touch' in env.read_text()
    with pytest.raises(ValueError,match='overwrite'): relocate(db,root,tmp_path/'separate')


def test_reset_cannot_erase_existing_library(tmp_path):
    from app.rag.store import replace_chunks
    db=tmp_path/'test.db';add(db,'one.pdf')
    with pytest.raises(ValueError,match='nonempty'): replace_chunks(db,[])
    assert audit(db)['sections']==1


def test_production_settings_reject_defaults(tmp_path):
    from app.config import Settings
    with pytest.raises(ValueError,match='admin password'):
        Settings(_env_file=None,environment='production')
    with pytest.raises(ValueError,match='outside the code'):
        Settings(_env_file=None,environment='production',admin_password='x'*20,admin_session_secret='s'*40,
            legal_ai_database=Path.cwd()/'data/test.db',legal_ai_storage_root=Path.cwd()/'data/storage')


def test_production_excludes_undated_legacy_material(tmp_path,monkeypatch):
    db=tmp_path/'test.db';add(db,'legacy.pdf');add(db,'dated.pdf')
    version(db,'dated.pdf','2020-01-01')
    with connect(db) as c:
        c.execute("INSERT INTO document_metadata(source_file,status) VALUES ('legacy.pdf','verified')")
    monkeypatch.setattr('app.rag.store.get_settings',lambda:SimpleNamespace(environment='production',legal_ai_embedding_model='test'))
    assert [s.source_file for s in search(db,'theft')] == ['dated.pdf']


def test_import_registration_rolls_back_on_asset_failure(tmp_path,monkeypatch):
    from app.main import commit_import
    from app.config import Settings
    db=tmp_path/'test.db';initialize_admin(db)
    settings=Settings(_env_file=None,legal_ai_database=db,legal_ai_storage_root=tmp_path/'storage')
    chunks=[LegalChunk('new-1','New Law','1',None,'New text with enough information.','new.pdf',1,1)]
    def fail(*args): raise RuntimeError('storage registration failed')
    monkeypatch.setattr('app.main.register_asset',fail)
    with pytest.raises(RuntimeError):
        commit_import(settings,'new.pdf',chunks,'act','pdf_text',None,[],[(0,{})])
    with connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM document_records').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM document_metadata').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]==0


def test_production_publication_requires_edition(tmp_path,monkeypatch):
    db=tmp_path/'test.db';add(db,'one.pdf')
    save_document_metadata(db,'one.pdf',DocumentMetadataUpdate(status='in_review',document_type='act',
        official_source_url='https://example.gov/one.pdf',publication_date='2020-01-01',effective_date='2020-01-01'))
    save_section_review(db,'one.pdf-1',SectionReviewUpdate(text='Theft of movable property is covered by this provision.',status='verified'))
    monkeypatch.setattr('app.config.get_settings',lambda:SimpleNamespace(environment='production'))
    with pytest.raises(ValueError,match='edition year'):
        approve_document(db,'one.pdf',extraction_complete=True,authority_checked=True)
