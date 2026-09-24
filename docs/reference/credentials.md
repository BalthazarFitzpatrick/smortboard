# credential files

**`shift`+`p` is the whole setup.** adding a profile writes its file at mode 600, and the board
refuses to read one anyone else can. where each one lands:

| what | where |
|---|---|
| the original single claude token | `~/.config/smortboard/card_token`, or `%APPDATA%\smortboard\card_token` on windows |
| any named profile | `~/.config/smortboard/tokens/<lab>/<name>`, `<lab>` being `anthropic` or `openai` |

`$XDG_CONFIG_HOME` is honoured where set.

an openai profile holds the whole `~/.codex/auth.json` or an api key, per the kind chosen when it
was added. a bare access token pulled out of that json is refused, as measured in
[the credential spike](../spikes/s7-codex-auth.md).

- **elsewhere:** `SMORTBOARD_CARD_TOKEN_PATH` moves the `default` profile's file. credentials are
  read only from these files.
- **several subscriptions, or both labs:** one profile per credential in `shift`+`p`, each under
  its own name. when the active one hits a usage limit, what happens is one setting in `o`: wait
  for the reset (the default), ask me, or switch by itself. switch moves to the next profile of the
  same lab, then to the role's [fallback model](../practices/labs.md). crossing labs needs that
  fallback list, on purpose.

neither `claude setup-token` nor `codex login` hands its credential to the board, so the board keeps
its own copy: a model-only token, not your login.
