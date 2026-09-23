# Backup

## From the terminal

```bash
uv run smortboard export backup.json   # writes the board to a file
uv run smortboard import backup.json   # reads it back into an empty database
```

Both run without a server: the way to get a board out, or back in, when the UI will not start.

- Both respect `--db` and `SMORTBOARD_DB` exactly like the server, so a bundle round-trips through
  whichever database you point them at.
- Both refuse `--demo`. A demo database is a throwaway made and discarded every run.
- `import` refuses a database that already holds a board, rather than half-overwrite it. Point
  `--db` at a fresh path for a restore.

## From the browser

While the board runs, the **backup** section of settings (`o`) does the same:

| Button | Does |
|---|---|
| **export** | downloads the bundle |
| **import as new board(s)** | uploads one and adds its boards beside yours. Every row gets a new id, so nothing already on the board is replaced. |

Browser import leaves out the bundle's board-wide settings, saved prompts and deleted-card backups;
they belong to the database they came from. Replacing a board in place is not offered. A restore
under the original ids is still `smortboard import`, into an empty database.

## What a bundle contains

Everything a board's own database holds, as one plain-text JSON file:

- every board, repo registration and the paths remembered for it
- every card and its tasks, criteria, leases, dependencies and comments
- attachments, contents included, base64-encoded
- the event log
- board-wide settings and saved prompts
- mission control's messages and plans

## What a bundle never contains

Your card token, your API key, any Claude credential profile name or token, or anything else under
`~/.config/smortboard/`. None of it is ever written to the database a bundle is built from, so
export has nothing to strip. A bundle is safe to attach to a public bug report as it stands.
