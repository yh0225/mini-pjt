"""Place search over the Chroma index (RAG tool for the Researcher)."""

from __future__ import annotations

import threading
from typing import Any

from langchain_core.tools import tool

from travel_agent.ingest.index import get_vectorstore

# Theme tags are conservative (e.g. Seongsan Ilchulbong has none), so a theme is a
# ranking signal, not a filter: tagged places get their cosine distance reduced.
THEME_BOOST = 0.05
OVERFETCH = 3
MAX_K = 10
SNIPPET_CHARS = 300


_store_lock = threading.Lock()
_store_instance = None


def _store():
    """Open the vector store once. The agent runs tool calls in parallel threads, and
    opening Chroma concurrently from two threads crashes, so initialization is locked."""
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            _store_instance = get_vectorstore()
        return _store_instance


def rerank(results: list[tuple[Any, float]], theme: str | None, k: int) -> list[tuple[Any, float]]:
    """Sort (doc, distance) pairs by distance, boosting docs tagged with `theme`."""

    def adjusted(item: tuple[Any, float]) -> float:
        doc, distance = item
        if theme and doc.metadata.get(f"theme_{theme}"):
            return distance - THEME_BOOST
        return distance

    return sorted(results, key=adjusted)[:k]


def format_result(doc: Any) -> str:
    m = doc.metadata
    coords = "yes" if "lat" in m and "lon" in m else "no"
    themes = m.get("themes") or "-"
    return (
        f"- place: {m.get('place')} | category: {m.get('category')} | themes: {themes} "
        f"| coords: {coords}\n  {doc.page_content[:SNIPPET_CHARS]}"
    )


@tool
def search_places(query: str, city: str, theme: str | None = None, k: int = 8) -> str:
    """Search travel guide listings (sights, activities, restaurants, shops) in one city.

    Args:
        query: What to look for. Korean or English both work, e.g. "아이와 갈 만한 해변".
        city: City id, e.g. "jeju", "busan", "osaka" (see the trip request).
        theme: Optional "family" or "healing"; places tagged with it are ranked higher.
        k: Number of results (max 10).
    """
    k = max(1, min(k, MAX_K))
    where = {"$and": [{"city": city}, {"kind": "listing"}]}
    results = _store().similarity_search_with_score(query, k=k * OVERFETCH, filter=where)
    if not results:
        return f"No places found in '{city}' for '{query}'. Try a different query."
    return "\n".join(format_result(doc) for doc, _ in rerank(results, theme, k))


def lookup_place(city: str, place: str) -> dict[str, Any] | None:
    """Return index metadata for an exact place name in a city, or None."""
    where = {"$and": [{"city": city}, {"place": place}, {"kind": "listing"}]}
    found = _store().get(where=where, limit=1)
    metadatas = found.get("metadatas") or []
    return metadatas[0] if metadatas else None
