from travel_agent.rules import check_itinerary, to_minutes
from travel_agent.state import Activity, DayPlan, Itinerary, TripRequest

FAMILY = TripRequest(destination="jeju", days=1, theme="family", children_ages=[5])
FAMILY_OLDER = TripRequest(destination="jeju", days=1, theme="family", children_ages=[9])
HEALING = TripRequest(destination="bali", days=1, theme="healing")


def act(start: str, end: str, category: str, title: str = "x") -> Activity:
    return Activity(start=start, end=end, title=title, place="p", category=category)


def trip(*activities: Activity, theme: str = "family", day_count: int = 1) -> Itinerary:
    days = [
        DayPlan(day=i, summary="s", activities=list(activities)) for i in range(1, day_count + 1)
    ]
    return Itinerary(title="t", theme=theme, days=days)


GOOD_FAMILY_DAY = [
    act("10:00", "11:30", "sight"),
    act("11:30", "12:00", "move"),
    act("12:00", "13:00", "meal"),
    act("13:00", "14:30", "rest"),
    act("14:30", "16:30", "activity"),
    act("16:30", "17:00", "move"),
    act("17:30", "18:30", "meal"),
]

GOOD_HEALING_DAY = [
    act("09:30", "11:00", "activity"),
    act("11:00", "12:30", "meal"),
    act("12:30", "14:00", "rest"),
    act("14:00", "15:30", "sight"),
    act("15:30", "17:00", "rest"),
]


def rules_of(violations) -> set[str]:
    return {v.rule for v in violations}


def test_to_minutes():
    assert to_minutes("09:30") == 570
    assert to_minutes("9:5") == 545
    assert to_minutes("ab:cd") is None
    assert to_minutes("25:00") is None


def test_good_family_day_passes():
    assert check_itinerary(trip(*GOOD_FAMILY_DAY), FAMILY) == []


def test_family_too_many_moves():
    acts = GOOD_FAMILY_DAY + [act("18:30", "18:45", "move"), act("18:45", "19:00", "move")]
    assert "family_max_moves" in rules_of(check_itinerary(trip(*acts), FAMILY))


def test_family_day_must_end_by_20():
    acts = GOOD_FAMILY_DAY + [act("19:00", "20:30", "rest")]
    assert "family_day_end" in rules_of(check_itinerary(trip(*acts), FAMILY))


def test_family_nap_required_only_for_young_children():
    no_nap = [a for a in GOOD_FAMILY_DAY if a.category != "rest"]
    assert "family_nap" in rules_of(check_itinerary(trip(*no_nap), FAMILY))
    assert "family_nap" not in rules_of(check_itinerary(trip(*no_nap), FAMILY_OLDER))


def test_family_nap_too_short_or_too_late():
    short = [a if a.category != "rest" else act("13:00", "13:30", "rest") for a in GOOD_FAMILY_DAY]
    assert "family_nap" in rules_of(check_itinerary(trip(*short), FAMILY))
    late = [a if a.category != "rest" else act("15:30", "16:30", "rest") for a in GOOD_FAMILY_DAY]
    assert "family_nap" in rules_of(check_itinerary(trip(*late), FAMILY))


def test_family_nap_not_required_on_short_day():
    morning_only = [act("09:00", "11:00", "sight"), act("11:30", "12:30", "meal")]
    assert "family_nap" not in rules_of(check_itinerary(trip(*morning_only), FAMILY))


def test_family_lunch_window():
    late_lunch = [
        a if a.category != "meal" or a.start != "12:00" else act("13:30", "14:00", "meal")
        for a in GOOD_FAMILY_DAY
    ]
    assert "family_lunch" in rules_of(check_itinerary(trip(*late_lunch), FAMILY))


def test_good_healing_day_passes():
    assert check_itinerary(trip(*GOOD_HEALING_DAY, theme="healing"), HEALING) == []


def test_healing_too_many_main_activities_and_gaps():
    acts = [
        act("09:30", "10:30", "sight"),
        act("10:40", "11:30", "activity"),  # only 10 min after the previous main
        act("12:30", "13:30", "sight"),
        act("14:00", "15:00", "activity"),
        act("15:00", "17:00", "rest"),
    ]
    rules = rules_of(check_itinerary(trip(*acts, theme="healing"), HEALING))
    assert {"healing_max_main", "healing_gap"} <= rules


def test_healing_free_time_required_on_full_day():
    # keep the day running past 16:00 so it still counts as a full day
    no_rest = [a for a in GOOD_HEALING_DAY if a.category != "rest"] + [
        act("16:30", "17:30", "meal")
    ]
    assert "healing_free_time" in rules_of(
        check_itinerary(trip(*no_rest, theme="healing"), HEALING)
    )


def test_structure_day_count_bad_times_and_overlap():
    request = TripRequest(destination="jeju", days=2, theme="healing")
    acts = [
        act("10:00", "11:00", "sight"),
        act("10:30", "12:00", "meal"),
        act("13:00", "12:00", "rest"),
    ]
    rules = rules_of(check_itinerary(trip(*acts, theme="healing"), request))
    assert {"day_count", "time_overlap", "time_format"} <= rules


def test_violations_carry_day_numbers():
    acts = GOOD_FAMILY_DAY + [act("19:00", "20:30", "rest")]
    violations = check_itinerary(
        trip(*acts, day_count=2), TripRequest(**{**FAMILY.model_dump(), "days": 2})
    )
    assert sorted({v.day for v in violations}) == [1, 2]
