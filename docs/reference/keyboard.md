# Keyboard

Keyboard-first. Three rules carry the whole model:

- **`space` opens.** On a focused card it opens the card; on the open card it closes it.
- **`esc` goes one level back:** out of a text field, then out of the panel, then to the board.
- **`/` types.** It is the only way into typing. Opening a card or a drawer never steals focus, so
  every letter stays a board key until you press `/`.

What follows from that:

- A card's comment box exists only while the card is open. Open it with `space`, then `/`.
- An inbox card that shows its answer box takes `/` straight from focus.
- Settings has many fields: arrow to one first. `esc` closes the panel and saves the field.

Keys resolve on the physical key, so a non-US layout does not move them. `cmd`, `ctrl` and `alt`
belong to the browser (`cmd`+`c` copies), with one exception: `cmd`/`ctrl`+`f` on a board with
nothing open filters its cards, and `esc` clears the filter.

## Confirmations

Anything that starts, spends, lands or destroys asks once, with one button already focused:

- **`y`, `x` and `k` focus confirm.** A wrong accept or reject is undone from the card's menu
  (`m`), and a stopped run starts again. Being wrong costs a keypress.
- **`r`, `w`, `f`, and delete or change-model in the card menu focus cancel.** A stray `enter`
  cancels instead of spending money, starting a run or destroying something.

Reversible defaults to yes; expensive or destructive defaults to no.

`s` shows the same list in the app, paged left and right. Both come from `BINDINGS` in
`smortboard/ui/shortcuts.js`, and `tests/js/keyboard_docs.mjs` fails when a key there is missing
here.

## Cards and the board

| Key | Action |
|---|---|
| `up` | move focus up / exit to board bar |
| `down` | move focus down |
| `left` | move focus left |
| `right` | move focus right |
| `enter` | open the focused card |
| `space` | open the focused card, or close the open one |
| `esc` | one level back: input -> panel -> closed |
| `r` | run the focused card, with confirmation |
| `k` | stop the focused card if it is running |
| `y` | accept the focused card, with confirmation |
| `x` | reject the focused card, with confirmation |
| `m` | menu for the focused card: edit, model, complexity, move to, delete |
| `t` | run replay: scrub the focused card's run step by step |
| `/` | type: the open card's comment, or the open chat |
| `w` | run the board: start (with confirmation) / stop the queue |
| `f` | fold: merge the todo cards one agent should do as one (asks first) |

## Panels and boards

| Key | Action |
|---|---|
| `u` | usage: rate-limit windows and per-model spend |
| `i` | cost telemetry: card attempts, or the board cost table |
| `c` | cost overview: spend across every board |
| `a / shift+a` | agent roster / change board merge mode, with confirmation |
| `d` | morning digest: pull requests and open questions |
| `s` | this shortcut overlay |
| `p / shift+p` | p: orchestrator, worker and reviewer prompts. shift+p: credential profiles |
| `b` | boards and repos: create a board, register a repo |
| `n` | attention inbox: answer a blocked card, across every board |
| `h` | pre-flight checklist: what is missing before a card can run |
| `o / shift+o` | o: settings every board shares. shift+o: this board's own settings |
| `v` | pull requests: every open one across every board, in merge order |
| `q` | landing lock: who holds the push lock on each repo, and the queue behind them |
| `,` | workforce: chat with the focused card's agent |
| `.` | mission control: chat with the board orchestrator |
| `1 .. 9` | jump to board 1 .. 9 |

