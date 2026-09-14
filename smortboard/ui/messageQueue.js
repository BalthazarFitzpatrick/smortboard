// a local outbound queue shared by every tab: localStorage holds the one copy, each tab reads it
// before acting and changes it by id, so a message one tab delivered is never sent again by another.
// one send in flight at a time, in the order messages were written, retried until it lands.
// plain global script, same as every other file in ui/ - no module system in this project.

const QUEUE_KEY_PREFIX = 'smortboard-outbox-';

// null means storage is unavailable (private browsing, quota, disabled) - the queue then runs from
// memory, for this tab alone
function readQueue(key) {
  try {
    const raw = localStorage.getItem(QUEUE_KEY_PREFIX + key);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return null;
  }
}

function writeQueue(key, items) {
  try {
    localStorage.setItem(QUEUE_KEY_PREFIX + key, JSON.stringify(items));
  } catch (err) {
    // best effort only - the queue still drains this session even if nothing persists
  }
}

// backoff for a failed send, in ms; the last step repeats for every attempt past it
const RETRY_BACKOFF_MS = [500, 1000, 2000, 5000, 15000];
// a busy refusal (mission control mid-turn) is waiting, not failing: check again at this pace
const BUSY_RETRY_MS = 2000;
// a message another tab marked as sending is left to it; past this age that tab is taken to be gone
const SENDING_STALE_MS = 60000;

let sequence = 0;
// unique across tabs as well, since the server uses it to ignore a message it already accepted
function nextQueueId() {
  sequence += 1;
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}-${sequence}`;
}

// a throwing redraw must not decide a message's fate
function notify(fn, ...args) {
  try {
    fn?.(...args);
  } catch (err) {
    console.error('message queue callback failed', err);
  }
}

// one outbound queue, keyed by `key` (mission control keys it per board). `send(body, id)` returns
// a promise: resolve on success, reject on failure. a rejection carrying `busy: true` (a 409 while
// mission control is mid-turn) keeps the message pending instead of marking it failed.
// onChange fires with the shared snapshot after every change, this tab's or another tab's.
// onSent fires once a message is delivered and already off the queue.
function createMessageQueue(key, send, {
  onChange, onSent, backoffMs = RETRY_BACKOFF_MS, busyRetryMs = BUSY_RETRY_MS,
} = {}) {
  const owner = nextQueueId();
  let memory = [];
  let inFlight = false;
  let retryTimer = null;

  function load() {
    const stored = readQueue(key);
    return stored === null ? memory : stored;
  }

  function save(items) {
    memory = items;
    writeQueue(key, items);
    notify(onChange, items.slice());
  }

  function change(id, patch) {
    save(load().map(it => (it.id === id ? {...it, ...patch} : it)));
  }

  function retryIn(ms) {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(pump, ms);
  }

  function enqueue(body) {
    save([...load(), {id: nextQueueId(), body, state: 'pending', attempts: 0}]);
    pump();
  }

  // FIFO and single-flight across tabs: only the head is ever sent, and a head another live tab is
  // already sending is left to that tab
  async function pump() {
    if (inFlight) return;
    const head = load()[0];
    if (!head) return;
    const othersSending = head.state === 'sending' && head.owner !== owner
      && Date.now() - (head.since || 0) < SENDING_STALE_MS;
    if (othersSending) { retryIn(busyRetryMs); return; }

    inFlight = true;
    change(head.id, {state: 'sending', owner, since: Date.now()});
    let result;
    try {
      result = await send(head.body, head.id);
    } catch (err) {
      inFlight = false;
      const attempts = (head.attempts || 0) + 1;
      if (err && err.busy) {
        change(head.id, {state: 'pending', attempts});
        retryIn(busyRetryMs);
      } else {
        change(head.id, {state: 'failed', attempts});
        retryIn(backoffMs[Math.min(attempts - 1, backoffMs.length - 1)]);
      }
      return;
    }
    change(head.id, {state: 'sent'});
    save(load().filter(it => it.id !== head.id));
    inFlight = false;
    notify(onSent, result, head.body);
    pump();
  }

  // another tab changed the shared queue: redraw it here, and take over a head that tab left behind
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('storage', evt => {
      if (evt.key !== QUEUE_KEY_PREFIX + key) return;
      notify(onChange, load());
      pump();
    });
  }

  pump(); // resume whatever a reload left queued, without waiting for the next enqueue
  return {
    enqueue,
    items: () => load().slice(),
    stop: () => clearTimeout(retryTimer),
  };
}
