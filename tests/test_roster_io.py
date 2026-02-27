"""Tests for roster CSV loading and saving."""

import pytest
from click import ClickException

from manage_keys import load_roster, save_roster


@pytest.fixture()
def valid_roster_csv(tmp_path):
    """Write a minimal valid roster CSV and return its path."""
    p = tmp_path / "roster.csv"
    p.write_text(
        "first_name,last_name,email,mq_id,budget,limit_reset\n"
        "Chaeyeon,Kim,chaeyeon.kim@example.com,60853425,3,weekly\n"
        "Dasol,Kim,dasol.kim@example.com,60853379,5,monthly\n"
    )
    return str(p)


class TestLoadRoster:
    def test_loads_valid_roster(self, valid_roster_csv):
        roster = load_roster(valid_roster_csv)
        assert len(roster) == 2
        assert "chaeyeon.kim@example.com" in roster
        assert roster["chaeyeon.kim@example.com"]["first_name"] == "Chaeyeon"
        assert roster["chaeyeon.kim@example.com"]["mq_id"] == "60853425"
        assert roster["chaeyeon.kim@example.com"]["budget"] == 3.0
        assert roster["chaeyeon.kim@example.com"]["limit_reset"] == "weekly"

    def test_budget_parsed_as_float(self, valid_roster_csv):
        roster = load_roster(valid_roster_csv)
        assert roster["dasol.kim@example.com"]["budget"] == 5.0

    def test_missing_budget_is_none(self, tmp_path):
        p = tmp_path / "roster.csv"
        p.write_text(
            "first_name,last_name,email,mq_id,budget,limit_reset\nYuki,Aoki,yuki@example.com,48385123,,weekly\n"
        )
        roster = load_roster(str(p))
        assert roster["yuki@example.com"]["budget"] is None

    def test_missing_limit_reset_is_none(self, tmp_path):
        p = tmp_path / "roster.csv"
        p.write_text("first_name,last_name,email,mq_id,budget,limit_reset\nYuki,Aoki,yuki@example.com,48385123,3,\n")
        roster = load_roster(str(p))
        assert roster["yuki@example.com"]["limit_reset"] is None

    def test_invalid_limit_reset_raises(self, tmp_path):
        p = tmp_path / "roster.csv"
        p.write_text(
            "first_name,last_name,email,mq_id,budget,limit_reset\nYuki,Aoki,yuki@example.com,48385123,3,biweekly\n"
        )
        with pytest.raises(ClickException, match="Invalid limit_reset"):
            load_roster(str(p))

    def test_missing_required_field_raises(self, tmp_path):
        p = tmp_path / "roster.csv"
        p.write_text("first_name,last_name,email,mq_id,budget,limit_reset\n,Aoki,yuki@example.com,48385123,3,weekly\n")
        with pytest.raises(ClickException, match="first_name"):
            load_roster(str(p))

    def test_nonexistent_file_returns_empty(self, tmp_path):
        roster = load_roster(str(tmp_path / "missing.csv"))
        assert roster == {}

    def test_strips_whitespace(self, tmp_path):
        p = tmp_path / "roster.csv"
        p.write_text(
            "first_name,last_name,email,mq_id,budget,limit_reset\n"
            " Yuki , Aoki ,yuki@example.com, 48385123 ,3, Weekly \n"
        )
        roster = load_roster(str(p))
        assert roster["yuki@example.com"]["first_name"] == "Yuki"
        assert roster["yuki@example.com"]["last_name"] == "Aoki"
        assert roster["yuki@example.com"]["mq_id"] == "48385123"
        assert roster["yuki@example.com"]["limit_reset"] == "weekly"


class TestSaveRoster:
    def test_roundtrip(self, tmp_path):
        roster = {
            "yuki@example.com": {
                "first_name": "Yuki",
                "last_name": "Aoki",
                "mq_id": "48385123",
                "budget": 3.0,
                "limit_reset": "weekly",
            },
        }
        path = str(tmp_path / "out.csv")
        save_roster(roster, path)
        loaded = load_roster(path)
        assert loaded["yuki@example.com"]["first_name"] == "Yuki"
        assert loaded["yuki@example.com"]["mq_id"] == "48385123"
        assert loaded["yuki@example.com"]["budget"] == 3.0
        assert loaded["yuki@example.com"]["limit_reset"] == "weekly"

    def test_none_budget_saved_as_empty(self, tmp_path):
        roster = {
            "yuki@example.com": {
                "first_name": "Yuki",
                "last_name": "Aoki",
                "mq_id": "48385123",
                "budget": None,
                "limit_reset": None,
            },
        }
        path = str(tmp_path / "out.csv")
        save_roster(roster, path)
        loaded = load_roster(path)
        assert loaded["yuki@example.com"]["budget"] is None
        assert loaded["yuki@example.com"]["limit_reset"] is None

    def test_output_is_sorted_by_email(self, tmp_path):
        roster = {
            "zz@example.com": {"first_name": "Z", "last_name": "Z", "mq_id": "2", "budget": 3.0, "limit_reset": None},
            "aa@example.com": {"first_name": "A", "last_name": "A", "mq_id": "1", "budget": 3.0, "limit_reset": None},
        }
        path = str(tmp_path / "out.csv")
        save_roster(roster, path)
        content = (tmp_path / "out.csv").read_text()
        assert content.index("aa@example.com") < content.index("zz@example.com")
