# keyboard

**keyboard first. three rules carry the model.**

- **`space` opens.** on a focused card it opens the card. on the open card it pops the focused
  section out over the card, and `space` or `esc` puts it back. on the card's title it closes the
  card.
- **`esc` goes one level back:** out of a text field, then out of the panel, then to the board.
- **`/` types.** it is the only way into typing. opening a card or a drawer never steals focus, so
  every letter stays a board key until you press `/`.

what follows:

- a card's note field exists only while the card is open, in its needs section: `space`, then `/`.
  popped out, needs brings the field along. `enter` sends.
- an inbox card that shows its answer box takes `/` straight from focus.
- in a panel the arrows follow its layout: up and down change rows, left and right move along one,
  across fields and buttons alike. `esc` closes the panel and saves the field.

keys resolve on the physical key, so a non-us layout does not move them. `cmd`, `ctrl` and `alt`
belong to the browser (`cmd`+`c` copies), with one exception: `cmd`/`ctrl`+`f` on a board with
nothing open filters its cards, and `esc` clears the filter.

## confirmations

anything that starts, spends, lands or destroys asks once, with one button already focused:

- **`y`, `x` and `k` focus confirm.** a wrong accept or reject is undone from the card's menu
  (`m`), and a stopped run starts again. being wrong costs a keypress.
- **`r`, `w`, `f`, and delete or change-model in the card menu focus cancel.** a stray `enter`
  cancels instead of spending money, starting a run or destroying something.

reversible defaults to yes. expensive or destructive defaults to no.

`s` shows the same list in the app, paged left and right. both come from `BINDINGS` in
`smortboard/ui/shortcuts.js`, and `tests/js/keyboard_docs.mjs` fails when a key there is missing
here.

## cards and the board

| key | action |
|---|---|
| `up` | move focus up / exit to board bar |
| `down` | move focus down |
| `left` | move focus left |
| `right` | move focus right |
| `enter` | open the focused card |
| `space` | open the focused card, or close it. on an open card: pop the focused section out, or back |
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

## panels and boards

| key | action |
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

