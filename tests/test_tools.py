from types import SimpleNamespace

import pytest

from travel_agent.tools.places import THEME_BOOST, rerank
from travel_agent.tools.travel import haversine_km, travel_minutes


def doc(name: str, family: bool = False):
    return SimpleNamespace(metadata={"place": name, "theme_family": family})


def test_rerank_boosts_theme_tagged_docs_without_filtering_others():
    results = [(doc("untagged-close"), 0.30), (doc("tagged-far", family=True), 0.30 + 0.03)]
    ranked = [d.metadata["place"] for d, _ in rerank(results, "family", k=2)]
    assert ranked == ["tagged-far", "untagged-close"]  # 0.03 gap < THEME_BOOST


def test_rerank_keeps_much_closer_untagged_doc_first():
    results = [(doc("untagged"), 0.20), (doc("tagged", family=True), 0.20 + THEME_BOOST + 0.1)]
    ranked = [d.metadata["place"] for d, _ in rerank(results, "family", k=2)]
    assert ranked == ["untagged", "tagged"]


def test_rerank_without_theme_is_plain_distance_order_and_truncates():
    results = [(doc("b"), 0.5), (doc("a"), 0.1), (doc("c"), 0.9)]
    assert [d.metadata["place"] for d, _ in rerank(results, None, k=2)] == ["a", "b"]


def test_haversine_known_distance():
    # Jeju City Hall -> Seogwipo City Hall is roughly 30 km apart.
    assert haversine_km(33.4996, 126.5312, 33.2541, 126.5600) == pytest.approx(27.4, abs=1.0)


def test_travel_minutes_walk_for_short_distance():
    minutes, mode = travel_minutes(0.5, "car")
    assert mode == "walk" and 5 <= minutes <= 10


def test_travel_minutes_car_faster_than_transit():
    car, _ = travel_minutes(20, "car")
    transit, _ = travel_minutes(20, "transit")
    assert car < transit


def test_travel_minutes_unknown_transport_falls_back_to_transit():
    assert travel_minutes(10, "boat") == travel_minutes(10, "transit")
