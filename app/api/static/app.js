(() => {
  const openDetails = new Set();
  const error = document.getElementById('connection-error');
  const slider = document.getElementById('replay-minute');
  const play = document.getElementById('play-shift');
  let playing = false, timer, scrubTimer, beforeCards = new Map();
  const timeText = value => {
    const minutes = 480 + Number(value);
    return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
  };
  function wireDetails() {
    document.querySelectorAll('details[data-detail-key]').forEach(detail => {
      if (detail.dataset.wired) return;
      detail.dataset.wired = 'true';
      detail.open = openDetails.has(detail.dataset.detailKey);
      detail.addEventListener('toggle', () => {
        if (!detail.isConnected) return;
        if (detail.open) openDetails.add(detail.dataset.detailKey);
        else openDetails.delete(detail.dataset.detailKey);
      });
    });
  }
  function label(value) {
    if (!slider) return;
    document.getElementById('replay-time').value = timeText(value);
    slider.setAttribute('aria-valuetext', timeText(value));
  }
  function stop() {
    playing = false;
    clearTimeout(timer);
    if (play) {
      play.textContent = '▶ Play shift';
      play.setAttribute('aria-pressed', 'false');
    }
  }
  function seek(value) {
    slider.value = value;
    label(value);
    htmx.trigger(document.getElementById('replay-form'), 'change');
  }
  function step() {
    if (!playing) return;
    const now = Number(document.getElementById('board').dataset.minute);
    if (now >= 600) return stop();
    // Stop exactly at each meaningful boundary; the watchdog must not flash past.
    const landmarks = [5,60,61,90,91,132,133,193,194,215,216,217,240,242,250,251,341,342,375,376,390,391,415,416,480,481,525,526,600];
    const next = landmarks.find(value => value > now);
    seek(Math.min(now + 10, next ?? 600));
  }
  function syncClock() {
    const board = document.getElementById('board');
    if (!slider || !board) return;
    slider.value = board.dataset.minute;
    label(slider.value);
    document.getElementById('clock-state').textContent = board.dataset.replay === 'true' ? 'REPLAY' : 'CURRENT SHIFT';
    document.querySelectorAll('.landmark').forEach(mark => {
      mark.classList.toggle('active', mark.dataset.minute === board.dataset.minute);
      if (mark.dataset.minute === board.dataset.minute) mark.setAttribute('aria-current', 'time');
      else mark.removeAttribute('aria-current');
    });
  }
  slider?.addEventListener('input', () => {
    stop();
    label(slider.value);
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => seek(slider.value), 100);
  });
  slider?.addEventListener('change', () => clearTimeout(scrubTimer));
  play?.addEventListener('click', () => {
    if (playing) return stop();
    playing = true;
    play.textContent = 'Ⅱ Pause';
    play.setAttribute('aria-pressed', 'true');
    if (Number(slider.value) >= 600) seek(0);
    else step();
  });
  document.querySelectorAll('.landmark, .current-shift').forEach(link => link.addEventListener('click', stop));
  document.body.addEventListener('htmx:beforeSwap', event => {
    const target = event.detail.target;
    if (target.id === 'board') {
      beforeCards = new Map([...target.querySelectorAll('.exception-card[id], .fine-card[id]')].map(card => [card.id, card.dataset.status || 'fine']));
      // Pause passive polling during inspection; clock navigation still takes effect.
      const isPoll = event.detail.requestConfig?.triggeringEvent?.type === 'every';
      if (isPoll && (playing || target.querySelector('details[open]'))) event.detail.shouldSwap = false;
    }
    if (event.detail.requestConfig.verb === 'get' && window.getSelection()?.toString() && target.contains(window.getSelection().anchorNode)) event.detail.shouldSwap = false;
  });
  document.body.addEventListener('htmx:afterSwap', event => {
    wireDetails();
    if (event.detail.target.id !== 'board') return;
    syncClock();
    document.querySelectorAll('#board .exception-card[id], #board .fine-card[id]').forEach(card => {
      if (!beforeCards.has(card.id)) card.classList.add('card-enter');
      else if (beforeCards.get(card.id) !== (card.dataset.status || 'fine')) card.classList.add('card-moved');
    });
    if (playing) {
      clearTimeout(timer);
      const minute = Number(slider.value);
      timer = setTimeout(step, [133,194,342,376,390].includes(minute) ? 2200 : 450);
    }
  });
  document.body.addEventListener('htmx:afterRequest', event => {
    if (event.detail.successful) error.hidden = true;
  });
  for (const name of ['htmx:sendError', 'htmx:responseError', 'htmx:timeout']) {
    document.body.addEventListener(name, () => { error.hidden = false; stop(); });
  }
  wireDetails();
  syncClock();
})();
