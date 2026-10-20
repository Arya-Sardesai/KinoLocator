from datetime import datetime, timezone

from kinolocator.stats import build_stats

NOW = datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)


def show(venue, start, title, **kw):
    return {"venue_id": venue, "start": start, "title": title, **kw}


def test_counts_unique_screenings_not_snapshot_rows():
    a = show("rio", "2026-10-20T19:00:00-07:00", "Alien", format="35mm")
    b = show("rio", "2026-10-21T19:00:00-07:00", "Alien", format="35mm")
    c = show("park", "2026-10-21T19:00:00-07:00", "Wildwood")
    snapshots = [
        {"date": "2026-10-19", "showtimes": [a, b]},
        {"date": "2026-10-20", "showtimes": [a, b, c]},  # a and b again
    ]
    current = {
        "showtimes": [dict(a, tmdb_id=348), dict(c, tmdb_id=791523)],
        "films": {"348": {"title": "Alien", "year": 1979}, "791523": {"title": "Wildwood", "year": 2026}},
    }
    stats = build_stats(snapshots, current, {}, {"rio": "The Rio"}, NOW)
    assert stats["days"] == 2
    assert stats["totals"] == {"screenings": 3, "films": 2, "cinemas": 2}
    # Older rows without a tmdb_id take the current match for their venue and title.
    assert stats["top_films"][0] == {"title": "Alien", "year": 1979, "tmdb_id": 348, "screenings": 2}
    assert stats["decades"] == [{"decade": 1970, "films": 1}, {"decade": 2020, "films": 1}]
    assert stats["repertory"]["repertory"] == {"films": 1, "screenings": 2}
    assert stats["repertory"]["new"] == {"films": 1, "screenings": 1}
    assert stats["cinemas"][0] == {"venue_id": "rio", "name": "The Rio", "screenings": 2}
    assert {"format": "35mm", "screenings": 2} in stats["formats"]
