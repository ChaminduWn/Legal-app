"""Resumable, verified intake of legacy PDF collections; never publishes extracted text."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

from pypdf import PdfReader

from app.admin import initialize_admin, register_import
from app.config import get_settings
from app.rag.schema import LegalChunk
from app.rag.store import connect, replace_document_chunks
from app.storage import store_file, register_asset
from scripts.import_sinhala_collections import clean_text, split_page, text_score, law_name
from scripts.import_step_001 import IMPORTS, EXCLUDED

logging.getLogger('pypdf').setLevel(logging.ERROR)

ROOT_NAMES = ('Civil Law', 'Criminal law', 'පනත්', 'step 001 pdf folder')


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(workspace):
    records=[]
    for name in ROOT_NAMES:
        root=workspace/name
        if not root.exists(): continue
        if root.is_symlink() or not root.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('Unsafe collection root: '+str(root))
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
                raise ValueError('Linked directories/files cannot be automatically removed: '+str(path))
            if path.is_file():
                records.append(dict(origin_path=path.relative_to(workspace).as_posix(),sha256=digest_file(path),
                    byte_size=path.stat().st_size,is_pdf=path.suffix.lower()=='.pdf'))
    return records


def initialize_inventory(database):
    initialize_admin(database)
    with connect(database) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS collection_files (
            origin_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
            source_file TEXT REFERENCES document_records(source_file), storage_key TEXT,
            state TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0, extracted_pages INTEGER NOT NULL DEFAULT 0,
            warnings_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)""")
        c.execute('CREATE INDEX IF NOT EXISTS collection_file_hash ON collection_files(sha256)')


def extract_pages(path):
    reader=PdfReader(str(path),strict=False)
    pages=[]
    warnings=[]
    for number,page in enumerate(reader.pages,1):
        try: pages.append(clean_text(page.extract_text() or ''))
        except Exception as exc:
            pages.append('')
            warnings.append(f'Page {number}: text extraction failed ({type(exc).__name__}).')
    return pages,warnings


def page_chunks(pages,source_file,title,digest):
    chunks=[]
    for number,text in enumerate(pages,1):
        for part,body in enumerate(split_page(text),1):
            chunks.append(LegalChunk(f'collection-{digest}-p{number}-{part}',title,
                f'Page {number}' if part==1 else f'Page {number}.{part}',body.splitlines()[0][:160],
                body,source_file,number,number))
    return chunks


def copy_verified(original,target,digest):
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        temporary=target.with_name(target.name+'.copying')
        shutil.copyfile(original,temporary)
        if digest_file(temporary)!=digest:
            raise ValueError('Archive checksum mismatch: '+str(original))
        os.replace(temporary,target)
    if digest_file(target)!=digest:
        raise ValueError('Existing archive differs from original; refusing overwrite: '+str(target))


def archive_path(root,relative):
    target=(root/relative).resolve()
    if not target.is_relative_to(root.resolve()): raise ValueError('Unsafe archive path.')
    return target


