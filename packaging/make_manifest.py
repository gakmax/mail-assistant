"""Write the release assets the updater reads: latest.json, .sha256, notes.

latest.json is deliberately unversioned. The updater fetches it through
https://github.com/<repo>/releases/latest/download/latest.json, a redirect
that always resolves to the newest release's asset of that exact name, has
no rate limit, and needs no token -- so the filename has to stay constant
across releases while the installer's does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'dist' / 'installer'
DOWNLOAD = 'https://github.com/{repo}/releases/download/v{version}/{name}'


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def git(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                              text=True, encoding='utf-8', check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def notes(version):
    """Commit subjects since the previous tag, as the update dialog's body."""
    previous = git('describe', '--tags', '--abbrev=0', f'v{version}^')
    span = f'{previous}..v{version}' if previous else f'v{version}'
    lines = [f'- {line}' for line in git('log', '--no-merges', '--format=%s', span).splitlines()
             if line and not line.startswith('Co-Authored-By')]
    return '\n'.join(lines) if lines else '세부 변경 내용은 커밋 기록을 확인하세요.'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--repo', required=True, help='owner/name')
    args = parser.parse_args(argv)

    name = f'MailAssistant-Setup-{args.version}.exe'
    installer = OUTPUT / name
    if not installer.exists():
        raise SystemExit(f'설치 파일이 없습니다: {installer}')

    sha = digest(installer)
    body = notes(args.version)
    # sha256sum format, for anyone verifying a manual download by hand.
    (OUTPUT / f'{name}.sha256').write_text(f'{sha}  {name}\n', encoding='utf-8')
    (OUTPUT / 'RELEASE_NOTES.md').write_text(
        f'{body}\n\n---\n\n설치 파일 SHA-256: `{sha}`\n', encoding='utf-8')
    (OUTPUT / 'latest.json').write_text(json.dumps({
        'schema': 1,
        'version': args.version,
        'tag': f'v{args.version}',
        'published_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'notes': body,
        'installer': {
            'name': name,
            'url': DOWNLOAD.format(repo=args.repo, version=args.version, name=name),
            'size': installer.stat().st_size,
            'sha256': sha,
        },
        # The installer owns its own invocation contract, so a future change to
        # these flags reaches clients that are already deployed.
        'install': {
            'silent_args': ['/SILENT', '/NOCANCEL', '/NORESTART', '/SUPPRESSMSGBOXES'],
            'app_mutex': 'Local\\HiworksMailAssistant',
        },
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f'{name}  {installer.stat().st_size:,} bytes')
    print(f'sha256 {sha}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
