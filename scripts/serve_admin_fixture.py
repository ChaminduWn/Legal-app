"""Local browser-test server. Uses synthetic documents and an isolated temporary database."""
import argparse
from pathlib import Path
import tempfile

import uvicorn
from pypdf import PdfWriter

from app.config import Settings
from app.models import DocumentMetadataUpdate, DocumentVersionUpdate, SectionReviewUpdate
from app.rag.schema import LegalChunk
from app.rag.store import connect, replace_chunks
from app.storage import store_file, register_asset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    workspace=Path(__file__).resolve().parent.parent
    (workspace/'tmp').mkdir(exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='admin-ui-',dir=workspace/'tmp'))
    settings=Settings(_env_file=None,legal_ai_database=root/'library.sqlite3',legal_ai_storage_root=root/'originals',
        gemini_api_key=None,admin_username='reviewer',admin_password='reviewer-test-password',admin_session_secret='fixture-secret-for-local-browser-tests')
    # Exercise production publication policy while keeping every fixture path inside the test workspace.
    settings=settings.model_copy(update={'environment':'production'})
    import app.config
    import app.main
    app.config.get_settings=lambda:settings
    app.main.get_settings=lambda:settings
    from app.admin import initialize_admin, save_document_metadata, save_section_review
    initialize_admin(settings.legal_ai_database)
    descriptions=[('a.pdf','Review example',3),('b.pdf','Another review example',2),('flagged.pdf','Flagged extraction example',1),
        ('published.pdf','Published example',1),('collection/සිංහල.pdf','සිංහල review example',1)]
    descriptions += [(f'sample-{i:03d}.pdf',f'Library sample {i:03d}',1) for i in range(50)]
    chunks=[]
    for name,title,count in descriptions:
        for page in range(1,count+1):
            chunks.append(LegalChunk(name+'-'+str(page),title,'Page '+str(page),f'Test passage {page}',
                f'Synthetic review fixture passage {page}. This text exists only for interface testing and is not legal information.',name,page,page))
    replace_chunks(settings.legal_ai_database,chunks)
    writer=PdfWriter()
    for _ in range(3): writer.add_blank_page(width=595,height=842)
    original=root/'fixture.pdf'
    with original.open('wb') as stream: writer.write(stream)
    asset=store_file(settings.legal_ai_storage_root,original)
    with connect(settings.legal_ai_database) as c:
        c.execute('INSERT INTO document_records VALUES (?,?)',('empty.pdf','Original awaiting extraction'))
        for name in [d[0] for d in descriptions]+['empty.pdf']:
            register_asset(c,name,0,asset)
            c.execute("INSERT INTO document_metadata(source_file,status,updated_at,document_type) VALUES (?,'in_review','2026-10-28','act')",(name,))
        c.execute("UPDATE document_metadata SET extraction_warnings=? WHERE source_file='flagged.pdf'",('["Pages still blank/unreadable after OCR: 2,3"]',))
        c.execute("UPDATE document_metadata SET status='superseded' WHERE source_file='sample-049.pdf'")
        c.execute("INSERT INTO document_topics VALUES ('a.pdf','criminal')")
        c.execute("INSERT INTO document_topics VALUES ('b.pdf','civil')")
    save_section_review(settings.legal_ai_database,'a.pdf-3',SectionReviewUpdate(text=chunks[2].text,status='verified'))
    save_document_metadata(settings.legal_ai_database,'b.pdf',DocumentMetadataUpdate(status='in_review',
        version=DocumentVersionUpdate(instrument_id='another-law',instrument_title='Another law',edition_year=2024,version_kind='original',valid_from='2024-02-01')))
    save_document_metadata(settings.legal_ai_database,'published.pdf',DocumentMetadataUpdate(status='in_review',official_source_url='https://example.gov/published.pdf',publication_date='2024-01-01',effective_date='2024-02-01',
        version=DocumentVersionUpdate(instrument_id='published-law',instrument_title='Published law',edition_year=2024,version_kind='original',valid_from='2024-02-01')))
    save_section_review(settings.legal_ai_database,'published.pdf-1',SectionReviewUpdate(text=chunks[6].text,status='verified'))
    with connect(settings.legal_ai_database) as c: c.execute("UPDATE document_metadata SET status='verified' WHERE source_file='published.pdf'")
    print('Isolated UI fixture ready: '+str(root),flush=True)
    uvicorn.run(app.main.app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__': main()
