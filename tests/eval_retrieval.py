"""Retrieval quality check: Hit@5 over Korean queries with known answer places.

Not a pytest test (needs the built Chroma index and the embedding model).
Usage:
    python tests/eval_retrieval.py            # city filter only
    python tests/eval_retrieval.py --theme    # city + theme filter
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from travel_agent.ingest.index import get_vectorstore
from travel_agent.ingest.search import build_filter

K = 5

# (query, city, theme, expected substring of the `place` metadata)
CASES: list[tuple[str, str, str, str]] = [
    ("아이랑 물놀이할 수 있는 워터파크", "jeju", "family", "Jeju Water World"),
    ("일출 보기 좋은 화산 봉우리", "jeju", "healing", "Seongsan Ilchulbong"),
    ("부산에서 유명한 해수욕장", "busan", "family", "Haeundae Beach"),
    ("알록달록한 집이 모여 있는 언덕 마을", "busan", "family", "Gamcheon"),
    ("철새를 볼 수 있는 조용한 습지 산책", "gangneung", "healing", "Gyeongpo"),
    ("신라 시대 대표 사찰", "gyeongju", "healing", "Bulguksa"),
    ("전통 한옥이 모여 있는 마을 산책", "jeonju", "healing", "Jeonju Hanok Village"),
    ("조선 시대 왕궁", "seoul", "family", "Gyeongbokgung"),
    ("고래상어를 볼 수 있는 대형 수족관", "osaka", "family", "Kaiyukan"),
    ("판다를 볼 수 있는 동물원", "tokyo", "family", "Ueno Zoo"),
    ("호수가 있는 큰 공원에서 산책", "fukuoka", "healing", "Ohori Park"),
    ("오키나와에서 아이들이 좋아할 수족관", "okinawa", "family", "Churaumi"),
    ("케이블카 타고 올라가는 산 위 테마파크", "danang", "family", "Ba Na"),
    ("섬에 있는 놀이공원", "nhatrang", "family", "Vinpearl"),
    ("온천과 머드 스파에서 휴식", "nhatrang", "healing", "Thap Ba"),
    ("원숭이가 사는 숲", "bali", "family", "Monkey Forest"),
    ("계단식 논 풍경", "bali", "healing", "Tegallalang"),
    ("괌에서 바다 생물 보는 수족관", "guam", "family", "UnderWater World"),
    ("야간에 동물을 구경하는 사파리", "singapore", "family", "Night Safari"),
    ("거대한 인공 나무가 있는 정원", "singapore", "healing", "Gardens by the Bay"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", action="store_true", help="also filter by theme tag")
    args = parser.parse_args()

    store = get_vectorstore()
    console = Console()
    table = Table(title=f"Retrieval Hit@{K} ({'city+theme' if args.theme else 'city'} filter)")
    for col in ["hit", "rank", "city", "query", "expected", "top-1"]:
        table.add_column(col, overflow="fold")

    hits = 0
    for query, city, theme, expected in CASES:
        where = build_filter(city, theme if args.theme else None, listings_only=True)
        docs = store.similarity_search(query, k=K, filter=where)
        places = [d.metadata.get("place", "") for d in docs]
        rank = next((i + 1 for i, p in enumerate(places) if expected.lower() in p.lower()), None)
        hits += rank is not None
        table.add_row(
            "✅" if rank else "❌",
            str(rank or "-"),
            city,
            query,
            expected,
            places[0] if places else "-",
        )

    console.print(table)
    console.print(f"[bold]Hit@{K}: {hits}/{len(CASES)} = {hits / len(CASES):.0%}[/]")


if __name__ == "__main__":
    main()
