from travel_agent.ingest.chunk import parse_page

SAMPLE = """{{pagebanner|Banner.jpg}}
'''Testville''' is a quiet seaside town that is popular with families and people who want to rest.

==Understand==
Testville has a long sandy beach, calm waves, and many parks. It is a good place for a slow trip.

==Get in==
Take the train from the capital. This section should be skipped because it is not useful.

==See==
* {{see | name=Sunny Beach | alt=햇살해변 | lat=33.5 | long=126.5 | content=Shallow water, safe for kids. }}
* {{listing | type=do | name=Forest Spa | lat=abc | long= | hours=09:00-18:00 | content=Relaxing hot spring. }}

==Sleep==
* {{sleep | name=Grand Hotel | content=Expensive. }}
"""


def test_listings_are_extracted_with_category_and_coordinates():
    chunks = parse_page("testville", "en", "Testville", SAMPLE)
    listings = {c.place: c for c in chunks if c.kind == "listing"}

    assert set(listings) == {"Sunny Beach", "Forest Spa"}  # sleep listing skipped
    beach = listings["Sunny Beach"]
    assert beach.category == "sight"
    assert (beach.lat, beach.lon) == (33.5, 126.5)
    assert "햇살해변" in beach.text and "safe for kids" in beach.text
    spa = listings["Forest Spa"]
    assert spa.category == "activity"
    assert spa.lat is None and spa.lon is None  # invalid / empty coordinates
    assert "Hours: 09:00-18:00" in spa.text


def test_prose_skips_irrelevant_sections_and_templates():
    chunks = parse_page("testville", "en", "Testville", SAMPLE)
    prose = [c for c in chunks if c.kind == "prose"]
    sections = {c.section for c in prose}

    assert sections == {"Lead", "Understand"}
    assert not any("pagebanner" in c.text for c in prose)
    assert all(c.url == "https://en.wikivoyage.org/wiki/Testville" for c in chunks)


def test_chunk_ids_are_unique_and_stable():
    first = parse_page("testville", "en", "Testville", SAMPLE)
    second = parse_page("testville", "en", "Testville", SAMPLE)
    ids = [c.id for c in first]
    assert len(ids) == len(set(ids))
    assert ids == [c.id for c in second]
