"""Central configuration: paths, model IDs, and environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
CITIES_FILE = DATA_DIR / "cities.yaml"
RAW_DIR = DATA_DIR / "raw"
CHUNKS_DIR = DATA_DIR / "raw" / "chunks"
TAGS_DIR = DATA_DIR / "raw" / "tags"
CHROMA_DIR = DATA_DIR / "chroma"
LOG_DIR = DATA_DIR / "logs"
CHECKPOINT_DB = DATA_DIR / "checkpoints.db"
OUTPUT_DIR = ROOT_DIR / "output"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
LIGHT_MODEL = os.getenv("LIGHT_MODEL", "claude-haiku-5-5")

EMBEDDING_MODEL = "BAAI/bge-m3"
CHROMA_COLLECTION = "places"

USER_AGENT = "travel-agent-study/0.1 (learning project; https://github.com/yh0225/mini-pjt)"
CRAWL_DELAY_SECONDS = 1.0


def require_env(name: str) -> str:
    """Return an environment variable or raise a clear error if it is missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value
