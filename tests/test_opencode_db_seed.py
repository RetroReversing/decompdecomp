"""Regression test: per-helper OPENCODE_DB inherits OpenCode 1.19+ account/credential rows without session history."""
import sqlite3
from pathlib import Path

from decompnet.agents.opencode import OPENCODE


def test_session_command_seeds_opencode_account_tables_only(tmp_path, monkeypatch):
    xdg_data = tmp_path / "xdg_data"
    main_db = xdg_data / "opencode" / "opencode.db"
    main_db.parent.mkdir(parents=True)

    conn = sqlite3.connect(str(main_db))
    conn.executescript(
        """
        CREATE TABLE migration(id TEXT PRIMARY KEY);
        CREATE TABLE account(id TEXT PRIMARY KEY, token TEXT NOT NULL);
        CREATE TABLE account_state(account_id TEXT PRIMARY KEY, active INTEGER NOT NULL);
        CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT NOT NULL);
        INSERT INTO migration VALUES ('m1');
        INSERT INTO account VALUES ('acct1', 'tok-initial');
        INSERT INTO account_state VALUES ('acct1', 1);
        INSERT INTO session VALUES ('ses1', 'do-not-copy');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setattr(OPENCODE, "standalone", lambda exe: False)

    workdir = tmp_path / "helper"
    workdir.mkdir()
    _, env = OPENCODE.session_command("opencode", workdir, "http://127.0.0.1:1/mcp", "mcp-tok", "opencode-go/hy4-flash-free")

    helper_db = Path(env["OPENCODE_DB"])
    assert helper_db.is_file()
    dst = sqlite3.connect(str(helper_db))
    assert dst.execute("SELECT id, token FROM account").fetchall() == [("acct1", "tok-initial")]
    assert dst.execute("SELECT account_id, active FROM account_state").fetchall() == [("acct1", 1)]
    assert dst.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0
    dst.close()

    # Refreshed credentials in main_db propagate on the next session.
    conn = sqlite3.connect(str(main_db))
    conn.execute("UPDATE account SET token='tok-refreshed' WHERE id='acct1'")
    conn.commit()
    conn.close()

    OPENCODE.session_command("opencode", workdir, "http://127.0.0.1:1/mcp", "mcp-tok", "opencode-go/hy4-flash-free")
    dst = sqlite3.connect(str(helper_db))
    assert dst.execute("SELECT token FROM account WHERE id='acct1'").fetchone() == ("tok-refreshed",)
    dst.close()
