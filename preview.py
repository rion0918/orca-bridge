"""Serve bounded static snapshots on an origin separate from the Bridge API."""

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

import bridge

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
TTL = 900
MAX_FILES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
STATIC_DIRS = {'assets', 'css', 'fonts', 'images', 'img', 'js'}
MEDIA_TYPES = {
    '.html': 'text/html', '.css': 'text/css',
    '.js': 'text/javascript', '.mjs': 'text/javascript',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.ico': 'image/x-icon',
    '.woff': 'font/woff', '.woff2': 'font/woff2',
}
HEADERS = {
    'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': (
        "sandbox allow-scripts allow-same-origin allow-forms; default-src 'none'; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'none'; worker-src 'none'; "
        "frame-ancestors 'none'; form-action 'none'; base-uri 'none'"
    ),
}


def origin():
    raw = os.environ.get('BRIDGE_PREVIEW_ORIGIN', '').rstrip('/')
    try:
        url = urlsplit(raw)
        valid = (url.scheme == 'https' and url.hostname and url.username is None
                 and url.password is None and not (url.path or url.query or url.fragment))
        if not valid and raw != 'http://127.0.0.1:8767':
            raise ValueError
        port = url.port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        raise HTTPException(409, 'Configure a separate BRIDGE_PREVIEW_ORIGIN')
    api_origins = {os.environ.get('BRIDGE_NGROK_URL', ''), 'http://127.0.0.1:8765'}
    tunnel = bridge.DATA / 'tunnel.json'
    if tunnel.is_file():
        api_origins.add(json.loads(tunnel.read_text()).get('url', ''))
    for api in api_origins:
        endpoint = urlsplit(api)
        if api and (endpoint.hostname, endpoint.port or (443 if endpoint.scheme == 'https' else 80)) == (url.hostname, port):
            raise HTTPException(409, 'Preview and Bridge API origins must be separate')
    return raw


def directory(preview_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}', preview_id):
        raise HTTPException(404, 'Preview not found')
    return bridge.DATA / 'previews' / preview_id


def read_asset(root, path):
    # Resolve every component relative to an already opened directory.
    current = os.open(root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise HTTPException(409, 'Preview assets must be ordinary files')
            data = file.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, 'Preview asset is too large')
            return data
    finally:
        os.close(current)


def create(project):
    allowed = {part.strip() for part in os.environ.get('BRIDGE_PREVIEW_PROJECTS', '').split(',')}
    if project not in allowed:
        raise HTTPException(403, 'Enable preview for this project on the Mac first')
    endpoint = origin()
    project_root = bridge.project_path(project)
    dist = project_root / 'dist'
    root = dist if not dist.is_symlink() and (dist / 'index.html').is_file() else project_root
    if not (root / 'index.html').is_file() or (root / 'index.html').is_symlink():
        raise HTTPException(409, 'No static index.html or dist/index.html found')

    preview_id = secrets.token_urlsafe(24)
    dest = directory(preview_id)
    parent = dest.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = time.time()
    active = 0
    for folder in parent.iterdir():
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', folder.name) or folder.is_symlink():
            continue
        try:
            expiry = json.loads((folder / 'expires.json').read_text())
        except FileNotFoundError:
            continue
        if expiry <= now:
            shutil.rmtree(folder)
        else:
            active += 1
    if active >= 4:
        raise HTTPException(409, 'Close an existing preview first')

    expires_at = now + TTL
    known_secrets = [value.encode() for key, value in os.environ.items()
                     if len(value) > 7 and any(word in key.upper()
                                              for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY'))]
    count = total = 0
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        staging = Path(tmp) / 'snapshot'
        assets = staging / 'assets'
        assets.mkdir(parents=True)
        for base, dirs, files in os.walk(root, followlinks=False):
            base = Path(base)
            dirs[:] = [name for name in dirs
                       if not name.startswith('.') and name != 'node_modules'
                       and not (base / name).is_symlink()
                       and (root == dist or base != root or name in STATIC_DIRS)]
            for name in files:
                path = base / name
                if name.startswith('.') or path.is_symlink() or path.suffix.lower() not in MEDIA_TYPES:
                    continue
                relative = path.relative_to(root)
                data = read_asset(root, relative)
                if any(secret in data for secret in known_secrets):
                    raise HTTPException(409, 'Credential detected in preview assets')
                count += 1
                total += len(data)
                if count > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise HTTPException(413, 'Preview exceeds the snapshot limit')
                output = assets / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
        if not (assets / 'index.html').is_file():
            raise HTTPException(409, 'Preview entry was not captured')
        (staging / 'expires.json').write_text(json.dumps(expires_at))
        staging.rename(dest)
    bridge.audit('static_preview_created', project)
    return {'id': preview_id, 'url': f'{endpoint}/p/{preview_id}/', 'expires_at': expires_at}


def revoke(preview_id):
    dest = directory(preview_id)
    if dest.is_dir() and not dest.is_symlink():
        shutil.rmtree(dest)


@app.get('/p/{preview_id}/{name:path}')
@app.get('/p/{preview_id}/')
def get_asset(preview_id: str, name: str = 'index.html'):
    dest = directory(preview_id)
    path = Path(name)
    media = MEDIA_TYPES.get(path.suffix.lower())
    if path.is_absolute() or not media or any(part.startswith('.') for part in path.parts):
        raise HTTPException(404, 'Asset not found')
    try:
        if json.loads((dest / 'expires.json').read_text()) <= time.time():
            raise HTTPException(404, 'Preview not found')
        data = read_asset(dest / 'assets', path)
    except OSError:
        raise HTTPException(404, 'Asset not found')
    return Response(data, media_type=media, headers=HEADERS)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8767, access_log=False)
