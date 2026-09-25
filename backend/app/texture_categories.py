"""Versioned vocabulary transcribed from TEXTURE-CATEGORIES_2026.xlsx, List1."""
import json
from pathlib import Path

CATEGORIES = json.loads(Path(__file__).with_name("texture_categories_2026.json").read_text(encoding="utf-8"))
