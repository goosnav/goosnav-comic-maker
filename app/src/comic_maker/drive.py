"""Send complete carousel exports to Drive in posting order."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import pipeline
from .settings import atomic_bytes


class TransferError(ValueError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def folder_link(value):
    if not isinstance(value, str):
        raise ValueError('Paste a Google Drive folder link.')
    url = urlsplit(value.strip())
    query = parse_qs(url.query)
    match = re.fullmatch(r'/drive/(?:u/\d+/)?folders/([\w-]+)/?', url.path, re.ASCII)
    folder_id = match[1] if match else query.get('id', [''])[0] if url.path == '/open' else ''
    resource_key = query.get('resourcekey', [''])[0]
    if (url.scheme != 'https' or url.netloc.lower() != 'drive.google.com'
            or not re.fullmatch(r'[A-Za-z0-9_-]+', folder_id)
            or not re.fullmatch(r'[A-Za-z0-9_-]*', resource_key)):
        raise ValueError('Paste a Google Drive folder link.')
    return folder_id, resource_key


def carousel_exports(job):
    root = Path(job['output']).resolve() / 'all-social-carousels'
    if not job.get('carousel_output') or not root.is_dir() or root.is_symlink():
        raise ValueError('This batch has no social carousel folder. Export with a carousel option first.')
    carousels = []
    for item in job['items']:
        if item['status'] not in ('complete', 'warning'):
            continue
        stage = item.get('result', {}).get('stages', {}).get('stylized-carousel', {})
        if stage.get('status') != 'complete':
            continue
        stage_slides = [Path(name) for name in stage['files'] if name.endswith('.png')]
        if not stage_slides:
            continue
        name = stage_slides[0].parent.name
        if name == 'stylized-carousel':
            name = item['folder'] + '--' + name
        if not re.fullmatch(r'[\w .-]+', name) or name in ('.', '..'):
            raise ValueError('Regenerate the comic to restore its carousel folder.')
        folder = root / name
        slides = pipeline.carousel_slides(folder)
        expected = [f'image-{i}.png' for i in range(1, len(stage_slides) + 1)]
        caption = folder / (name + '.txt')
        files = ([caption] if caption.exists() else []) + slides
        if (folder.is_symlink() or [p.name for p in slides] != expected
                or any(not p.is_file() or p.is_symlink() for p in files)):
            raise ValueError(f'Carousel files are incomplete or invalid in {name}. Regenerate the comic first.')
        carousels.append((name, files))
    if not carousels:
        raise ValueError('This batch has no completed social carousels to upload.')
    return carousels


class Drive:
    def __init__(self, root):
        self.config = root / 'drive.conf'
        self.lock = threading.Lock()

    def binary(self):
        found = shutil.which('rclone', path=os.pathsep.join([
            os.environ.get('PATH', ''), '/opt/homebrew/bin', '/usr/local/bin', str(Path.home()/'.local/bin'),
        ]))
        if not found:
            raise ValueError('Install rclone to connect Google Drive.')
        return found

    def status(self):
        return {'connected': self.config.is_file(), 'busy': self.lock.locked()}

    def run(self, *args, config=None, timeout=120, log=lambda message: None):
        # This profile belongs to Comic Maker. Do not accept settings from other rclone remotes.
        env = {key: value for key, value in os.environ.items() if not key.startswith('RCLONE_')}
        try:
            result = subprocess.run([self.binary(), '--config', str(config or self.config), '--ask-password=false',
                                     '--retries', '1', '--low-level-retries', '1', *args],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f'{args[0]} timed out after {timeout} seconds.')
            raise TransferError('Drive timed out. Use the same folder link to continue.', retryable=True) from None
        if result.returncode:
            # rclone setup output can contain credentials. Keep it out of the GUI and logs.
            if args[0] == 'config':
                raise ValueError('Google Drive sign-in did not finish. Try connecting again.')
            detail = result.stderr.strip()
            for flag in ('--drive-resource-key', '--drive-root-folder-id'):
                if flag in args:
                    detail = detail.replace(args[args.index(flag) + 1], '[redacted]')
            detail = re.sub(r'(?i)(Bearer\s+|[?&](?:access_token|resourcekey|upload_id)=)[^\s&"\']+', r'\1[redacted]', detail)
            log(f'{args[0]} exited with status {result.returncode}\n{detail[-8000:]}')
            detail = result.stderr.lower()
            codes = set(re.findall(r'(?:error |status(?: code)?[ =:]+)([45]\d\d)\b', detail))
            if (codes & {'429', '500', '502', '503', '504'} or re.search(
                r'ratelimitexceeded|rate limit|backenderror|connection reset|connection refused|\beof\b|'
                r'i/o timeout|tls handshake timeout|temporary failure|deadline exceeded', detail
            )):
                raise TransferError('Temporary Drive error. Use the same folder link to continue.', retryable=True)
            if 'invalid_grant' in detail or '401' in codes:
                raise ValueError('Google Drive sign-in expired. Connect Google Drive again.')
            if 'permission' in detail or codes & {'403', '404'} or 'directory not found' in detail:
                raise ValueError('Drive could not access the destination. Check the folder link and edit access.')
            raise ValueError(f'Drive could not finish {args[0]}. Try again to continue.')
        return result.stdout

    def connect(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError('Wait for the current Drive operation to finish.')
        try:
            with tempfile.TemporaryDirectory(dir=self.config.parent) as temporary:
                config = Path(temporary) / 'drive.conf'
                self.run('config', 'create', 'comic-maker', 'drive', 'scope=drive', 'config_is_local=true',
                         config=config, timeout=300)
                profile = configparser.ConfigParser(interpolation=None)
                profile.read(config)
                if not profile.get('comic-maker', 'token', fallback=''):
                    raise ValueError('Google Drive sign-in did not finish. Try connecting again.')
                atomic_bytes(self.config, config.read_bytes(), private=True)
        finally:
            self.lock.release()

    def upload(self, destination, carousels, progress, log):
        folder_id, resource_key = destination
        flags = ['--drive-root-folder-id', folder_id]
        if resource_key:
            flags += ['--drive-resource-key', resource_key]
        remote = 'comic-maker:'

        def retry(operation):
            for attempt in range(4):
                try:
                    return operation()
                except TransferError as exc:
                    if not exc.retryable or attempt == 3:
                        raise
                    delay = 2 ** (attempt + 1)
                    log(f'{exc} Retrying in {delay} seconds.')
                    time.sleep(delay)

        def listing(path, *options):
            log(f'Listing {path.removeprefix(remote) or "destination"}')
            return retry(lambda: json.loads(self.run('lsjson', path, *flags, *options, log=log)))

        def existing_files(path):
            return listing(path, '--hash', '--hash-type', 'MD5', '--drive-use-created-date')

        def verified_prefix(name, files, entries):
            expected = {path.name: path for path in files}
            seen = set()
            images = []
            for entry in entries:
                filename = entry['Name']
                if entry['IsDir'] or '\t' in entry.get('ID', '') or filename in seen or filename not in expected:
                    raise ValueError(f'Drive already contains different files in {name}. Choose another destination.')
                with expected[filename].open('rb') as file:
                    checksum = hashlib.file_digest(file, 'md5').hexdigest()
                if entry.get('Hashes', {}).get('md5') != checksum:
                    raise ValueError(f'Drive already has a different {filename} in {name}. Choose another destination.')
                seen.add(filename)
                if filename.endswith('.png'):
                    images.append((datetime.fromisoformat(entry['ModTime']), filename))
            images.sort()
            slide_names = [path.name for path in files if path.suffix == '.png']
            if ([filename for _, filename in images] != slide_names[:len(images)]
                    or len({created for created, _ in images}) != len(images)):
                raise ValueError(f'The slides already on Drive in {name} are out of order. Choose another destination.')
            return seen

        def folder_state(name, files, children):
            path = remote + name
            matches = [entry for entry in children if entry['Name'] == name]
            if matches and (len(matches) != 1 or not matches[0]['IsDir'] or '\t' in matches[0].get('ID', '')):
                raise ValueError(f'The Drive name {name} is already in use. Choose another destination.')
            seen = verified_prefix(name, files, existing_files(path)) if matches else set()
            return bool(matches), seen

        children = listing(remote)
        states = [folder_state(name, files, children) for name, files in carousels]
        done = 0
        total = sum(len(files) for _, files in carousels)
        for (name, files), initial_state in zip(carousels, states):
            path = remote + name
            state = initial_state

            def send_carousel():
                nonlocal state
                # After a failed write, check Drive before retrying. It may have accepted the file.
                exists, seen = state if state is not None else folder_state(name, files, listing(remote))
                state = None
                if not exists:
                    log(f'Creating folder {name}')
                    self.run('mkdir', path, *flags, log=log)
                for file in files:
                    if file.name in seen:
                        log(f'Already verified {name}/{file.name}')
                        continue
                    progress(done + len(seen), total, f'{name} / {file.name}')
                    log(f'Uploading {name}/{file.name}')
                    self.run('copyto', str(file), path + '/' + file.name, *flags,
                             '--immutable', '--checksum', '--no-update-modtime', log=log)
                    seen.add(file.name)
                    log(f'Uploaded {name}/{file.name}')

            retry(send_carousel)

            def verify_carousel():
                if len(verified_prefix(name, files, existing_files(path))) != len(files):
                    raise TransferError(f'Drive has not listed every file in {name} yet.', retryable=True)

            retry(verify_carousel)
            log(f'Verified {len(files)} files in {name}')
            done += len(files)
            progress(done, total, name)
        return total
