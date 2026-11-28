// Popovers anchored to a button: the ⋮ menu and the ⓘ credits (UI refresh spec 5.1).
// One open at a time; Esc or a click outside closes it and returns focus to its button.

const open = new Set();

/** Wire `button` to show and hide `panel`. Returns {open(), close()}. */
export function mountPopover(button, panel) {
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", panel.id);
  panel.hidden = true;

  const api = {
    open() {
      for (const other of [...open]) if (other !== api) other.close(false);
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      open.add(api);
      const first = panel.querySelector("button, input, select, a[href], [tabindex]");
      if (first) first.focus({ preventScroll: true });
    },
    close(refocus = true) {
      if (panel.hidden) return;
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
      open.delete(api);
      if (refocus) button.focus({ preventScroll: true });
    },
  };

  button.addEventListener("click", () => (panel.hidden ? api.open() : api.close()));
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      api.close();
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (panel.hidden || panel.contains(e.target) || button.contains(e.target)) return;
    api.close(false);
  });
  return api;
}

/** Close whichever popover is open (Esc pressed outside one). True when one was open. */
export function closeOpenPopover() {
  const any = open.size > 0;
  for (const p of [...open]) p.close();
  return any;
}
