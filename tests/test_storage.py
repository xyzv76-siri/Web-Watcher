from web_watcher.storage import open_database


def test_sqlite_foundation(tmp_path):
    db_path = tmp_path / "test.db"

    with open_database(db_path) as connection:
        connection.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO test DEFAULT VALUES")
        row = connection.execute("SELECT COUNT(*) FROM test").fetchone()

    assert row == (1,)
