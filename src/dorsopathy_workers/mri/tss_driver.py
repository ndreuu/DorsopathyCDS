"""Supervise a trusted MPS script after its main has synchronised and returned.

The existing parent CLI can hang in Python's atexit hooks after main returns
zero. This shim does not recover exceptions or timeouts: successful main(0)
alone allows flushing logs and a supervised process exit. The MRI supervisor
then stops any remaining children and independently validates output masks.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-script',type=Path,required=True)
    parser.add_argument('parent_args',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    path=args.parent_script
    if path.is_symlink() or not path.is_file(): return 2
    spec=importlib.util.spec_from_file_location('_trusted_parent_tss_mps',path)
    module=importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        forwarded=args.parent_args
        if forwarded and forwarded[0]=='--': forwarded=forwarded[1:]
        code=module.main(forwarded)
    except BaseException:
        print('Trusted TSS main did not return normally.',file=sys.stderr,flush=True)
        return 2
    if type(code)is not int or code!=0:
        return code if type(code)is int and 1<=code<=255 else 2
    for stream in (sys.stdout,sys.stderr):
        stream.flush()
        try: os.fsync(stream.fileno())
        except OSError: pass  # Pipes cannot be fsynced; flush remains mandatory.
    os._exit(0)


if __name__=='__main__': raise SystemExit(main())