def intake_one(workspace,archive,storage,database,record):
    name=record['origin_path']; original=workspace/name
    archived=archive_path(archive,name)
    copy_verified(original,archived,record['sha256'])
    if not record['is_pdf']:
        return {**record,'state':'archived_non_pdf'}
    with connect(database) as c:
        previous=c.execute('SELECT * FROM collection_files WHERE origin_path=?',(name,)).fetchone()
        if previous and previous['sha256']!=record['sha256']:
            raise ValueError('Source changed since inventory: '+name)
        existing=c.execute("""SELECT a.source_file,a.storage_key,COUNT(ch.source_id) AS chunks
            FROM document_assets a LEFT JOIN legal_chunks ch ON ch.source_file=a.source_file
            WHERE a.sha256=? AND a.page_number=0 GROUP BY a.source_file ORDER BY chunks DESC LIMIT 1""",
            (record['sha256'],)).fetchone()
    asset=store_file(storage,original)
    source_file=existing['source_file'] if existing else name
    if previous and previous['source_file'] and previous['storage_key'] == asset['storage_key']:
        return {**record,'source_file':previous['source_file'],'state':previous['state'],'pages':previous['pages'],'extracted_pages':previous['extracted_pages']}
    if existing and existing['chunks']:
        state='already_indexed' if existing['source_file']==name else 'duplicate_indexed'
        with connect(database) as c:
            if previous and previous['state'] in ('ocr_pending','ocr_failed'):
                state=previous['state']
            elif previous and previous['state']=='ocr_imported': state='ocr_imported'
            c.execute("""INSERT INTO collection_files(origin_path,sha256,byte_size,source_file,storage_key,state,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(origin_path) DO UPDATE SET updated_at=excluded.updated_at""",
                (name,record['sha256'],record['byte_size'],source_file,asset['storage_key'],state,datetime.now(UTC).isoformat()))
        return {**record,'source_file':source_file,'state':state}
    pages=[]; warnings=[]
    title = IMPORTS.get(Path(name).name, (Path(name).stem, ''))[0] if name.startswith('step 001 pdf folder/') else law_name(Path(name))
    if name.startswith('step 001 pdf folder/') and Path(name).name in EXCLUDED:
        warnings.append('Reference only: '+EXCLUDED[Path(name).name][1])
    try:
        pages,page_warnings=extract_pages(original)
        warnings.extend(page_warnings)
        usable=sum(text_score(text)>=60 for text in pages)
        chunks=page_chunks(pages,source_file,title,record['sha256'])
        if usable<len(pages): warnings.append(f'{len(pages)-usable} page(s) need OCR or contain no readable text.')
        state='imported' if pages and usable==len(pages) else 'ocr_pending'
    except Exception as exc:
        chunks=[];usable=0;state='extraction_failed'
        warnings.append(f'Cannot extract PDF: {type(exc).__name__}: {str(exc)[:200]}')
    with connect(database) as c:
        c.execute('BEGIN IMMEDIATE')
        # Name collisions with a different original must never replace existing material.
        collision=c.execute('SELECT sha256 FROM document_assets WHERE source_file=? AND page_number=0',(source_file,)).fetchone()
        if collision and collision['sha256']!=record['sha256']:
            raise ValueError('Document identity has a different original: '+source_file)
        c.execute('INSERT OR IGNORE INTO document_records VALUES (?,?)',(source_file,title))
        if chunks:
            replace_document_chunks(database,source_file,chunks,connection=c)
        register_import(database,source_file,'other','pdf_text_pending_ocr' if state=='ocr_pending' else 'pdf_text',
                        None,warnings,chunks,connection=c)
        register_asset(c,source_file,0,asset)
        if name.startswith('Civil Law/'): topic='civil'
        elif name.startswith('Criminal law/'): topic='criminal'
        else: topic=None
        if topic: c.execute('INSERT OR IGNORE INTO document_topics VALUES (?,?)',(source_file,topic))
        c.execute("""INSERT INTO collection_files VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(origin_path) DO UPDATE SET state=excluded.state,pages=excluded.pages,
            extracted_pages=excluded.extracted_pages,warnings_json=excluded.warnings_json,updated_at=excluded.updated_at""",
            (name,record['sha256'],record['byte_size'],source_file,asset['storage_key'],state,len(pages),usable,
             json.dumps(warnings,ensure_ascii=False),datetime.now(UTC).isoformat()))
    return {**record,'source_file':source_file,'state':state,'pages':len(pages),'extracted_pages':usable,'chunks':len(chunks),'warnings':warnings}


def verify_removal(workspace,archive,storage,database,records):
    for name in ROOT_NAMES:
        root=(workspace/name).resolve()
        if archive.resolve().is_relative_to(root) or storage.resolve().is_relative_to(root) or database.resolve().is_relative_to(root):
            raise ValueError('Database, archive and managed originals must be outside the folders being removed.')
    current=inventory(workspace)
    if {(r['origin_path'],r['sha256']) for r in current}!={(r['origin_path'],r['sha256']) for r in records}:
        raise ValueError('Collection changed during import; refusing removal.')
    with connect(database) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database integrity check failed; refusing removal.')
        for record in records:
            if digest_file(archive_path(archive,record['origin_path']))!=record['sha256']:
                raise ValueError('Original is not safely archived.')
            if not record['is_pdf']: continue
            row=c.execute('SELECT * FROM collection_files WHERE origin_path=?',(record['origin_path'],)).fetchone()
            if not row or row['sha256']!=record['sha256'] or not row['source_file']:
                raise ValueError('Original not registered in library: '+record['origin_path'])
            if digest_file(archive_path(storage,row['storage_key']))!=record['sha256']:
                raise ValueError('Managed original failed checksum: '+record['origin_path'])
            if not c.execute('SELECT 1 FROM document_assets WHERE source_file=? AND sha256=?',(row['source_file'],record['sha256'])).fetchone():
                raise ValueError('Managed original has no database link.')
    for name in ROOT_NAMES:
        target=workspace/name
        if not target.exists(): continue
        if target.resolve().parent!=workspace.resolve() or target.is_symlink():
            raise ValueError('Unsafe removal target: '+str(target))


