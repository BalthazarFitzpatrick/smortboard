// a local outbound message queue: every message is written to localStorage before it is sent, so
// a request that fails (dropped network, mission control still thinking) leaves the message
// queued rather than losing it. one send in flight at a time, in the order messages were written,
// retried with backoff until it lands - nothing here drops a message on a transient failure.
// plain global script, same as every other file in ui/ - no module system in this project.

const QUEUE_KEY_PREFIX = 'smortboard-outbox-';

// wrapped everywhere it touches storage - localStorage can be unavailable (private browsing,
// quota, disabled entirely) and the queue still has to work in memory when it is
function readQueue(key) {
  try {
    const raw = localStorage.getItem(QUEUE_KEY_PREFIX + key);
    return raw ? JSON.parse(raw) : [];
  } catch (err) {
    return [];
  }
}

function writeQueue(key, items) {
  try {
    localStorage.setItem(QUEUE_KEY_PREFIX + key, JSON.stringify(items));
  } catch (err) {
    // best effort only - the queue still drains this session even if nothing persists
  }
}

// backoff for a retried send, in ms; the last step repeats for every attempt past it
const RETRY_BACKOFF_MS = [500, 1000, 2000, 5000, 15000];

let sequence = 0;
function nextQueueId() {
  sequence += 1;
  return `${Date.now()}-${sequence}`;
}

// one outbound queue, keyed by `key` (mission control keys it per board, so a reload rebuilds the
// right backlog for whichever board it is opened against). `send(body)` must return a promise:
// resolve on success, reject on any failure - a network error or a non-2xx, 409 included - so the
// message is retried rather than dropped.
// onChange fires with a snapshot after every state change, including the transient 'sent' state.
// onSent fires once a message is fully delivered and already removed from the queue - the caller's
// hook for redrawing the confirmed transcript without a queued line still on screen to duplicate it
function createMessageQueue(key, send, {onChange, onSent, backoffMs = RETRY_BACKOFF_MS} = {}) {
  // a queue loaded mid-send was never actually sending - nothing was in flight when the page
  // reloaded, so it resumes as pending rather than stuck
  let items = readQueue(key).map(it => (it.state === 'sending' ? {...it, state: 'pending'} : it));
  let inFlight = false;
  let retryTimer = null;

  function emit() {
    writeQueue(key, items);
    onChange?.(items.slice());
  }

  function enqueue(body) {
    items.push({id: nextQueueId(), body, state: 'pending', attempts: 0});
    emit();
    pump();
  }

  // FIFO and single-flight: the next send only starts once the head of the queue has landed, so
  // messages arrive in the order they were written even across retries
  async function pump() {
    if (inFlight || !items.length) return;
    const item = items[0];
    inFlight = true;
    item.state = 'sending';
    emit();
    try {
      const result = await send(item.body);
      item.state = 'sent';
      emit();
      items.shift();
      inFlight = false;
      emit();
      onSent?.(result, item.body);
      pump();
    } catch (err) {
      item.attempts += 1;
      item.state = 'failed';
      emit();
      const wait = backoffMs[Math.min(item.attempts - 1, backoffMs.length - 1)];
      retryTimer = setTimeout(() => { inFlight = false; pump(); }, wait);
    }
  }

  pump(); // resume whatever a reload left queued, without waiting for the next enqueue
  return {
    enqueue,
    items: () => items.slice(),
    stop: () => clearTimeout(retryTimer),
  };
}
