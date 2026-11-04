"""Tag chunks with travel themes (family / healing) using the light Claude model.

Tags are cached per city in data/raw/tags/<city>.json so re-runs only tag new chunks.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Literal

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, Field, ValidationError

from travel_agent.config import PROMPTS_DIR, TAGS_DIR
from travel_agent.ingest.chunk import Chunk
from travel_agent.llm import get_chat_model

log = logging.getLogger(__name__)

BATCH_SIZE = 25
SNIPPET_CHARS = 500
PARSE_RETRIES = 2

Theme = Literal["family", "healing"]


class ChunkTag(BaseModel):
    id: str
    themes: list[Theme] = Field(default_factory=list)


class TagBatch(BaseModel):
    results: list[ChunkTag]


def _load_cache(city: str) -> dict[str, list[str]]:
    path = TAGS_DIR / f"{city}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_cache(city: str, cache: dict[str, list[str]]) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    path = TAGS_DIR / f"{city}.json"
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


class Tagger:
    def __init__(self) -> None:
        # No temperature: current Claude models reject non-default sampling params.
        llm = get_chat_model("light", max_tokens=8000)
        self.llm = llm.with_structured_output(TagBatch)
        self.system = (PROMPTS_DIR / "tagger.md").read_text(encoding="utf-8")

    def _tag_batch(self, batch: list[Chunk]) -> dict[str, list[str]]:
        snippets = "\n\n".join(
            f'<snippet id="{c.id}">\n{c.text[:SNIPPET_CHARS]}\n</snippet>' for c in batch
        )
        expected = {c.id for c in batch}
        last_error: Exception | None = None
        # Only parse failures are retried here; API errors (auth, access, throttling)
        # are retried by the client itself or should fail fast.
        for _ in range(PARSE_RETRIES + 1):
            try:
                result = self.llm.invoke([("system", self.system), ("human", snippets)])
            except (OutputParserException, ValidationError) as exc:
                last_error = exc
                log.warning("Tagging output could not be parsed, retrying: %s", exc)
                continue
            if not isinstance(result, TagBatch):
                last_error = ValueError(f"Unexpected structured output: {result!r}")
                log.warning("Tagging returned no structured output, retrying")
                continue
            tags = {r.id: list(r.themes) for r in result.results if r.id in expected}
            # Snippets the model skipped are treated as "no theme".
            return {cid: tags.get(cid, []) for cid in expected}
        raise RuntimeError(f"Tagging failed after retries: {last_error}")

    def tag_city(
        self,
        city: str,
        chunks: list[Chunk],
        on_progress: Callable[[int], None] | None = None,
    ) -> dict[str, list[str]]:
        cache = _load_cache(city)
        todo = [c for c in chunks if c.id not in cache]
        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start : start + BATCH_SIZE]
            cache.update(self._tag_batch(batch))
            _save_cache(city, cache)  # save after each batch so failures lose little work
            if on_progress:
                on_progress(len(batch))
        return {c.id: cache.get(c.id, []) for c in chunks}
