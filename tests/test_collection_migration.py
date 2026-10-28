from pathlib import Path
import sqlite3

import pytest

from app.admin import list_documents
from app.rag.store import connect
from scripts.migrate_collections import inventory, initialize_inventory, intake_one, verify_removal


def setup_collection(tmp_path):
    workspace=tmp_path/'project';workspace.mkdir()
    archive=tmp_path/'outside'/'collections';storage=tmp_path/'outside'/'originals'
    database=workspace/'test.db';initialize_inventory(database)
    return workspace,archive,storage,database


def test_import_deduplicates_and_links_every_original(tmp_path,monkeypatch):
    workspace,archive,storage,db=setup_collection(tmp_path)
    for folder in ('Civil Law','Criminal law'):
        path=workspace/folder/'law.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-identical')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: (['A legal text describing the relevant statutory provisions and procedures.'],[]))
    records=inventory(workspace)
    results=[intake_one(workspace,archive,storage,db,r) for r in records]
    assert [r['state'] for r in results]==['imported','duplicate_indexed']
    assert len(list_documents(db))==1
    with connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM collection_files').fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]==1
        assert c.execute('SELECT status FROM document_metadata').fetchone()[0]=='in_review'
    verify_removal(workspace,archive,storage,db,records)
    for r in records: assert (archive/r['origin_path']).read_bytes()==b'%PDF-identical'


def test_scanned_original_is_registered_without_invented_text(tmp_path,monkeypatch):
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'scan.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-scan')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: (['',''],[]))
    record=inventory(workspace)[0]
    result=intake_one(workspace,archive,storage,db,record)
    assert result['state']=='ocr_pending'
    doc=list_documents(db)[0]
    assert doc.section_count==0 and doc.extraction_warnings
    verify_removal(workspace,archive,storage,db,[record])


def test_removal_refuses_missing_managed_copy_or_changed_source(tmp_path,monkeypatch):
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'law.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-original')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: (['Usable text describing a legal provision and procedure for reviewing a law.'],[]))
    record=inventory(workspace)[0];intake_one(workspace,archive,storage,db,record)
    path.write_bytes(b'%PDF-changed')
    with pytest.raises(ValueError,match='changed'): verify_removal(workspace,archive,storage,db,[record])
    path.write_bytes(b'%PDF-original')
    with connect(db) as c: key=c.execute('SELECT storage_key FROM document_assets').fetchone()[0]
    (storage/key).unlink()
    with pytest.raises(FileNotFoundError): verify_removal(workspace,archive,storage,db,[record])


def test_unreadable_pdf_is_retained_and_failure_recorded(tmp_path,monkeypatch):
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'broken.pdf';path.parent.mkdir();path.write_bytes(b'broken-pdf')
    def broken(path): raise ValueError('Cannot decode PDF')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',broken)
    result=intake_one(workspace,archive,storage,db,inventory(workspace)[0])
    assert result['state']=='extraction_failed'
    assert (archive/'Civil Law'/'broken.pdf').exists()
    assert list_documents(db)[0].section_count==0


def test_rerun_preserves_pending_ocr_and_does_not_reextract(tmp_path,monkeypatch):
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'scan.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-scan')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: ([''],[]))
    record=inventory(workspace)[0];intake_one(workspace,archive,storage,db,record)
    def should_not_extract(path): raise AssertionError('Duplicate extraction')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',should_not_extract)
    result=intake_one(workspace,archive,storage,db,record)
    assert result['state']=='ocr_pending'


def test_ocr_imports_missing_pages_without_publishing(tmp_path,monkeypatch):
    from scripts.migrate_collections import ocr_pending
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'scan.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-scan')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: ([''],[]))
    record=inventory(workspace)[0];intake_one(workspace,archive,storage,db,record)
    tessdata=workspace/'data/ocr/tessdata';tessdata.mkdir(parents=True);(tessdata/'sin.traineddata').write_bytes(b'test')
    monkeypatch.setattr('scripts.migrate_collections.shutil.which',lambda tool:'/tool/'+tool)
    monkeypatch.setattr('scripts.migrate_collections.ocr_page',lambda pdf,n,work,data,dpi:(n,'Recovered legal text that must be verified against the original PDF before publishing.'))
    ocr_pending(db,storage,workspace,workers=1)
    with connect(db) as c:
        assert c.execute('SELECT state FROM collection_files').fetchone()[0]=='ocr_imported'
        assert c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]==1
        assert c.execute('SELECT status FROM document_metadata').fetchone()[0]=='in_review'
    assert list_documents(db)[0].extraction_method=='pdf_ocr'


def test_one_ocr_page_failure_does_not_discard_other_pages(tmp_path,monkeypatch):
    from scripts.migrate_collections import ocr_pending
    workspace,archive,storage,db=setup_collection(tmp_path)
    path=workspace/'Civil Law'/'scan.pdf';path.parent.mkdir();path.write_bytes(b'%PDF-scan')
    monkeypatch.setattr('scripts.migrate_collections.extract_pages',lambda path: (['',''],[]))
    intake_one(workspace,archive,storage,db,inventory(workspace)[0])
    tessdata=workspace/'data/ocr/tessdata';tessdata.mkdir(parents=True);(tessdata/'sin.traineddata').write_bytes(b'test')
    monkeypatch.setattr('scripts.migrate_collections.shutil.which',lambda tool:'/tool/'+tool)
    def recover(pdf,n,work,data,dpi):
        if n==2: raise RuntimeError('Unreadable page')
        return n,'Recovered legal text that must be checked against the original document before publication.'
    monkeypatch.setattr('scripts.migrate_collections.ocr_page',recover)
    ocr_pending(db,storage,workspace,workers=1)
    with connect(db) as c:
        assert c.execute('SELECT state FROM collection_files').fetchone()[0]=='ocr_partial'
        assert c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]==1
        assert 'Page 2: OCR failed' in c.execute('SELECT warnings_json FROM collection_files').fetchone()[0]
