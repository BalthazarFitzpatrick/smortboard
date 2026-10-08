// what each setting is and what its choices mean, for the floating help box that follows the
// focused row in the settings panels. one entry per row id; the panels tag a row with the same id
// (data-help) and the box reads it from here. words only: no markup, no dom
//
// {title, lines}: the title is the row's name, the lines say what it is, then what each choice does

const SETTINGS_HELP = {
  // ---- general panel (o) ----------------------------------------------------------------------
  preset: {
    title: 'mode preset',
    lines: [
      'sets the gates, how many cards run at once and the spend caps together.',
      'careful: everything off, one card at a time, low caps. balanced: today\'s defaults. fast: free merge and soft file leases on, four cards at once.',
      'custom lights by itself when your values match none of them. it is not a choice.',
    ],
  },
  allow_soft_leases: {
    title: 'soft file leases',
    lines: [
      'lets a board choose soft file leases instead of strict.',
      'on: a board set to soft lets its cards also write unprotected paths no other card holds.',
      'off: every board runs strict. what a board saved is kept and applies again when this is on.',
    ],
  },
  allow_free_merge: {
    title: 'free merge',
    lines: [
      'lets a board merge passing cards into its base by itself.',
      'on: a board set to free merges without waiting for you. off-limit branches are still never merged.',
      'off: every board opens a pull request and waits for you. what a board saved is kept.',
    ],
  },
  allow_open_mode: {
    title: 'open run mode',
    lines: [
      'lets a board run cards on your machine instead of in a container.',
      'on: a board set to open runs cards as plain processes, protected only by file leases and hooks, which is weaker than a container.',
      'off: every board runs sealed, in a container.',
    ],
  },
  off_limit_branches: {
    title: 'off-limit branches',
    lines: [
      'the global switch for every board\'s list of branches it never lands on.',
      'on: each board refuses its own list as a landing target.',
      'off: every board may land on any branch, main included. turning it off asks for a second click.',
    ],
  },
  max_parallel: {
    title: 'cards at once',
    lines: [
      'how many cards run at the same time across every board.',
      'empty: the default of 2. a board can only lower this for itself, never raise it.',
    ],
  },
  mall_cam_interval_seconds: {
    title: 'mall cam interval',
    lines: [
      'how often the workforce drawer cycles to the next card, in seconds.',
      'empty: 10.',
    ],
  },
  enable_mouse: {
    title: 'mouse',
    lines: [
      'adds the pointer shortcuts that mirror the keyboard.',
      'on: hovering focuses what the arrows would, and right-click opens a card\'s menu.',
      'off: keyboard only. clicks on buttons and panels always work.',
    ],
  },
  worker_budget_usd: {
    title: 'spend cap: worker',
    lines: ['the most one worker run may spend, in usd. empty: 5.00. a run that reaches it stops.'],
  },
  reviewer_budget_usd: {
    title: 'spend cap: reviewer',
    lines: ['the most one review may spend, in usd. empty: 1.50.'],
  },
  orchestrator_budget_usd: {
    title: 'spend cap: orchestrator',
    lines: ['the most one mission control turn may spend, in usd. empty: 1.00.'],
  },
  fold_budget_usd: {
    title: 'spend cap: fold',
    lines: ['the most one fold may spend, in usd. empty: 2.00.'],
  },
  card_total_budget_usd: {
    title: 'spend cap: card total',
    lines: ['the most one card may spend across every run, in usd. empty: no limit.'],
  },
  usage_limit_route: {
    title: 'usage limits',
    lines: [
      'what happens when a lab says you hit its usage limit.',
      'wait: the board parks until the limit resets. ask me: the card asks in the inbox before a fallback model is used.',
      'switch: it moves to your next credential profile, then to the role\'s fallback models, by itself.',
    ],
  },
  reviewer_input: {
    title: 'reviewer reads',
    lines: [
      'what the reviewer is given to judge a card.',
      'diff: only the change. code: the changed files in full, which costs more.',
    ],
  },
  models: {
    title: 'models by role',
    lines: [
      'the model a role uses and the fallbacks it moves to when a limit is hit.',
      'each row is one lab, model and effort. the first row is the primary, the rest are tried in order.',
      'fallbacks are only used when usage limits is set to switch.',
    ],
  },
  repos_home: {
    title: 'where new repos go',
    lines: ['the folder the folder picker opens in when you add a repo. empty: your home folder.'],
  },
  mission_control_read_paths: {
    title: 'mission control can read',
    lines: [
      'extra folders mission control may read, as absolute paths.',
      'it reads them read-only, alongside the repos of the board.',
    ],
  },
  backup: {
    title: 'backup',
    lines: [
      'export writes every board, card and setting to one file.',
      'import adds the boards in a file beside the ones you have. it never overwrites.',
    ],
  },
  findings_route: {
    title: 'findings route',
    lines: [
      'where a card goes when review finds problems.',
      'per card: each card decides. fix: the card is sent back to the worker. attention: it waits in the inbox for you.',
      'fix or attention here overrides every card.',
    ],
  },
  resume_briefing: {
    title: 'resume briefing',
    lines: [
      'whether a card that continues an earlier run is given a short briefing of what happened.',
      'on: it is. off: it starts without one.',
    ],
  },
  gate_timeout_seconds: {
    title: 'gate timeout',
    lines: ['how long the tests of a card may run before the gate gives up, in seconds. empty: 600.'],
  },

  // ---- board panel (shift+o) ------------------------------------------------------------------
  board_preset: {
    title: 'board preset',
    lines: [
      'sets merge mode, file lease and run mode together for this board.',
      'review only: review, strict, sealed. free merge: free, strict, sealed. open: review, soft, open.',
      'a preset is dashed until the switches it needs are on in general. custom lights by itself.',
    ],
  },
  merge_mode: {
    title: 'merge mode',
    lines: [
      'how a passing card reaches the base branch.',
      'review: a pull request is opened and waits for you. free: it merges by itself.',
      'free needs free merge on in general. off-limit branches are never merged either way.',
    ],
  },
  board_off_limit_branches: {
    title: 'off-limit branches',
    lines: [
      'the branches this board never lands a card on, comma separated.',
      'main, master and trunk until you change the list. empty means none.',
      'while the global switch in general is off, none of it applies.',
    ],
  },
  lease_mode: {
    title: 'file lease',
    lines: [
      'which files a card may write.',
      'strict: only inside its own lease. soft: also unprotected paths no other card holds, and each one is shown on the card.',
      'soft needs soft file leases on in general.',
    ],
  },
  run_mode: {
    title: 'run mode',
    lines: [
      'where this board\'s cards run.',
      'sealed: in a container. open: on your machine, protected only by file leases and hooks, which is weaker.',
      'open needs open run mode on in general.',
    ],
  },
  board_max_parallel: {
    title: 'cards at once',
    lines: [
      'how many of this board\'s cards may run together.',
      'empty: only the global limit applies. a number here can only lower it for this board.',
    ],
  },
  daily_budget_usd: {
    title: 'daily budget',
    lines: ['the most this board may spend per day, in usd (utc). new cards stop starting once it is reached. empty: no cap.'],
  },
};

// the entry for a row id, or null when a row has none (the box then stays hidden)
function helpFor(id) {
  return SETTINGS_HELP[id] || null;
}
