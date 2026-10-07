#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible release archives, only from tracked files."""
import gzip
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
version = (root / 'VERSION').read_text().strip()
output = root / 'artifacts'; output.mkdir(exist_ok=True)
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
files = sorted(f for f in files if f)
if not files: raise SystemExit('Commit the source before building a release.')
prefix = f'gnome-ultra-power-{version}'
archive = output / f'{prefix}.tar.gz'
with archive.open('wb') as dest, gzip.GzipFile(fileobj=dest, mode='wb', filename='', mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w') as tar:
    for name in files:
        data = (root/name).read_bytes()
        info=tarfile.TarInfo(f'{prefix}/{name}')
        info.size=len(data); info.mtime=0; info.mode=0o755 if (root/name).stat().st_mode & 0o111 else 0o644
        tar.addfile(info, io.BytesIO(data))
extension = output / f'ultra-power@vcanonici-{version}.zip'
with zipfile.ZipFile(extension, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
    for name in files:
        if name.startswith('extension/'):
            info=zipfile.ZipInfo(name.removeprefix('extension/'), (2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o100644 << 16
            bundle.writestr(info, (root/name).read_bytes())
(output/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in [archive,extension]))
print(archive); print(extension); print(output/'SHA256SUMS')
