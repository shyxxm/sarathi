(() => {
  // Audio lives outside the polled HTML: swapping the reply must neither
  // interrupt playback nor synthesise/speak that reply again.
  const replies = new Map();
  let currentId;
  let recording = false;
  const panel = () => document.querySelector('.reply-audio');

  function paint(id) {
    const node = panel(), entry = replies.get(id);
    if (!node || node.dataset.replyId !== id) return;
    const button = node.querySelector('[data-replay-audio]');
    button.hidden = !entry.audio;
    button.textContent = entry.audio && !entry.audio.paused ? 'Pause reply' : 'Replay reply';
    node.querySelector('[data-audio-status]').textContent = entry.status;
  }

  async function play(id) {
    const entry = replies.get(id);
    if (recording || currentId !== id || !entry?.audio) return;
    entry.audio.currentTime = 0;
    try {
      await entry.audio.play();
      entry.status = '';
    } catch (error) {
      // Autoplay can be refused even after a form submission. Keep a real
      // user-gesture playback control beside the text.
      entry.status = error.name === 'NotAllowedError' ? 'Tap Replay reply to listen.' : 'Audio could not play. Your reply is above.';
    }
    paint(id);
  }

  function sync() {
    const node = panel(), id = node?.dataset.replyId;
    if (id !== currentId) {
      replies.get(currentId)?.audio?.pause();
      currentId = id;
    }
    if (!id) return;
    if (replies.has(id)) return paint(id);
    const entry = {audio: null, status: ''};
    replies.set(id, entry);
    fetch(node.dataset.audioUrl)
      .then(async response => {
        if (response.status === 204) return;
        if (!response.ok) throw new Error('Audio unavailable');
        const url = URL.createObjectURL(await response.blob());
        entry.audio = new Audio(url);
        for (const event of ['play', 'pause', 'ended']) {
          entry.audio.addEventListener(event, () => paint(id));
        }
        entry.audio.addEventListener('error', () => {
          entry.status = 'Audio could not play. Your reply is above.';
          paint(id);
        });
        await play(id);
      })
      .catch(() => { entry.status = 'Audio unavailable. Your reply is above.'; })
      .finally(() => paint(id));
  }

  document.body.addEventListener('click', event => {
    if (!event.target.closest('[data-replay-audio]')) return;
    const id = panel()?.dataset.replyId, entry = replies.get(id);
    if (!entry?.audio) return;
    if (!entry.audio.paused) entry.audio.pause();
    else play(id);
  });
  // htmx:load covers normal swaps and the out-of-band reply after sending.
  document.body.addEventListener('htmx:load', sync);
  document.body.addEventListener('sarathi:recording', event => {
    recording = event.detail.active;
    if (recording) replies.get(currentId)?.audio?.pause();
  });
  sync();
})();
