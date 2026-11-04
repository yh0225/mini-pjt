"""Embed chunks with bge-m3 and store them in Chroma."""

from __future__ import annotations

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from travel_agent.config import CHROMA_COLLECTION, CHROMA_DIR, EMBEDDING_MODEL
from travel_agent.ingest.chunk import Chunk

ADD_BATCH_SIZE = 64


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True, "batch_size": 16},
    )


def get_vectorstore() -> Chroma:
    return Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=get_embeddings(),
        persist_directory=str(CHROMA_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def to_document(chunk: Chunk, themes: list[str]) -> Document:
    # Chroma metadata must be scalar and non-null, so themes become boolean flags.
    metadata = {
        "city": chunk.city,
        "lang": chunk.lang,
        "source": chunk.source,
        "url": chunk.url,
        "page": chunk.page,
        "section": chunk.section,
        "kind": chunk.kind,
        "themes": ",".join(sorted(themes)),
        "theme_family": "family" in themes,
        "theme_healing": "healing" in themes,
    }
    optional = {
        "place": chunk.place,
        "category": chunk.category,
        "lat": chunk.lat,
        "lon": chunk.lon,
    }
    metadata.update({k: v for k, v in optional.items() if v is not None})
    return Document(id=chunk.id, page_content=chunk.text, metadata=metadata)


def index_city(city: str, chunks: list[Chunk], tags: dict[str, list[str]]) -> int:
    """Replace all documents for `city` with freshly embedded chunks."""
    store = get_vectorstore()
    store.delete(where={"city": city})  # idempotent re-runs
    docs = [to_document(c, tags.get(c.id, [])) for c in chunks]
    for start in range(0, len(docs), ADD_BATCH_SIZE):
        batch = docs[start : start + ADD_BATCH_SIZE]
        store.add_documents(batch, ids=[d.id for d in batch])
    return len(docs)
