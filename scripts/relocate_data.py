"""Copy the verified library to a private persistent directory, then update local config.
Original PDFs and the old database are retained for rollback. Restart the app afterward.
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

from app.config import get_settings


def relocate(database: Path, storage: Path, destination: Path):
    destination=destination.resolve()
    new_database=destination/'legal_index.sqlite3'
    new_storage=destination/'originals'
    if new_database.exists():
        raise ValueError('Destination already contains a database; refusing to overwrite it.')
    if destination.is_relative_to(storage.resolve()) or storage.resolve().is_relative_to(destination):
        raise ValueError('Destination and source storage must be separate directories.')
    destination.mkdir(parents=True,exist_ok=True)
    new_storage.mkdir(exist_ok=True)
    with sqlite3.connect(database) as source, sqlite3.connect(new_database) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Copied database failed integrity validation.')
        if target.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Copied database failed relationship validation.')
        assets=target.execute('SELECT DISTINCT storage_key,sha256 FROM document_assets').fetchall()
    for key,digest in assets:
        original=(storage/key).resolve()
        target=(new_storage/key).resolve()
        if not original.is_relative_to(storage.resolve()) or not target.is_relative_to(new_storage):
            raise ValueError('Unsafe storage key.')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,target)
        with target.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                raise ValueError('Original failed checksum validation: '+key)
    return new_database,new_storage,len(assets)


def update_config(path: Path, database: Path, storage: Path):
    text=path.read_text(encoding='utf-8') if path.exists() else ''
    replacements={'LEGAL_AI_DATABASE':database.as_posix(),'LEGAL_AI_STORAGE_ROOT':storage.as_posix()}
    lines=[]
    for line in text.splitlines():
        key=line.partition('=')[0].strip()
        if key in replacements:
            lines.append(key+'='+replacements.pop(key))
        else: lines.append(line)
    lines.extend(k+'='+v for k,v in replacements.items())
    temporary=path.with_name(path.name+'.relocating')
    temporary.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--update-config',action='store_true')
    args=parser.parse_args()
    settings=get_settings()
    database,storage,count=relocate(settings.legal_ai_database,settings.legal_ai_storage_root,args.destination)
    if args.update_config: update_config(Path('.env'),database,storage)
    print(f'Copied and verified {count} originals. Database: {database}. Storage: {storage}. Restart the application to use the new paths.')


if __name__=='__main__': main()
