const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync(`${__dirname}/../api/static/record.js`, 'utf8');
const settled = () => new Promise(resolve => setImmediate(resolve));

function browser({microphoneError = false, supported = true, deferredPermission = false} = {}) {
  const elements = {}, listeners = {}, requests = [], recorders = [], events = [];
  let stopped = 0, uid = 0, grant, timeout;
  for (const id of ['voice-composer','record-voice','stop-voice','cancel-voice','send-voice','voice-preview','download-voice','voice-status']) {
    elements[id] = {hidden: false, disabled: false, events: {},
      addEventListener(name, fn) { this.events[name] = fn; },
      pause() {}, removeAttribute(name) { delete this[name]; }};
  }
  const stream = {getTracks: () => [{stop: () => stopped++}]};
  class MediaRecorder {
    static isTypeSupported(type) { return type === 'audio/webm;codecs=opus'; }
    constructor(stream, {mimeType}) { this.mimeType = mimeType; this.events = {}; this.state = 'inactive'; recorders.push(this); }
    addEventListener(name, fn) { this.events[name] = fn; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      setImmediate(() => {
        this.events.dataavailable({data: new Blob(['speech'])});
        this.events.stop();
      });
    }
  }
  runInNewContext(source, {
    Blob, TypeError, Error, MediaRecorder: supported ? MediaRecorder : undefined,
    URL: {createObjectURL: () => 'blob:note', revokeObjectURL() {}},
    crypto: {randomUUID: () => `message-${++uid}`},
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    navigator: {mediaDevices: {getUserMedia: async () => {
      if (microphoneError) throw new Error('permission refused');
      if (deferredPermission) return new Promise(resolve => { grant = () => resolve(stream); });
      return stream;
    }}},
    window: {isSecureContext: true, addEventListener: (name, fn) => { listeners[name] = fn; }},
    document: {getElementById: id => elements[id], querySelector: () => null,
      body: {addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent: event => events.push(event)}},
    htmx: {trigger: (node, event) => events.push({type: event})},
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    setTimeout: fn => { timeout = fn; return 1; }, clearTimeout() {},
  });
  const click = id => elements[id].events.click();
  return {elements, requests, recorders, events, click, stopped: () => stopped,
    grant: () => grant(), timeout: () => timeout(), listeners};
}

test('record, stop, preview, save and send original bytes; capture never sends automatically', async () => {
  const page = browser();
  await page.click('record-voice');
  assert.equal(page.requests.length, 0);
  assert.equal(page.events[0].detail.active, true);
  page.click('stop-voice');
  await settled();
  assert.equal(page.stopped(), 1);
  assert.equal(page.elements['voice-preview'].hidden, false);
  assert.equal(page.elements['download-voice'].download, 'voice-note.webm');
  const sending = page.click('send-voice');
  assert.equal(page.requests.length, 1);
  assert.equal(await page.requests[0].options.body.text(), 'speech');
  assert.equal(page.requests[0].options.headers['Content-Type'], 'audio/webm;codecs=opus');
  page.requests[0].resolve({ok: true, json: async () => ({reply_id: 'reply'})});
  await sending;
  assert.equal(page.elements['send-voice'].hidden, true);
  assert.ok(page.events.some(event => event.type === 'shiftUpdated'));
});

test('permission denial and unsupported recording leave typed fallback without a submission', async () => {
  const page = browser({microphoneError: true});
  await page.click('record-voice');
  assert.match(page.elements['voice-status'].textContent, /Microphone unavailable/);
  assert.equal(page.requests.length, 0);
  assert.equal(page.elements['record-voice'].disabled, false);
  const unsupported = browser({supported: false});
  assert.equal(unsupported.elements['record-voice'].disabled, true);
  assert.match(unsupported.elements['voice-status'].textContent, /typed message box/);
});

test('cancel while microphone permission is pending releases the late stream', async () => {
  const page = browser({deferredPermission: true});
  const starting = page.click('record-voice');
  page.click('cancel-voice');
  page.grant();
  await starting;
  assert.equal(page.stopped(), 1);
  assert.equal(page.recorders.length, 0);
  assert.equal(page.requests.length, 0);
});

test('a late stop from a discarded recording cannot replace a new recording', async () => {
  const page = browser();
  await page.click('record-voice');
  page.click('cancel-voice');
  await page.click('record-voice');
  await settled();
  assert.equal(page.elements['send-voice'].hidden, true);
  assert.equal(page.recorders[1].state, 'recording');
  page.timeout();
  await settled();
  assert.equal(page.elements['send-voice'].hidden, false);
  assert.equal(page.requests.length, 0);
});

test('STT failure is an error, with the same recording and id retained for retry', async () => {
  const page = browser();
  await page.click('record-voice');
  page.click('stop-voice');
  await settled();
  const first = page.click('send-voice');
  page.requests[0].resolve({ok: false, json: async () => ({error: 'Voice transcription failed. Please type.'})});
  await first;
  assert.match(page.elements['voice-status'].textContent, /transcription failed/);
  assert.equal(page.elements['send-voice'].hidden, false);
  assert.ok(!page.events.some(event => event.type === 'shiftUpdated'));
  const second = page.click('send-voice');
  assert.equal(page.requests[0].url, page.requests[1].url);
  assert.equal(page.requests[0].options.body, page.requests[1].options.body);
  page.requests[1].resolve({ok: true, json: async () => ({})});
  await second;
});
