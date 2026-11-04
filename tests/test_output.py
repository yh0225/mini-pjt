import json

from travel_agent.output import save_itinerary, sources, to_markdown
from travel_agent.state import Activity, Candidate, DayPlan, Itinerary, TripRequest

REQUEST = TripRequest(destination="jeju", nights=0, days=1, theme="family", children_ages=[5])
CANDS = [
    Candidate(
        place="Beach",
        city="jeju",
        category="sight",
        summary_ko="",
        reason_ko="",
        source_url="https://en.wikivoyage.org/wiki/Jeju",
    ),
    Candidate(
        place="Unused",
        city="jeju",
        category="sight",
        summary_ko="",
        reason_ko="",
        source_url="https://en.wikivoyage.org/wiki/Unused",
    ),
]
ITIN = Itinerary(
    title="제주 가족 여행",
    theme="family",
    days=[
        DayPlan(
            day=1,
            summary="해변",
            activities=[
                Activity(
                    start="10:00",
                    end="12:00",
                    title="해변 | 모래놀이",
                    place="Beach",
                    category="sight",
                ),
                Activity(
                    start="12:00", end="13:00", title="점심", place="근처 식당", category="meal"
                ),
            ],
        )
    ],
    tips=["선크림"],
    warnings=["1일차: 이동이 많아요."],
)


def test_sources_lists_only_used_candidates():
    assert sources(ITIN, CANDS) == [("Beach", "https://en.wikivoyage.org/wiki/Jeju")]


def test_markdown_has_days_tips_warnings_sources_and_license():
    md = to_markdown(ITIN, REQUEST, CANDS)
    assert md.startswith("# 제주 가족 여행")
    assert "## 1일차 — 해변" in md
    assert "해변 / 모래놀이" in md  # pipes escaped so the table stays intact
    assert "- 선크림" in md and "1일차: 이동이 많아요." in md
    assert "[Beach](https://en.wikivoyage.org/wiki/Jeju)" in md and "Unused" not in md
    assert "CC BY-SA" in md


def test_save_writes_json_and_markdown(tmp_path):
    paths = save_itinerary(ITIN, REQUEST, CANDS, "t1", base_dir=tmp_path)
    assert [p.name for p in paths] == ["itinerary.json", "itinerary.md"]
    data = json.loads(paths[0].read_text(encoding="utf-8"))
    assert data["itinerary"]["title"] == "제주 가족 여행"
    assert data["sources"] == [{"place": "Beach", "url": "https://en.wikivoyage.org/wiki/Jeju"}]
