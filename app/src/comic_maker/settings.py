"""Validated settings and durable local storage."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError

class Settings(BaseModel):
    ink_strength: int = Field(50, ge=0, le=100)
    cleanup: int = Field(20, ge=0, le=100)
    master_scale: int = Field(1, ge=1, le=4)
    canvas_width: Literal[2160] = 2160
    panel_grid_width: int | None = Field(512, ge=16, le=16384)
    summary_grid_width: int | None = Field(1024, ge=16, le=16384)
    layout: Literal['auto', 'as-drawn', 'stack', 'grid', 'one-over-two'] = 'auto'
    recursive: bool = True
    pattern: str = '*'
    lines_file: str = ''
    ad_enabled: bool = False
    ad_folder: str = ''

def data_root() -> Path:
    if os.environ.get('COMIC_MAKER_DATA'):
        return Path(os.environ['COMIC_MAKER_DATA']).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/GoosnavComicMaker'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GoosnavComicMaker'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'GoosnavComicMaker'

def atomic_bytes(path: Path, data: bytes, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if private:
            os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def atomic_json(path: Path, value, private=False):
    atomic_bytes(path, json.dumps(value, indent=2, ensure_ascii=False).encode(), private)

class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / 'settings.json'

    def read(self, name, default):
        try:
            return json.loads((self.root / name).read_text())
        except (OSError, ValueError):
            return default

    def settings(self):
        try:
            values = self.read('settings.json', {})
            if values.get('canvas_width') != 2160:
                values = {**values, 'canvas_width': 2160}
            return Settings.model_validate(values)
        except ValidationError:
            return Settings()

    def save(self, values):
        settings = Settings.model_validate({**self.settings().model_dump(), **values})
        atomic_json(self.settings_path, settings.model_dump())
        return settings
