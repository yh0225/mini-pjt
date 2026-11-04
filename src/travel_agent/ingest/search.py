"""Quick retrieval check for M1.

Usage:
    python -m travel_agent.ingest.search "아이랑 갈 만한 해변" --city jeju --theme family
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from travel_agent.ingest.index import get_vectorstore

console = Console()


def build_filter(city: str | None, theme: str | None, listings_only: bool) -> dict | None:
    clauses: list[dict] = []
    if city:
        clauses.append({"city": city})
    if theme:
        clauses.append({f"theme_{theme}": True})
    if listings_only:
        clauses.append({"kind": "listing"})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--city")
    parser.add_argument("--theme", choices=["family", "healing"])
    parser.add_argument("--all-kinds", action="store_true", help="include prose chunks")
    parser.add_argument("-k", type=int, default=8)
    args = parser.parse_args()

    store = get_vectorstore()
    where = build_filter(args.city, args.theme, listings_only=not args.all_kinds)
    results = store.similarity_search_with_score(args.query, k=args.k, filter=where)

    table = Table(title=f"{args.query}  filter={where}")
    for col in ["score", "city", "place", "category", "themes", "coords", "text"]:
        table.add_column(col, overflow="fold")
    for doc, score in results:
        m = doc.metadata
        coords = f"{m['lat']:.3f},{m['lon']:.3f}" if "lat" in m and "lon" in m else "-"
        table.add_row(
            f"{score:.3f}",
            m["city"],
            m.get("place", "-"),
            m.get("category", "-"),
            m.get("themes", ""),
            coords,
            doc.page_content[:120],
        )
    console.print(table)


if __name__ == "__main__":
    main()
