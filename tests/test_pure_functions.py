"""Tests for pure functions in manage_keys.py — no I/O, no mocking needed."""

import pytest
from click import ClickException

from manage_keys import (
    build_key_name,
    display_name,
    map_keys_to_roster,
    parse_key_name,
    validate_roster_row,
)

# ── display_name ──


class TestDisplayName:
    def test_normal_name(self):
        assert display_name({"first_name": "Chaeyeon", "last_name": "Kim"}) == "Chaeyeon Kim"

    def test_strips_outer_whitespace(self):
        """strip() only removes leading/trailing whitespace from the combined string."""
        assert display_name({"first_name": "  Chaeyeon ", "last_name": " Kim  "}) == "Chaeyeon   Kim"

    def test_empty_first_name(self):
        assert display_name({"first_name": "", "last_name": "Kim"}) == "Kim"

    def test_empty_last_name(self):
        assert display_name({"first_name": "Chaeyeon", "last_name": ""}) == "Chaeyeon"

    def test_both_empty(self):
        assert display_name({"first_name": "", "last_name": ""}) == ""

    def test_multi_word_names(self):
        assert display_name({"first_name": "Maira Camila", "last_name": "Nagles Tapia"}) == "Maira Camila Nagles Tapia"


# ── parse_key_name ──


class TestParseKeyName:
    def test_full_format(self):
        date, name, mq_id = parse_key_name("20260227_Chaeyeon Kim_60853425")
        assert date == "20260227"
        assert name == "Chaeyeon Kim"
        assert mq_id == "60853425"

    def test_multi_word_name(self):
        date, name, mq_id = parse_key_name("20260227_Maira Camila Nagles Tapia_48388939")
        assert date == "20260227"
        assert name == "Maira Camila Nagles Tapia"
        assert mq_id == "48388939"

    def test_without_mq_id(self):
        date, name, mq_id = parse_key_name("20260227_Some Name")
        assert date == "20260227"
        assert name == "Some Name"
        assert mq_id is None

    def test_no_date_prefix(self):
        date, name, mq_id = parse_key_name("random_key_name")
        assert date is None
        assert name == "random_key_name"
        assert mq_id is None

    def test_empty_string(self):
        date, name, mq_id = parse_key_name("")
        assert date is None
        assert name == ""
        assert mq_id is None

    def test_roundtrip_with_build(self):
        """parse_key_name should recover what build_key_name produces."""
        info = {"first_name": "Yuki", "last_name": "Aoki", "mq_id": "48385123"}
        key_name = build_key_name(info, date="20260227")
        date, name, mq_id = parse_key_name(key_name)
        assert date == "20260227"
        assert name == "Yuki Aoki"
        assert mq_id == "48385123"


# ── build_key_name ──


class TestBuildKeyName:
    def test_explicit_date(self):
        info = {"first_name": "Chaeyeon", "last_name": "Kim", "mq_id": "60853425"}
        assert build_key_name(info, date="20260227") == "20260227_Chaeyeon Kim_60853425"

    def test_auto_date(self):
        info = {"first_name": "Chaeyeon", "last_name": "Kim", "mq_id": "60853425"}
        result = build_key_name(info)
        # Should start with 8-digit date
        assert len(result.split("_")[0]) == 8
        assert result.endswith("_Chaeyeon Kim_60853425")

    def test_multi_word_name(self):
        info = {"first_name": "Maira Camila", "last_name": "Nagles Tapia", "mq_id": "48388939"}
        assert build_key_name(info, date="20260227") == "20260227_Maira Camila Nagles Tapia_48388939"


# ── validate_roster_row ──


class TestValidateRosterRow:
    def test_valid_row(self):
        row = {"first_name": "Yuki", "last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        validate_roster_row(row, 2)  # Should not raise

    def test_missing_first_name(self):
        row = {"first_name": "", "last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="first_name"):
            validate_roster_row(row, 2)

    def test_missing_last_name(self):
        row = {"first_name": "Yuki", "last_name": "", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="last_name"):
            validate_roster_row(row, 2)

    def test_missing_mq_id(self):
        row = {"first_name": "Yuki", "last_name": "Aoki", "mq_id": "", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="mq_id"):
            validate_roster_row(row, 2)

    def test_whitespace_only_counts_as_empty(self):
        row = {"first_name": "  ", "last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="first_name"):
            validate_roster_row(row, 2)

    def test_missing_key_counts_as_empty(self):
        row = {"last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="first_name"):
            validate_roster_row(row, 2)

    def test_error_includes_line_number(self):
        row = {"first_name": "", "last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="row 5"):
            validate_roster_row(row, 5)

    def test_error_includes_email(self):
        row = {"first_name": "", "last_name": "Aoki", "mq_id": "48385123", "email": "yuki@example.com"}
        with pytest.raises(ClickException, match="yuki@example.com"):
            validate_roster_row(row, 2)


# ── map_keys_to_roster ──


class TestMapKeysToRoster:
    @pytest.fixture()
    def roster(self):
        return {
            "chaeyeon.kim@example.com": {
                "first_name": "Chaeyeon",
                "last_name": "Kim",
                "mq_id": "60853425",
                "budget": 3.0,
                "limit_reset": "weekly",
            },
            "dasol.kim@example.com": {
                "first_name": "Dasol",
                "last_name": "Kim",
                "mq_id": "60853379",
                "budget": 3.0,
                "limit_reset": "weekly",
            },
        }

    def test_matches_by_mq_id(self, roster):
        keys = [{"name": "20260227_Chaeyeon Kim_60853425", "hash": "abc123"}]
        matched, orphaned = map_keys_to_roster(keys, roster)
        assert len(matched) == 1
        assert matched[0][1] == "chaeyeon.kim@example.com"
        assert len(orphaned) == 0

    def test_distinguishes_same_last_name(self, roster):
        keys = [
            {"name": "20260227_Chaeyeon Kim_60853425", "hash": "abc123"},
            {"name": "20260227_Dasol Kim_60853379", "hash": "def456"},
        ]
        matched, orphaned = map_keys_to_roster(keys, roster)
        assert len(matched) == 2
        emails = {m[1] for m in matched}
        assert emails == {"chaeyeon.kim@example.com", "dasol.kim@example.com"}

    def test_orphaned_keys(self, roster):
        keys = [{"name": "20260227_Unknown Person_99999999", "hash": "xyz789"}]
        matched, orphaned = map_keys_to_roster(keys, roster)
        assert len(matched) == 0
        assert len(orphaned) == 1
        assert orphaned[0][1] == "20260227_Unknown Person_99999999"

    def test_key_without_mq_id_is_orphaned(self, roster):
        keys = [{"name": "20260227_Chaeyeon Kim", "hash": "abc123"}]
        matched, orphaned = map_keys_to_roster(keys, roster)
        assert len(matched) == 0
        assert len(orphaned) == 1

    def test_empty_roster(self):
        keys = [{"name": "20260227_Someone_12345", "hash": "abc"}]
        matched, orphaned = map_keys_to_roster(keys, {})
        assert len(matched) == 0
        assert len(orphaned) == 1

    def test_empty_keys(self, roster):
        matched, orphaned = map_keys_to_roster([], roster)
        assert len(matched) == 0
        assert len(orphaned) == 0

    def test_mixed_matched_and_orphaned(self, roster):
        keys = [
            {"name": "20260227_Chaeyeon Kim_60853425", "hash": "abc123"},
            {"name": "20260227_Unknown_99999999", "hash": "xyz789"},
        ]
        matched, orphaned = map_keys_to_roster(keys, roster)
        assert len(matched) == 1
        assert len(orphaned) == 1
