import pytest
import sqlite3
import os
from scripts.semantic_tagger.cli import get_main_db


def test_main_db_is_readonly():
    # If the database doesn't exist (e.g. running on CI), mock it or skip
    if not os.path.exists("data/mnemosyne.sqlite3"):
        pytest.skip("Main DB not found, skipping readonly test")

    conn = get_main_db()
    with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
        # Attempt to create a dummy table
        conn.execute("CREATE TABLE test_readonly (id INTEGER)")
