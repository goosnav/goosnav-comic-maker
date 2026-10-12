"""Validated settings and durable local storage."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ValidationError

DEFAULT_PROMPT = """Add color to this hand-drawn comic. Change nothing else.
Preserve every line, all lettering exactly letter for letter, expressions, poses, objects, panel borders, composition, and proportions. Do not redraw, reinterpret, add, erase, crop, or move anything. Keep speech balloons and their backgrounds white. Use coherent flat colors with no gradients, lighting effects, textures, or shading. Give recurring subjects the same colors in every panel. Keep black linework black and the surrounding paper white. Return only the complete colored image, in exactly the same layout and aspect ratio."""

class Settings(BaseModel):
    input_mode: Literal['auto', 'pencil', 'color'] = 'auto'
    style: Literal['ink', 'paint'] = 'ink'
    ink_strength: int = Field(50, ge=0, le=100)
    cleanup: int = Field(20, ge=0, le=100)
    crunch: int = Field(20, ge=0, le=100)
    palette_colors: int = Field(24, ge=4, le=64)
    master_scale: int = Field(1, ge=1, le=4)
    canvas_width: Literal[1080, 2160] = 1080
    layout: Literal['auto', 'as-drawn', 'stack', 'grid', 'one-over-two'] = 'auto'
    recursive: bool = True
    pattern: str = '*'
    colorize: bool = False
    provider: Literal['openai', 'gemini', 'fixture'] = 'openai'
    image_model: str = 'gpt-image-2.5-sunburst'
    image_quality: Literal['high', 'xhigh', 'max'] = 'high'
    image_edge: Literal[1024, 2048, 3840] = 3840
    prompt: str = DEFAULT_PROMPT
    vision_fallback: bool = False
    vision_model: str = 'gpt-6-astra'
    ai_attempts: int = Field(2, ge=1, le=3)
    ai_call_limit: int = Field(30, ge=0, le=500)

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
            return Settings.model_validate(self.read('settings.json', {}))
        except ValidationError:
            return Settings()

    def save(self, values):
        settings = Settings.model_validate({**self.settings().model_dump(), **values})
        if not settings.prompt.strip():
            settings.prompt = DEFAULT_PROMPT
        atomic_json(self.settings_path, settings.model_dump())
        return settings

    def key(self, provider):
        return self.read('keys.json', {}).get(provider) or os.environ.get({'openai': 'OPENAI_API_KEY', 'gemini': 'GEMINI_API_KEY'}.get(provider, ''), '')

    def set_key(self, provider, value):
        if provider not in ('openai', 'gemini'):
            raise ValueError('Unknown provider')
        value = value.strip()
        if any(c.isspace() for c in value):
            raise ValueError('API keys cannot contain whitespace')
        keys = self.read('keys.json', {})
        if value:
            keys[provider] = value
        else:
            keys.pop(provider, None)
        atomic_json(self.root / 'keys.json', keys, private=True)

    def key_status(self):
        return {p: bool(self.key(p)) for p in ('openai', 'gemini')}
