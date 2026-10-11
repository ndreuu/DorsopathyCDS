#!/usr/bin/env python3
"""Verify frozen module-source PDFs; optional exact-fragment text verification."""
import argparse
import hashlib
import json
from pathlib import Path
import unicodedata


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[1]
    parser.add_argument('--pdf-dir',type=Path,default=root/'var/clinical-sources')
    parser.add_argument('--check-quotes',action='store_true')
    args=parser.parse_args(argv)
    if args.check_quotes:
        try:from pypdf import PdfReader
        except ImportError:parser.error('Quote verification requires optional pypdf in an isolated environment')
    sources=json.loads((root/'src/dorsopathy_cds/catalog/module_sources.json').read_text())
    failed=False
    for doc in sources['documents']:
        filename=doc['local_pdf_filename']
        if Path(filename).name!=filename:raise ValueError('Unsafe catalogue PDF filename')
        path=args.pdf_dir/filename
        if not path.is_file():
            print(doc['id']+': local source PDF absent');failed=True;continue
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        matches=digest.hexdigest()==doc['pdf_sha256'];failed=failed or not matches
        print(doc['id']+(': SHA256 verified' if matches else ': SHA256 mismatch'))
        if args.check_quotes and matches:
            reader=PdfReader(path)
            for quote in doc['quotes']:
                text=unicodedata.normalize('NFKC',' '.join(reader.pages[quote['page']-1].extract_text().split()))
                found=quote['exact_quote'] in text
                print('  '+quote['id']+(': exact fragment verified' if found else ': fragment not found'))
                failed=failed or not found
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
