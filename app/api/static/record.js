(() => {
  const panel = document.getElementById('voice-composer');
  if (!panel) return;
  const record = document.getElementById('record-voice');
  const stop = document.getElementById('stop-voice');
  const cancel = document.getElementById('cancel-voice');
  const send = document.getElementById('send-voice');
  const preview = document.getElementById('voice-preview');
  const download = document.getElementById('download-voice');
  const status = document.getElementById('voice-status');
  let recorder, stream, chunks = [], blob, url, messageId, timer;
  let recording = false, sending = false, requesting = false, discarded = false;
  let generation = 0;
  const types = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'];
  const mimeType = typeof MediaRecorder !== 'undefined' && types.find(type => MediaRecorder.isTypeSupported(type));
  const ready = window.isSecureContext && navigator.mediaDevices?.getUserMedia && mimeType;
  record.disabled = !ready;
  if (!ready) status.textContent = 'Recording is unavailable in this browser. Please use the typed message box.';

  function mic(active) {
    document.body.dispatchEvent(new CustomEvent('sarathi:recording', {detail: {active}}));
  }
  function release() {
    clearTimeout(timer);
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
    recording = false;
    stop.hidden = true;
    mic(false);
  }
  function forget() {
    preview.pause();
    preview.removeAttribute('src');
    if (url) URL.revokeObjectURL(url);
    url = blob = messageId = null;
    preview.hidden = download.hidden = send.hidden = cancel.hidden = true;
  }
  function finish() {
    if (recorder?.state === 'recording') recorder.stop();
  }

  record.addEventListener('click', async () => {
    if (recording || sending || requesting) return;
    const mine = ++generation;
    forget();
    discarded = false;
    requesting = true;
    record.disabled = true;
    cancel.hidden = false;
    mic(true);
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      }});
      if (discarded) return release();
      chunks = [];
      recorder = new MediaRecorder(stream, {mimeType});
      recorder.addEventListener('dataavailable', event => {
        if (mine === generation && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener('error', () => {
        if (mine !== generation) return;
        discarded = true;
        release();
        record.disabled = false;
        forget();
        status.textContent = 'Recording failed. Nothing was sent. Please type or record again.';
      });
      recorder.addEventListener('stop', () => {
        if (mine !== generation) return;
        release();
        record.disabled = false;
        if (discarded) return;
        blob = new Blob(chunks, {type: recorder.mimeType});
        if (!blob.size || blob.size > 8 * 1024 * 1024) {
          forget();
          status.textContent = 'The recording was empty or too large. Please type or record again.';
          return;
        }
        messageId = crypto.randomUUID();
        url = URL.createObjectURL(blob);
        preview.src = download.href = url;
        download.download = `voice-note.${blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'}`;
        preview.hidden = download.hidden = send.hidden = false;
        status.textContent = 'Listen if you want, then send your voice note.';
      });
      recorder.start();
      recording = true;
      stop.hidden = false;
      status.textContent = 'Recording… stops automatically after 25 seconds.';
      timer = setTimeout(finish, 25000);
    } catch (error) {
      release();
      forget();
      status.textContent = 'Microphone unavailable. Nothing was sent. Please allow microphone access or type below.';
    } finally {
      requesting = false;
      if (!recording) record.disabled = false;
    }
  });
  stop.addEventListener('click', finish);
  cancel.addEventListener('click', () => {
    discarded = true;
    generation++;
    finish();
    release();
    forget();
    record.disabled = requesting || sending;
    status.textContent = 'Recording discarded. Nothing was sent.';
  });
  send.addEventListener('click', async () => {
    if (!blob || sending || recording) return;
    if (document.querySelector('#composer form.htmx-request')) {
      status.textContent = 'Your typed message is still being sent. Your recording is kept.';
      return;
    }
    sending = true;
    record.disabled = send.disabled = cancel.disabled = true;
    preview.pause();
    status.textContent = 'Sending your voice note…';
    try {
      const response = await fetch(`/driver/voice-messages/${messageId}`, {
        method: 'POST', headers: {'Content-Type': blob.type}, body: blob,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The voice note could not be sent. Please type or try again.');
      forget();
      status.textContent = 'Voice note sent. Check the transcript with your reply.';
      htmx.trigger(document.body, 'shiftUpdated');
    } catch (error) {
      // Keep both the original recording and the message id for a safe retry
      // if the response was lost after the report was already recorded.
      status.textContent = error instanceof TypeError ? 'Connection interrupted. Your recording is kept. Try sending again.' : error.message;
    } finally {
      sending = false;
      record.disabled = !ready;
      send.disabled = cancel.disabled = false;
    }
  });
  document.body.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.requestConfig?.path !== '/driver/messages') return;
    if (sending) {
      event.preventDefault();
      status.textContent = 'Your voice note is still being sent. Your typed message is kept.';
    }
  });
  window.addEventListener('pagehide', () => { discarded = true; finish(); release(); });
})();
