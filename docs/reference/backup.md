# backup

## from the terminal

```bash
uv run smortboard export backup.json   # writes the board to a file
uv run smortboard import backup.json   # reads it back into an empty database
```

**both run without a server.** that is the way to get a board out, or back in, when the ui will
not start.

- both respect `--db` and `SMORTBOARD_DB` exactly like the server, so a bundle round-trips through
  whichever database you point them at.
- both refuse `--demo`. a demo database is a throwaway made and discarded every run.
- `import` refuses a database that already holds a board, rather than half-overwrite it. point
  `--db` at a fresh path for a restore.

## from the browser

**while the board runs, the backup section in `o` does the same:**

| button | does |
|---|---|
| **export** | downloads the bundle |
| **import as new board(s)** | uploads one and adds its boards beside yours. every row gets a new id, so nothing already on the board is replaced. |

browser import leaves out the bundle's board-wide settings, saved prompts and deleted-card backups;
they belong to the database they came from. replacing a board in place is not offered. a restore
under the original ids is still `smortboard import`, into an empty database.

## what a bundle contains

**everything the database holds, as one plain-text json file:**

- every board, repo registration and the paths remembered for it
- every card and its tasks, criteria, leases, dependencies and comments
- attachments, contents included, base64-encoded
- the event log
- board-wide settings and saved prompts
- mission control's messages and plans

## what a bundle never contains

**no credential of any kind.** not your card token, your api key, any credential profile's name or
token, or anything else under `~/.config/smortboard/`. none of it is ever written to the database,
so export has nothing to strip. a bundle is safe to attach to a public bug report as it stands.
