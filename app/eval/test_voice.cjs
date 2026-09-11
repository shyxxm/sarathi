// Browser behaviour without dependencies: node --test app/eval/test_voice.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync(`${__dirname}/../api/static/voice.js`, 'utf8');
const settled = () => new Promise(resolve => setImmediate(resolve));

function browser() {
  const listeners = {}, requests = [], audios = [];
  let node;
  class Audio {
    constructor() { this.paused = true; this.plays = 0; this.events = {}; audios.push(this); }
    addEventListener(name, fn) { this.events[name] = fn; }
    async play() {
      this.plays++;
      if (this.blocked) throw Object.assign(new Error(), {name: 'NotAllowedError'});
      this.paused = false;
      this.events.play?.();
    }
    pause() { this.paused = true; this.events.pause?.(); }
  }
  function swap(id) {
    const button = {hidden: true}, status = {};
    node = {dataset: {replyId: id, audioUrl: `/audio/${id}`}, button, status,
      querySelector: selector => selector === '[data-replay-audio]' ? button : status};
    listeners['htmx:load']?.();
    return node;
  }
  swap('first');
  runInNewContext(source, {
    Audio, URL: {createObjectURL: () => 'blob:audio'},
    document: {querySelector: () => node, body: {addEventListener: (name, fn) => { listeners[name] = fn; }}},
    fetch: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
  });
  function respond(index, status = 200) {
    requests[index].resolve({status, ok: status === 200, blob: async () => 'wav'});
  }
  function click() { listeners.click({target: {closest: () => true}}); }
  return {requests, audios, swap, respond, click, current: () => node};
}

test('polling preserves audio, autoplay happens once, replay uses the same audio', async () => {
  const page = browser();
  page.respond(0);
  await settled();
  const audio = page.audios[0];
  assert.equal(audio.plays, 1);
  const replacement = page.swap('first');
  assert.equal(page.requests.length, 1);
  assert.equal(audio.paused, false);
  assert.equal(replacement.button.textContent, 'Pause reply');
  page.click();
  assert.equal(audio.paused, true);
  page.click();
  await settled();
  assert.equal(audio.plays, 2);
  assert.equal(page.requests.length, 1);
});

test('late audio for an older reply never plays over the new reply', async () => {
  const page = browser();
  page.swap('second');
  page.respond(0);
  await settled();
  assert.equal(page.audios[0].plays, 0);
  page.respond(1);
  await settled();
  assert.equal(page.audios[1].plays, 1);
  page.swap('third');
  assert.equal(page.audios[1].paused, true);
});

test('autoplay refusal leaves a working replay control', async () => {
  const page = browser();
  // Set refusal as soon as Audio is constructed, before play is awaited.
  const originalPush = page.audios.push;
  page.audios.push = function(audio) { audio.blocked = true; return originalPush.call(this, audio); };
  page.respond(0);
  await settled();
  assert.equal(page.current().button.hidden, false);
  assert.equal(page.current().status.textContent, 'Tap Replay reply to listen.');
  page.audios[0].blocked = false;
  page.click();
  await settled();
  assert.equal(page.audios[0].paused, false);
  assert.equal(page.current().status.textContent, '');
});

test('text-only fallback and failed fetches do not retry on polling', async () => {
  const page = browser();
  page.respond(0, 204);
  await settled();
  page.swap('first');
  assert.equal(page.current().button.hidden, true);
  assert.equal(page.requests.length, 1);
  page.swap('second');
  page.requests[1].reject(new Error('offline'));
  await settled();
  page.swap('second');
  assert.equal(page.requests.length, 2);
  assert.equal(page.current().status.textContent, 'Audio unavailable. Your reply is above.');
});
