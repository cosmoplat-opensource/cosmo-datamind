#!/usr/bin/env python3
"""Restore the original run layout from the compressed release files.

    python3 unpack.py            # decompress every <name>.xz and extract every <dir>.tar.xz next to itself
    python3 unpack.py --verify   # first check MANIFEST.sha256
    python3 unpack.py --remove   # delete the archives after a successful restore

Split files (<name>.part000, .part001, ...) are concatenated in order.  Needs about 11 GB of free disk space
for the whole tree; pass sub-directories as arguments to restore only those, e.g.
    python3 unpack.py runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1
"""
import hashlib, lzma, sys, tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parts(base):
    if base.exists():
        return [base]
    return sorted(base.parent.glob(base.name + ".part[0-9][0-9][0-9]"))


class Cat:
    def __init__(self, files):
        self.files, self.f = list(files), None

    def read(self, n=-1):
        chunks, need = [], n
        while self.files or self.f:
            if self.f is None:
                self.f = open(self.files.pop(0), "rb")
            b = self.f.read(need if n >= 0 else -1)
            if not b:
                self.f.close(); self.f = None
                continue
            chunks.append(b)
            if n >= 0:
                need -= len(b)
                if need <= 0:
                    break
        return b"".join(chunks)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    roots = [ROOT / a for a in args] or [ROOT]
    if "--verify" in sys.argv:
        bad = 0
        for line in (ROOT / "MANIFEST.sha256").read_text().splitlines():
            h, rel = line.split("  ", 1)
            if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != h:
                print("MISMATCH", rel); bad += 1
        if bad:
            sys.exit(1)
        print("manifest ok")
    bases = set()
    for r in roots:
        for f in r.rglob("*"):
            n = f.name
            if ".part" in n and n[-3:].isdigit():
                n = n[: n.rindex(".part")]
            if n.endswith(".xz"):
                bases.add(f.with_name(n))
    done = []
    for base in sorted(bases):
        src = parts(base)
        if base.name.endswith(".tar.xz"):
            with lzma.open(Cat(src)) as xz, tarfile.open(fileobj=xz, mode="r|") as tar:
                tar.extractall(base.parent, filter="data")
        else:
            with lzma.open(Cat(src)) as xz, open(base.with_name(base.name[:-3]), "wb") as out:
                for b in iter(lambda: xz.read(1 << 22), b""):
                    out.write(b)
        done += src
        print("restored", base.relative_to(ROOT))
    if "--remove" in sys.argv:
        for f in done:
            f.unlink()


if __name__ == "__main__":
    main()
