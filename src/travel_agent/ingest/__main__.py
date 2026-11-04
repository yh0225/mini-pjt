"""RAG ingestion pipeline: crawl → chunk → tag → index.

Usage:
    python -m travel_agent.ingest                          # all steps, all cities
    python -m travel_agent.ingest --city jeju busan        # selected cities
    python -m travel_agent.ingest --steps crawl chunk      # selected steps (no API key needed)
"""

from __future__ import annotations

import argparse
import logging

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress
from rich.table import Table

from travel_agent.config import CHUNKS_DIR, LOG_DIR
from travel_agent.ingest.chunk import Chunk, parse_page
from travel_agent.ingest.cities import City, select_cities
from travel_agent.ingest.crawler import WikivoyageCrawler

STEPS = ["crawl", "chunk", "tag", "index"]
console = Console()
log = logging.getLogger("travel_agent.ingest")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_DIR / "ingest.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            RichHandler(console=console, show_path=False, level=logging.WARNING),
            file_handler,
        ],
    )
    for noisy in ("httpx", "sentence_transformers", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def pages_of(city: City) -> list[tuple[str, str]]:
    return [("en", p) for p in city.wikivoyage_en] + [("ko", p) for p in city.wikivoyage_ko]


def chunks_path(city: City):
    return CHUNKS_DIR / f"{city.id}.jsonl"


def load_chunks(city: City) -> list[Chunk]:
    path = chunks_path(city)
    if not path.exists():
        raise FileNotFoundError(f"No chunks for {city.id}. Run the 'chunk' step first.")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Chunk.model_validate_json(line) for line in lines if line]


def step_crawl(cities: list[City], refresh: bool) -> None:
    crawler = WikivoyageCrawler()
    total = sum(len(pages_of(c)) for c in cities)
    try:
        with Progress(console=console) as progress:
            task = progress.add_task("crawl", total=total)
            for city in cities:
                for lang, page in pages_of(city):
                    progress.update(task, description=f"crawl {lang}:{page}")
                    try:
                        if crawler.fetch(lang, page, use_cache=not refresh) is None:
                            log.warning("skip %s %s:%s (not available)", city.id, lang, page)
                    except Exception:  # noqa: BLE001 - keep going on per-page failures
                        log.exception("crawl failed %s %s:%s", city.id, lang, page)
                    progress.advance(task)
    finally:
        crawler.close()


def dedupe_listings(chunks: list[Chunk]) -> list[Chunk]:
    """Keep one listing per place name (main and district pages often repeat places).

    Prefers the copy with coordinates, then the one with the longest description.
    """
    best: dict[str, Chunk] = {}
    for c in chunks:
        if c.kind != "listing" or not c.place:
            continue
        key = c.place.strip().lower()
        current = best.get(key)
        if current is None or (c.lat is not None, len(c.text)) > (
            current.lat is not None,
            len(current.text),
        ):
            best[key] = c
    keep = {c.id for c in best.values()}
    return [c for c in chunks if c.kind == "prose" or c.id in keep]


def step_chunk(cities: list[City]) -> dict[str, list[Chunk]]:
    from travel_agent.ingest.crawler import cache_path

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[Chunk]] = {}
    for city in cities:
        chunks: list[Chunk] = []
        for lang, page in pages_of(city):
            path = cache_path(lang, page)
            if not path.exists():
                continue
            try:
                chunks.extend(parse_page(city.id, lang, page, path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                log.exception("chunk failed %s %s:%s", city.id, lang, page)
        unique = dedupe_listings(chunks)
        chunks_path(city).write_text(
            "\n".join(c.model_dump_json() for c in unique), encoding="utf-8"
        )
        result[city.id] = unique
    return result


def step_tag(cities: list[City]) -> dict[str, dict[str, list[str]]]:
    from travel_agent.ingest.tagger import Tagger

    tagger = Tagger()
    all_chunks = {c.id: load_chunks(c) for c in cities}
    result: dict[str, dict[str, list[str]]] = {}
    with Progress(console=console) as progress:
        task = progress.add_task("tag", total=sum(len(v) for v in all_chunks.values()))
        for city in cities:
            progress.update(task, description=f"tag {city.id}")
            result[city.id] = tagger.tag_city(
                city.id, all_chunks[city.id], lambda n: progress.advance(task, n)
            )
    return result


def step_index(cities: list[City]) -> dict[str, int]:
    from travel_agent.ingest.index import index_city
    from travel_agent.ingest.tagger import _load_cache

    counts: dict[str, int] = {}
    with Progress(console=console) as progress:
        task = progress.add_task("index", total=len(cities))
        for city in cities:
            progress.update(task, description=f"index {city.id}")
            counts[city.id] = index_city(city.id, load_chunks(city), _load_cache(city.id))
            progress.advance(task)
    return counts


def print_summary(cities: list[City]) -> None:
    from travel_agent.ingest.tagger import _load_cache

    table = Table(title="Ingest summary")
    for col in ["city", "listings", "with coords", "prose", "family", "healing"]:
        table.add_column(col, justify="right" if col != "city" else "left")
    for city in cities:
        if not chunks_path(city).exists():
            continue
        chunks = load_chunks(city)
        tags = _load_cache(city.id)
        listings = [c for c in chunks if c.kind == "listing"]
        table.add_row(
            city.id,
            str(len(listings)),
            str(sum(c.lat is not None for c in listings)),
            str(len(chunks) - len(listings)),
            str(sum("family" in tags.get(c.id, []) for c in chunks)),
            str(sum("healing" in tags.get(c.id, []) for c in chunks)),
        )
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--city", nargs="*", help="city ids from data/cities.yaml")
    parser.add_argument("--steps", nargs="*", choices=STEPS, default=STEPS)
    parser.add_argument("--refresh", action="store_true", help="re-download cached pages")
    args = parser.parse_args()

    setup_logging()
    cities = select_cities(args.city)
    console.print(f"[bold]cities:[/] {', '.join(c.id for c in cities)}")
    console.print(f"[bold]steps:[/] {' → '.join(s for s in STEPS if s in args.steps)}")

    if "crawl" in args.steps:
        step_crawl(cities, args.refresh)
    if "chunk" in args.steps:
        step_chunk(cities)
    if "tag" in args.steps:
        step_tag(cities)
    if "index" in args.steps:
        counts = step_index(cities)
        console.print(f"indexed {sum(counts.values())} chunks")
    print_summary(cities)
    console.print(f"log: {LOG_DIR / 'ingest.log'}")


if __name__ == "__main__":
    main()