def backup_database(database):
    backup=database.parent/'backups'/('before-collection-intake-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.sqlite3')
    backup.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(database) as source,sqlite3.connect(backup) as destination: source.backup(destination)
    return str(backup)


def ocr_page(pdf,page_number,work,tessdata,dpi):
    prefix=work/f'page-{page_number}'
    image=prefix.with_suffix('.pgm')
    try:
        subprocess.run([shutil.which('pdftoppm'),'-f',str(page_number),'-l',str(page_number),'-singlefile',
            '-scale-to','1800','-gray',str(pdf),str(prefix)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180)
        env={**os.environ,'OMP_THREAD_LIMIT':'1'}
        last_error=None
        for mode,timeout in [('3',120),('6',90)]:
            try:
                result=subprocess.run([shutil.which('tesseract'),str(image),'stdout','--tessdata-dir',str(tessdata),
                    '-l','sin+eng','--psm',mode],check=True,capture_output=True,timeout=timeout,env=env)
                return page_number,clean_text(result.stdout.decode('utf-8',errors='replace'))
            except (subprocess.TimeoutExpired,subprocess.CalledProcessError) as exc:
                last_error=exc
        raise RuntimeError(f'Page {page_number}: OCR failed in both layout modes ({type(last_error).__name__}).') from last_error
    finally: image.unlink(missing_ok=True)


def ocr_pending(database,storage,workspace,workers,min_pages=0,max_documents=None,largest_first=False):
    with connect(database) as c:
        candidates=c.execute("SELECT * FROM collection_files WHERE state IN ('ocr_pending','ocr_failed') GROUP BY source_file ORDER BY pages,origin_path").fetchall()
    candidates=[row for row in candidates if row['pages']>=min_pages]
    if largest_first: candidates=sorted(candidates,key=lambda row:row['pages'],reverse=True)
    if max_documents is not None: candidates=candidates[:max_documents]
    tessdata=workspace/'data/ocr/tessdata'
    if not shutil.which('tesseract') or not shutil.which('pdftoppm') or not (tessdata/'sin.traineddata').exists():
        raise ValueError('OCR tools and Sinhala language data are required.')
    for index,row in enumerate(candidates,1):
        pdf=archive_path(storage,row['storage_key'])
        try:
            pages,warnings=extract_pages(pdf)
            cache=database.parent/'ocr-cache'/row['sha256']
            cache.mkdir(parents=True,exist_ok=True)
            needed=[]
            for number,text in enumerate(pages,1):
                if text_score(text)>=60: continue
                cached=cache/f'page-{number}.txt'
                if cached.exists(): pages[number-1]=cached.read_text(encoding='utf-8')
                else: needed.append(number)
            with tempfile.TemporaryDirectory(prefix='neethi-ocr-') as tmp,ThreadPoolExecutor(max_workers=workers) as pool:
                futures={pool.submit(ocr_page,pdf,n,Path(tmp),tessdata,180):n for n in needed}
                failed_pages=[]
                for completed,future in enumerate(as_completed(futures),1):
                    try:
                        number,text=future.result();pages[number-1]=text
                        temporary=cache/f'page-{number}.{os.getpid()}.tmp'
                        temporary.write_text(text,encoding='utf-8')
                        os.replace(temporary,cache/f'page-{number}.txt')
                    except Exception as exc:
                        number=futures[future];failed_pages.append(number)
                        warnings.append(f'Page {number}: OCR failed ({type(exc).__name__}); manual extraction is required.')
                        print(json.dumps({'phase':'ocr_failed_page','document':index,'page':number,'error':type(exc).__name__}),flush=True)
                    if completed%20==0: print(json.dumps({'phase':'ocr_pages','document':index,'documents':len(candidates),'pages_done':completed,'pages_needed':len(needed)}),flush=True)
            chunks=page_chunks(pages,row['source_file'],law_name(Path(row['source_file'])),row['sha256'])
            usable=sum(text_score(text)>=60 for text in pages)
            warnings.append('OCR output is unverified. Compare every extracted page with the original before publication.')
            empty=[i for i,text in enumerate(pages,1) if text_score(text)<60]
            if empty: warnings.append('Pages still blank/unreadable after OCR: '+','.join(map(str,empty)))
            with connect(database) as c:
                c.execute('BEGIN IMMEDIATE')
                replace_document_chunks(database,row['source_file'],chunks,connection=c)
                register_import(database,row['source_file'],'other','pdf_ocr',None,warnings,chunks,connection=c)
                c.execute("UPDATE collection_files SET state=?,extracted_pages=?,warnings_json=?,updated_at=? WHERE source_file=?",
                    (('ocr_partial' if failed_pages else 'ocr_imported') if chunks else 'ocr_failed',usable,json.dumps(warnings),datetime.now(UTC).isoformat(),row['source_file']))
            print(json.dumps({'phase':'ocr_document','done':index,'total':len(candidates),'chunks':len(chunks),'blank_pages':len(empty)}),flush=True)
        except Exception as exc:
            with connect(database) as c:
                c.execute("UPDATE collection_files SET state='ocr_failed',warnings_json=?,updated_at=? WHERE source_file=?",
                    (json.dumps([type(exc).__name__+': '+str(exc)[:300]]),datetime.now(UTC).isoformat(),row['source_file']))
            print(json.dumps({'phase':'ocr_failed','document':index,'reason':str(exc)[:200]}),flush=True)


def normalize_collection_titles(database):
    changed=0
    with connect(database) as c:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute("""SELECT DISTINCT d.source_file,d.title FROM document_records d JOIN collection_files cf USING(source_file)
            LEFT JOIN document_metadata m USING(source_file) WHERE COALESCE(m.status,'unreviewed')!='verified'""").fetchall()
        for row in rows:
            name=row['source_file']
            if not any(name.startswith(root+'/') for root in ROOT_NAMES): continue
            title=IMPORTS.get(Path(name).name,(Path(name).stem,''))[0] if name.startswith('step 001 pdf folder/') else law_name(Path(name))
            if title==row['title']: continue
            c.execute('UPDATE document_records SET title=? WHERE source_file=?',(title,name))
            c.execute('UPDATE legal_chunks SET law=? WHERE source_file=?',(title,name))
            c.execute('UPDATE legal_chunks_fts SET law=? WHERE source_id IN (SELECT source_id FROM legal_chunks WHERE source_file=?)',(title,name))
            c.execute('DELETE FROM legal_embeddings WHERE source_id IN (SELECT source_id FROM legal_chunks WHERE source_file=?)',(name,))
            changed+=1
    return changed


def verify_library(database,storage,archive):
    with connect(database) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Library integrity check failed.')
        records=c.execute('SELECT * FROM collection_files').fetchall()
        chunk_count=c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]
        indexed_count=c.execute('SELECT COUNT(DISTINCT source_id) FROM legal_chunks_fts').fetchone()[0]
        mismatches=c.execute('''SELECT COUNT(*) FROM legal_chunks_fts f LEFT JOIN legal_chunks ch USING(source_id)
            WHERE ch.source_id IS NULL OR f.law IS NOT ch.law OR f.section IS NOT ch.section
            OR f.heading IS NOT ch.heading OR f.text IS NOT ch.text''').fetchone()[0]
        if chunk_count!=indexed_count or mismatches:
            raise ValueError('Keyword search index does not match the authoritative extracted text.')
        for row in records:
            for root,key in ((archive,row['origin_path']),(storage,row['storage_key'])):
                if digest_file(archive_path(root,key))!=row['sha256']:
                    raise ValueError('Original checksum failed: '+row['origin_path'])
            if not c.execute('SELECT 1 FROM document_assets WHERE source_file=? AND sha256=?',(row['source_file'],row['sha256'])).fetchone():
                raise ValueError('Missing original database link.')
        return {
            'verified_original_paths':len(records),
            'unique_collection_pdfs':len({r['sha256'] for r in records}),
            'collection_documents_with_text':c.execute('SELECT COUNT(DISTINCT cf.source_file) FROM collection_files cf JOIN legal_chunks ch USING(source_file)').fetchone()[0],
            'total_library_documents':c.execute('SELECT COUNT(*) FROM document_records').fetchone()[0],
            'total_library_chunks':c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0],
            'states':dict(c.execute('SELECT state,COUNT(*) FROM collection_files GROUP BY state').fetchall()),
            'documents_with_blank_or_unreadable_pages':c.execute('SELECT COUNT(DISTINCT source_file) FROM collection_files WHERE pages>extracted_pages').fetchone()[0],
            'documents_with_ocr_errors':c.execute("SELECT COUNT(DISTINCT source_file) FROM collection_files WHERE state IN ('ocr_failed','ocr_partial','extraction_failed')").fetchone()[0],
            'published_documents':c.execute("SELECT COUNT(*) FROM document_metadata WHERE status='verified'").fetchone()[0],
            'integrity':'ok', 'foreign_key_violations':[], 'keyword_index_matches':True,
        }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--remove-source-folders',action='store_true')
    parser.add_argument('--ocr',action='store_true')
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--normalize-titles',action='store_true')
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--min-pages',type=int,default=0)
    parser.add_argument('--max-documents',type=int)
    parser.add_argument('--largest-first',action='store_true')
    parser.add_argument('--workspace',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--database',type=Path,default=get_settings().legal_ai_database)
    parser.add_argument('--storage-root',type=Path,default=get_settings().legal_ai_storage_root)
    parser.add_argument('--archive-root',type=Path,default=get_settings().legal_ai_database.parent/'source-collections')
    args=parser.parse_args()
    workspace=args.workspace.resolve()
    if args.remove_source_folders and not args.apply: parser.error('Removal requires --apply.')
    title_backup=backup_database(args.database) if args.normalize_titles else None
    if args.normalize_titles:
        print(json.dumps({"normalized_titles":normalize_collection_titles(args.database)}),flush=True)
    if args.verify:
        report=verify_library(args.database,args.storage_root,args.archive_root)
        if title_backup: report['pre_normalization_backup']=title_backup
        report['source_folders_removed'] = all(not (workspace/name).exists() for name in ROOT_NAMES)
        output=workspace/'reports/collection-final-verification.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
        return
    records=inventory(workspace)
    report={'total_files':len(records),'pdf_files':sum(r['is_pdf'] for r in records),
        'bytes':sum(r['byte_size'] for r in records),'unique_pdfs':len({r['sha256'] for r in records if r['is_pdf']}),
        'archive':str(args.archive_root),'records':records,'mode':'apply' if args.apply else 'audit'}
    if args.apply:
        report['database_backup']=backup_database(args.database)
        initialize_inventory(args.database)
        if args.ocr:
            ocr_pending(args.database,args.storage_root,workspace,args.workers,args.min_pages,args.max_documents,args.largest_first)
        else:
            results=[]
            for index,record in enumerate(records,1):
                results.append(intake_one(workspace,args.archive_root,args.storage_root,args.database,record))
                if index%10==0 or index==len(records):
                    print(json.dumps({'phase':'intake','done':index,'total':len(records),'states':dict(Counter(r['state'] for r in results))}),flush=True)
            report['records']=results
        with connect(args.database) as c:
            report['states']=dict(c.execute('SELECT state,COUNT(*) FROM collection_files GROUP BY state').fetchall())
            report['documents']=c.execute('SELECT COUNT(*) FROM document_records').fetchone()[0]
            report['chunks']=c.execute('SELECT COUNT(*) FROM legal_chunks').fetchone()[0]
        if args.remove_source_folders:
            verify_removal(workspace,args.archive_root,args.storage_root,args.database,records)
            removed=[]
            for name in ROOT_NAMES:
                target=workspace/name
                if target.exists(): shutil.rmtree(target);removed.append(name)
            report['removed_folders']=removed
    output=workspace/'reports'/('collection-ocr-large.json' if args.ocr and args.largest_first else 'collection-ocr.json' if args.ocr else 'collection-migration.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=True),flush=True)


if __name__=='__main__': main()
