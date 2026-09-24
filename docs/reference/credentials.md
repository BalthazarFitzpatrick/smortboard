# Credential files

**`shift`+`p` is the whole setup.** Adding a profile writes its file at mode 600, and the board
refuses to read one anyone else can. Where each one lands:

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
  its own name. When the active one hits a usage limit, what happens is one setting in `o`: wait
  for the reset (the default), ask me, or switch by itself. Switch moves to the next profile of the
  same lab, then to the role's [fallback model](../practices/labs.md). Crossing labs needs that
  fallback list, on purpose.

Neither `claude setup-token` nor `codex login` hands its credential to the board, so the board keeps
its own copy: a model-only token, not your login.
