(() => {
  const openSources = new Set();
  const error = document.getElementById("connection-error");

  function wireDetails(root = document) {
    root.querySelectorAll("details[data-detail-key]").forEach((detail) => {
      if (detail.dataset.wired) return;
      detail.dataset.wired = "true";
      if (openSources.has(detail.dataset.detailKey)) detail.open = true;
      detail.addEventListener("toggle", () => {
        if (!detail.isConnected || detail.dataset.hoverOpened) return;
        if (detail.open) openSources.add(detail.dataset.detailKey);
        else openSources.delete(detail.dataset.detailKey);
      });
      if (!detail.classList.contains("citation")) return;
      detail.addEventListener("pointerenter", (event) => {
        if (event.pointerType !== "mouse" || detail.open) return;
        detail.dataset.hoverOpened = "true";
        detail.open = true;
      });
      detail.addEventListener("pointerleave", () => {
        if (!detail.dataset.hoverOpened) return;
        delete detail.dataset.hoverOpened;
        detail.open = false;
      });
      detail.querySelector("summary").addEventListener("click", (event) => {
        if (!detail.dataset.hoverOpened) return;
        event.preventDefault();
        delete detail.dataset.hoverOpened;
        openSources.add(detail.dataset.detailKey);
      });
    });
  }

  document.getElementById("replay-minute")?.addEventListener("input", (event) => {
    const minute = 480 + Number(event.target.value);
    const time = `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
    document.getElementById("replay-time").value = time;
    event.target.setAttribute("aria-valuetext", time);
  });
  document.body.addEventListener("htmx:afterSwap", () => wireDetails());
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    // Keep a source readable while someone is inspecting or selecting it.
    if (event.detail.requestConfig.verb !== "get") return;
    const target = event.detail.target;
    if (target.querySelector(".citation:hover, .citation:focus-within") ||
        (window.getSelection()?.toString() && target.contains(window.getSelection().anchorNode))) {
      event.detail.shouldSwap = false;
    }
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    if (event.detail.successful) error.hidden = true;
  });
  for (const name of ["htmx:sendError", "htmx:responseError", "htmx:timeout"]) {
    document.body.addEventListener(name, () => { error.hidden = false; });
  }
  wireDetails();
})();
