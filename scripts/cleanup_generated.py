"""Remove only named generated test caches. Unknown files and legal originals are retained."""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    workspace=Path(__file__).resolve().parent.parent
    names=['.pytest-local','.pytest_cache','tmp/optional-details-tests-20260921',
        'tmp/optional-details-tests-20260921b','tmp/review-cache-tests-20260921',
        'tmp/review-fix-tests-20260921','tmp/project-insight-tests','tmp/catalog-tests']
    report={'removed':[],'retained':[],'errors':[],'mode':'apply' if args.apply else 'audit'}
    for name in names:
        target=workspace/name
        if not target.exists(): continue
        resolved=target.resolve()
        if not resolved.is_relative_to(workspace) or resolved==workspace or target.is_symlink():
            raise ValueError('Refusing unsafe cleanup target: '+str(target))
        if args.apply:
            try:
                shutil.rmtree(target)
                report['removed'].append(name)
            except OSError as exc:
                report['errors'].append({'path':name,'reason':str(exc)})
        else: report['retained'].append(name)
    # An old JSON chunk export duplicates the database but may help recovery: archive it.
    export=workspace/'data/processed/chunks.json'
    if args.apply and export.is_file():
        archive=workspace/'data/archive/legacy-chunks.json'
        archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():
            shutil.move(str(export),str(archive))
            report['archived_export']=str(archive.relative_to(workspace))
    destination=workspace/'reports/generated-cleanup.json'
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__': main()
