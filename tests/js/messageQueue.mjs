// messageQueue: a local outbound queue that persists to localStorage before sending, retries a
// failed send with backoff rather than dropping it, and keeps messages in the order they were
// written even across a retry. run: node tests/js/messageQueue.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// one shared localStorage-backed store for the whole file, same as a single browser tab would have
installStubDom({});

// fresh module scope per call, so each test block gets its own `sequence` counter and does not
// leak state into the next - except the reload test, which deliberately calls this once and
// constructs two queues from it to share the same localStorage-backed closures
const loadQueue = () => new Function(`${smort('messageQueue.js')}\n;return createMessageQueue;`)();

// ---- a failed send stays queued and is delivered once the request succeeds, in order -----------
{
  const createMessageQueue = loadQueue();
  const calls = [];
  let failFirstOnce = true;
  const send = body => {
    calls.push(body);
    if (body === 'first' && failFirstOnce) { failFirstOnce = false; return Promise.reject(new Error('offline')); }
    return Promise.resolve({ok: true});
  };
  const seenStates = new Set();
  const queue = createMessageQueue('board-a', send, {
    onChange: items => items.forEach(it => seenStates.add(it.state)),
    backoffMs: [5],
  });

  queue.enqueue('first');
  queue.enqueue('second');
  await new Promise(r => setTimeout(r, 30)); // past the backoff - both should have landed by now

  assert.deepEqual(calls, ['first', 'first', 'second'],
    'first is retried before second is attempted at all, so order survives the retry');
  assert.deepEqual(queue.items(), [], 'both messages are gone once delivered - nothing left queued');
  assert.ok(seenStates.has('pending'), 'a queued-but-not-yet-attempted message shows pending');
  assert.ok(seenStates.has('sending'), 'an in-flight message shows sending');
  assert.ok(seenStates.has('failed'), 'a failed attempt shows failed rather than vanishing');
  assert.ok(seenStates.has('sent'), 'a delivered message shows sent before it is cleared');
}

// ---- a 409-shaped rejection (mission control still thinking) is treated the same as any other
// transient failure: retried, never dropped -------------------------------------------------------
{
  const createMessageQueue = loadQueue();
  let attempts = 0;
  const send = () => {
    attempts += 1;
    if (attempts < 3) return Promise.reject(new Error('still thinking'));
    return Promise.resolve({ok: true});
  };
  const queue = createMessageQueue('board-b', send, {backoffMs: [5, 5, 5]});
  queue.enqueue('are you there');
  await new Promise(r => setTimeout(r, 60));
  assert.equal(attempts, 3, 'a repeatedly-refused send keeps retrying rather than giving up');
  assert.deepEqual(queue.items(), [], 'it is delivered once mission control stops refusing');
}

// ---- a reload keeps unsent messages: a fresh queue instance against the same key picks up
// whatever localStorage still has, in the order it was written -------------------------------------
{
  const createMessageQueue = loadQueue();
  const send = () => new Promise(() => {}); // never resolves - nothing here should count as sent
  const queue = createMessageQueue('board-reload', send, {backoffMs: [10_000]});
  queue.enqueue('unsent one');
  queue.enqueue('unsent two');

  // a fresh instance against the same key stands in for the page reload
  const reloaded = createMessageQueue('board-reload', send, {backoffMs: [10_000]});
  const items = reloaded.items();
  assert.equal(items.length, 2, 'both unsent messages survive the reload');
  assert.deepEqual(items.map(i => i.body), ['unsent one', 'unsent two'], 'in the order they were written');
  assert.ok(items.every(i => i.state === 'pending' || i.state === 'sending'),
    'neither message is left in a dropped or stuck state');
  queue.stop();
  reloaded.stop();
}

// ---- two tabs on one board: every message is delivered exactly once, however the tabs overlap.
// this is the loop that re-sent mission control messages every turn: each tab kept its own copy
// of the queue and wrote it back over the one the other tab had just emptied -----------------------
{
  const createMessageQueue = loadQueue();
  const delivered = [];
  let busy = true;
  setTimeout(() => { busy = false; }, 40);
  // one server both tabs talk to: busy while a turn runs, then accepting
  const send = body => {
    if (busy) return Promise.reject(Object.assign(new Error('a turn is in progress'), {busy: true}));
    delivered.push(body);
    return Promise.resolve({ok: true});
  };
  const opts = {backoffMs: [5], busyRetryMs: 5};
  const tabOne = createMessageQueue('board-two-tabs', send, opts);
  tabOne.enqueue('first');
  tabOne.enqueue('second');
  // tab two loads while both are still queued
  const tabTwo = createMessageQueue('board-two-tabs', send, opts);
  await new Promise(r => setTimeout(r, 200));
  assert.deepEqual(delivered, ['first', 'second'], 'each message reaches the server once, in order');
  assert.deepEqual(tabOne.items(), [], 'tab one sees an empty queue');
  assert.deepEqual(tabTwo.items(), [], 'and so does tab two - nothing left to send again');
  tabOne.stop();
  tabTwo.stop();
}

// ---- a busy refusal is waiting, not failing: the message shows pending, never failed ------------
{
  const createMessageQueue = loadQueue();
  let refusals = 2;
  const send = () => {
    if (refusals > 0) {
      refusals -= 1;
      return Promise.reject(Object.assign(new Error('a turn is in progress'), {busy: true}));
    }
    return Promise.resolve({ok: true});
  };
  const seen = new Set();
  const queue = createMessageQueue('board-busy', send, {
    onChange: items => items.forEach(it => seen.add(it.state)),
    busyRetryMs: 5,
  });
  queue.enqueue('while thinking');
  await new Promise(r => setTimeout(r, 60));
  assert.ok(!seen.has('failed'), 'a turn in progress never paints the message as failed');
  assert.deepEqual(queue.items(), [], 'it goes out once the turn is over');
  queue.stop();
}

// ---- a redraw that throws does not turn a delivered message into a failed one -------------------
{
  const createMessageQueue = loadQueue();
  const delivered = [];
  const send = body => { delivered.push(body); return Promise.resolve({ok: true}); };
  const realError = console.error;
  console.error = () => {};
  try {
    const queue = createMessageQueue('board-throwing-redraw', send, {
      onChange: items => { if (items.some(it => it.state === 'sent')) throw new Error('redraw broke'); },
      backoffMs: [5],
    });
    queue.enqueue('only once');
    await new Promise(r => setTimeout(r, 40));
    assert.deepEqual(delivered, ['only once'], 'a broken redraw does not cause a second send');
    assert.deepEqual(queue.items(), [], 'the delivered message still leaves the queue');
    queue.stop();
  } finally {
    console.error = realError;
  }
}

// ---- localStorage being unavailable does not throw - the queue still works in memory ------------
{
  const createMessageQueue = loadQueue();
  const realLocalStorage = globalThis.localStorage;
  globalThis.localStorage = {
    getItem() { throw new Error('storage disabled'); },
    setItem() { throw new Error('storage disabled'); },
  };
  try {
    const send = () => Promise.resolve({ok: true});
    const queue = createMessageQueue('board-no-storage', send, {backoffMs: [5]});
    assert.doesNotThrow(() => queue.enqueue('still works'));
    await new Promise(r => setTimeout(r, 10));
    assert.deepEqual(queue.items(), [], 'a message still sends even though nothing could persist');
  } finally {
    globalThis.localStorage = realLocalStorage;
  }
}

console.log('ok');
