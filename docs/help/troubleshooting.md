# Troubleshooting

## A card stopped on `LEASE_CONFLICT`

Either the card committed a file outside its [lease](../practices/leases.md), or a write was refused
and it committed nothing. The note names the files. A card refused a write that still committed the
rest does not stop here: it goes on to the gates and lists the wanted paths under needs.

Usual cause: a lease written for a layout the repo does not have. Ways out:

- **Soft lease:** turn soft file leases on in `o`, then switch a board you trust to soft in
  `shift`+`o`. Cards then reach unprotected files on their own.
- **approve** in the inbox (`n`) adds exactly the refused paths and resumes the card.
- **Answer** instead, telling the agent to leave those files alone. An answer alone never widens a
  lease.

Through the API, with the key loaded once:

```bash
K="X-Smortboard-Key: $(cat ~/.config/smortboard/api_key)"
curl -s -H "$K" 127.0.0.1:8000/api/boards                        # board ids
curl -s -H "$K" 127.0.0.1:8000/api/boards/<board-id>/cards       # card ids
curl -s -X PATCH -H "$K" -H 'content-type: application/json' 127.0.0.1:8000/api/cards/<card-id> \
  -d '{"leases": ["src/app/**", "tests/**"]}'                    # replaces the whole list
```

## Other common stops

| Symptom | Fix |
|---|---|
| `this tab has no api key` at the top of the page | Open the link the board printed on start. |
| A card blocks on `no tests ran` | The card added no tests. Each criterion needs one; re-run it with a note saying so. |
| Preflight warns `has no tests yet` | Ask mission control (`.`) for a first card that adds a test suite, or commit your own tests. |
| `.../card_token is not mode 600 - refusing to read it` | `chmod 600 ~/.config/smortboard/card_token` |
| An expired or revoked token (HTTP 401) | `claude setup-token` again, then in `shift`+`p` add it as a new profile, activate it, remove the expired one. |
| The gate fails on `Read-only file system` for a tool other than ruff or pytest | Give that tool its own cache flag: `--no-cache`, `-p no:cacheprovider`, or whatever it takes. |
| A card fails a test its own diff never touches | Preflight (`h`) may show its repo image predates `uv.lock`. The next run rebuilds it. |
| A card waits with `lease conflict with card <id>` | Two leases overlap. It starts when the other card finishes. |
| No new runs start | Check the usage window (`u`) and the board's daily budget (`shift`+`o`). |
| Cards were `CRASH`ed on start-up | The board was stopped mid-run. They wait in the inbox to be resumed. |

Anything else: [open a bug report](https://github.com/BalthazarFitzpatrick/smortboard/issues).
[Beta and reporting](beta.md) says what to include.
