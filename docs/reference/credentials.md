# Credential files

`shift`+`p` is the whole setup. Adding a profile writes its file at mode 600, and the board refuses
to read one anyone else can. For reference:

| What | Where |
|---|---|
| the original single Claude token | `~/.config/smortboard/card_token`, or `%APPDATA%\smortboard\card_token` on Windows |
| any named profile | `~/.config/smortboard/tokens/<lab>/<name>`, `<lab>` being `anthropic` or `openai` |

`$XDG_CONFIG_HOME` is honoured where set.

An OpenAI profile holds the whole `~/.codex/auth.json` or an API key, per the kind chosen when it
was added. A bare access token pulled out of that JSON is refused, as measured in
[the credential spike](../spikes/S7-codex-auth.md).

- **Elsewhere:** `SMORTBOARD_CARD_TOKEN_PATH` moves the `default` profile's file. Credentials are
  read only from these files.
- **Several subscriptions, or both labs:** one profile per credential in `shift`+`p`, each under
  its own name. When the active one is rate-limited, the board rotates to the next profile of the
  same lab - only with `usage_limit_route` set to `switch` in settings; otherwise new
  starts wait for the reset. Crossing to the other lab needs a [fallback list per role](../practices/labs.md), on
  purpose.

Neither `claude setup-token` nor `codex login` hands its credential to the board. So the board keeps
its own copy: a model-only token, not your login.
