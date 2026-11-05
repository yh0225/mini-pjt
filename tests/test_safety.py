from travel_agent.agents.intake import IntakeExtraction, merge_request
from travel_agent.safety import (
    MASK,
    filter_preferences,
    guard_input,
    is_blocked_place,
    is_blocked_request,
    mask_pii,
)
from travel_agent.state import TripRequest

# --- personal data ---------------------------------------------------------------------------


def test_mask_pii_hides_ids_cards_phones_and_email():
    text = (
        "주민번호 900101-1234567, 카드 1234-5678-9012-3456, 폰 010-1234-5678, 메일 hong@example.com"
    )
    masked, kinds = mask_pii(text)
    for secret in ("900101-1234567", "1234-5678-9012-3456", "010-1234-5678", "hong@example.com"):
        assert secret not in masked
    assert masked.count(MASK) == 4
    assert kinds == ["주민등록번호", "카드번호", "전화번호", "이메일"]


def test_mask_pii_handles_korean_particles_right_after_numbers():
    # Regression: "\b" does not match between a digit and a Korean letter.
    masked, kinds = mask_pii(
        "주민번호 900101-1234567인데 카드는 1234567890123456이고 01012345678로 연락"
    )
    assert "1234567" not in masked and "3456" not in masked and "5678" not in masked
    assert kinds == ["주민등록번호", "카드번호", "전화번호"]


def test_mask_pii_leaves_trip_details_alone():
    text = "제주 2박3일 5살 아이랑, 10:00 출발, 2026-12-01"
    assert mask_pii(text) == (text, [])


def test_guard_input_notices():
    g = guard_input("제주 2박3일. 주민번호 900101-1234567인데 숙소 예약도 해줘")
    assert "900101-1234567" not in g.text and MASK in g.text
    assert any("개인정보(주민등록번호)" in n for n in g.notices)
    assert any("예약은 지원하지 않아요" in n for n in g.notices)
    assert guard_input("부산 1박2일 아이랑").notices == []


# --- off-topic requests ---------------------------------------------------------------------


def test_off_topic_requests_are_detected():
    assert is_blocked_request("방콕 카지노랑 유흥업소 위주로")
    assert is_blocked_request("nightclub tour")
    assert any("도와드리지 않아요" in n for n in guard_input("카지노 가고 싶어").notices)


def test_ordinary_requests_are_not_flagged():
    # regressions for over-broad words
    assert not is_blocked_request("싱가포르 나이트 사파리 가고 싶어")
    assert not is_blocked_request("성인 2명이랑 아이 1명")
    assert not is_blocked_request("바다 보고 바나힐 가고 싶어")


def test_filter_preferences_and_merge_drop_off_topic_wishes():
    assert filter_preferences(["스파", "카지노", "유흥업소"]) == (["스파"], ["카지노", "유흥업소"])
    merged = merge_request(TripRequest(), IntakeExtraction(preferences=["카지노", "유흥업소"]))
    assert merged.preferences == []
    merged = merge_request(TripRequest(), IntakeExtraction(preferences=["요가", "카지노"]))
    assert merged.preferences == ["요가"]


# --- places ---------------------------------------------------------------------------------


def test_bars_clubs_and_casinos_are_blocked():
    # real listings returned in evaluation GRD-01 and casinos in other sections
    assert is_blocked_place({"place": "Narz", "section": "Drink"}, "방콕 최고의 클럽 중 하나")
    assert is_blocked_place({"place": "Octave Rooftop Lounge & Bar", "section": "Drink"})
    assert is_blocked_place({"place": "Angel Witch", "section": "Drink"}, "화려한 쇼")
    assert is_blocked_place({"place": "Seven luck casino", "section": "Do"})
    assert is_blocked_place({"place": "Marina Bay Sands Casino", "section": "See"})


def test_cafes_spas_and_parks_are_allowed():
    assert not is_blocked_place({"place": "Seniman Coffee Studio", "section": "Drink"})
    assert not is_blocked_place({"place": "Bali Pulina", "section": "Drink"}, "논 뷰 카페")
    assert not is_blocked_place({"place": "I.Sawan Residential Spa & Club", "section": "Do"})
    assert not is_blocked_place({"place": "Benjakiti Park", "section": "See"})
    assert not is_blocked_place({"place": "Night Safari", "section": "See"}, "야간 사파리")
