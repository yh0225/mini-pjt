from travel_agent.agents.approval import apply_removals
from travel_agent.agents.intake import (
    QUESTIONS,
    IntakeExtraction,
    build_question,
    merge_request,
    missing_fields,
    normalize_duration,
    unsupported_destination,
    unsupported_message,
)
from travel_agent.graph import route_after_intake
from travel_agent.state import Candidate, TripRequest


def test_normalize_duration_fills_the_other_value():
    assert normalize_duration(3, None) == (3, 4)
    assert normalize_duration(None, 1) == (0, 1)
    assert normalize_duration(None, None) == (None, None)


def test_merge_keeps_known_values_and_applies_new_ones():
    current = TripRequest(destination="jeju", nights=3, days=4, theme="family")
    ext = IntakeExtraction(children_ages=[5], preferences=["바다"])
    merged = merge_request(current, ext)
    assert merged.destination == "jeju" and merged.days == 4
    assert merged.children_ages == [5] and merged.preferences == ["바다"]


def test_merge_maps_supported_city_and_clears_on_unsupported_switch():
    merged = merge_request(TripRequest(), IntakeExtraction(destination_name="제주", city_id="jeju"))
    assert merged.destination == "jeju"
    switched = merge_request(merged, IntakeExtraction(destination_name="파리", city_id=None))
    assert switched.destination is None


def test_unsupported_destination_detection():
    assert unsupported_destination(IntakeExtraction(destination_name="파리")) == "파리"
    assert (
        unsupported_destination(IntakeExtraction(destination_name="제주", city_id="jeju")) is None
    )
    assert unsupported_destination(IntakeExtraction()) is None
    # an id the model made up is not a supported city
    assert unsupported_destination(IntakeExtraction(destination_name="x", city_id="paris")) == "x"


def test_missing_fields_requires_child_ages_only_for_family():
    assert missing_fields(TripRequest()) == ["destination", "duration", "theme"]
    family = TripRequest(destination="jeju", days=4, theme="family")
    assert missing_fields(family) == ["children_ages"]
    healing = TripRequest(destination="bali", days=5, theme="healing")
    assert missing_fields(healing) == []


def test_build_question_asks_one_line_per_missing_field():
    q = build_question(["destination", "theme"])
    assert q.splitlines() == [QUESTIONS["destination"], QUESTIONS["theme"]]


def test_unsupported_message_names_the_city_and_lists_supported_ones():
    msg = unsupported_message("파리")
    assert "파리" in msg and "마칠게요" in msg
    assert "제주" in msg and "싱가포르" in msg


def test_unsupported_city_ends_the_conversation_before_asking_anything():
    state = {"unsupported_destination": "파리", "missing": ["duration", "theme"]}
    assert route_after_intake(state) == "reject"
    assert route_after_intake({"missing": ["theme"]}) == "ask_user"
    assert route_after_intake({"missing": []}) == "supervisor"


def _cand(name: str, approved: bool = True) -> Candidate:
    return Candidate(
        place=name, city="jeju", category="sight", summary_ko="", reason_ko="", approved=approved
    )


def test_apply_removals_uses_positions_among_approved_only():
    cands = [_cand("A"), _cand("B", approved=False), _cand("C"), _cand("D")]
    result = apply_removals(cands, [2, 99])  # 2nd shown item is C; 99 is ignored
    assert [c.place for c in result if c.approved] == ["A", "D"]
    assert len(result) == 4  # removed items are kept but hidden


def test_intake_extraction_tolerates_null_strings_from_the_model():
    # Regression: for "여행 가고 싶어" the model returned the string "null" for every field.
    ext = IntakeExtraction.model_validate(
        {
            "destination_name": "null",
            "city_id": "None",
            "nights": "null",
            "days": "null",
            "theme": "null",
            "children_ages": "null",
            "transport": "",
            "start_date": "null",
        }
    )
    assert ext == IntakeExtraction()
    assert missing_fields(merge_request(TripRequest(), ext)) == ["destination", "duration", "theme"]


def test_null_string_handling_keeps_real_values():
    ext = IntakeExtraction.model_validate(
        {"destination_name": "제주", "nights": 2, "theme": "family"}
    )
    assert (ext.destination_name, ext.nights, ext.theme) == ("제주", 2, "family")
