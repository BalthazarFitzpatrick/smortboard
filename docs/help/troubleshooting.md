# troubleshooting

## a card stopped on `LEASE_CONFLICT`

**the card committed a file outside its [lease](../practices/leases.md), or a write was refused and
it committed nothing.** the note names the files. a card that was refused a write but still committed
the rest does not stop here: it goes on to the gates and lists the wanted paths under needs.

the usual cause is a lease written for a layout the repo does not have. ways out:

- **soft lease:** enable soft file leases in `o`, then switch a board you trust to soft in
  `shift`+`o`. its cards then reach unprotected files no other card holds.
- **approve** in the inbox (`n`) adds exactly the refused paths and resumes the card.
- **answer** instead, telling the agent to leave those files alone. an answer alone never widens a
  lease.

through the api, with the key loaded once:

```bash
K="X-Smortboard-Key: $(cat ~/.config/smortboard/api_key)"
curl -s -H "$K" 127.0.0.1:8000/api/boards                        # board ids
curl -s -H "$K" 127.0.0.1:8000/api/boards/<board-id>/cards       # card ids
curl -s -X PATCH -H "$K" -H 'content-type: application/json' 127.0.0.1:8000/api/cards/<card-id> \
  -d '{"leases": ["src/app/**", "tests/**"]}'                    # replaces the whole list
```

## other common stops

| symptom | fix |
|---|---|
| `this tab has no api key` at the top of the page | open the link the board printed on start. |
| a card blocks on `no tests ran` | the card added no tests. each criterion needs one; re-run it with a note saying so. |
| preflight warns `has no tests yet` | ask mission control (`.`) for a first card that adds a test suite, or commit your own tests. |
| `.../card_token is not mode 600 - refusing to read it` | `chmod 600 ~/.config/smortboard/card_token` |
| an expired or revoked token (http 401) | `claude setup-token` or `codex login` again, then in `shift`+`p` add it as a new profile, activate it, remove the expired one. |
| the gate fails on `Read-only file system` for a tool other than ruff or pytest | give that tool its own cache flag: `--no-cache`, `-p no:cacheprovider`, or whatever it takes. |
| a card fails a test its own diff never touches | preflight (`h`) may show its repo image predates `uv.lock`. the next run rebuilds it. |
| a card waits with `lease conflict with card <id>` | two leases overlap. it starts when the other card finishes. |
| no new runs start | check the usage window (`u`), the board's daily budget (`shift`+`o`), and what usage limits does in `o`: the default waits for the reset. |
| cards were `CRASH`ed on start-up | the board was stopped mid-run. they wait in the inbox to be resumed. |

anything else: [open a bug report](https://github.com/BalthazarFitzpatrick/smortboard/issues).
[beta and reporting](beta.md) says what to include.
