// The subscribe pop-up.
//
// Without this file every Subscribe button is a plain link to /subscribe, a page with the same
// form. With it, a link marked data-subscribe-open opens the pop-up in base.html instead, and the
// form is sent from inside it, so the reader stays on the page they were reading.

const dialog = document.querySelector("dialog.subscribe");

if (dialog && typeof dialog.showModal === "function") {
  const form = dialog.querySelector("[data-subscribe-form]");
  const input = form.querySelector('input[type="email"]');
  const button = form.querySelector("button");
  const panels = dialog.querySelectorAll("[data-subscribe-panel]");
  const errors = dialog.querySelectorAll("[data-subscribe-error]");
  const art = dialog.querySelector(".subscribe-art img");
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  // The same time as the closing animation in site.css.
  const CLOSE_MS = 160;

  const show = (step) => {
    dialog.dataset.step = step;
    panels.forEach((panel) => { panel.hidden = panel.dataset.subscribePanel !== step; });
  };
  const complain = (about) => {
    errors.forEach((error) => { error.hidden = error.dataset.subscribeError !== about; });
  };
  // The painting is not fetched with the page, since most visits never open the pop-up. Once
  // the page has loaded and the browser is idle it is fetched, so it is there when asked for.
  const fetchArt = () => { art.loading = "eager"; };
  window.addEventListener("load", () => {
    if ("requestIdleCallback" in window) requestIdleCallback(fetchArt);
    else setTimeout(fetchArt, 1500);
  });
  const busy = (yes) => {
    form.setAttribute("aria-busy", String(yes));
    button.disabled = yes;
  };

  const open = (from) => {
    if (dialog.open) return;
    show("form");
    complain(null);
    fetchArt();
    dialog.style.transformOrigin = "";
    dialog.showModal();
    if (from) {
      // Grow from the middle of the button that was pressed. offsetLeft and offsetTop are where
      // the pop-up will rest; they ignore the opening animation, which has already started.
      const at = from.getBoundingClientRect();
      const x = at.left + at.width / 2 - dialog.offsetLeft;
      const y = at.top + at.height / 2 - dialog.offsetTop;
      dialog.style.transformOrigin = `${x}px ${y}px`;
    }
    input.focus();
  };

  const close = () => {
    if (!dialog.open || dialog.classList.contains("closing")) return;
    if (calm.matches) {
      dialog.close();
      return;
    }
    dialog.classList.add("closing");
    setTimeout(() => {
      dialog.classList.remove("closing");
      dialog.close();
    }, CLOSE_MS);
  };

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const opener = event.target.closest("[data-subscribe-open]");
    if (opener) {
      // Leave "open in a new tab" alone.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      open(opener);
    } else if (event.target.closest("[data-subscribe-close]")) {
      close();
    }
  });

  // A press outside the pop-up closes it. The press must also start outside, so dragging a
  // selection out of the address field does not.
  let pressedOutside = false;
  dialog.addEventListener("pointerdown", (event) => { pressedOutside = event.target === dialog; });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && pressedOutside) close();
  });
  // Escape closes with the same animation.
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  dialog.addEventListener("close", () => {
    busy(false);
    if (location.hash === "#subscribe") history.replaceState(null, "", location.pathname + location.search);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.getAttribute("aria-busy") === "true") return;
    const address = input.value.trim();
    const data = new FormData(form);
    complain(null);
    busy(true);

    let response;
    try {
      response = await fetch(form.action, { method: "POST", body: data, credentials: "same-origin" });
    } catch {
      // No answer at all. Send the form the ordinary way and let the browser show what happens.
      form.submit();
      return;
    }
    busy(false);

    // The server answers with a whole page; only its status matters here. 200 is also what an
    // address that is already subscribed gets, so the pop-up does not reveal who signed up.
    if (response.ok) {
      dialog.querySelector("[data-subscribe-address]").textContent = address;
      form.reset();
      show("sent");
      dialog.querySelector("[data-subscribe-sent-title]").focus();
    } else {
      complain(response.status === 422 ? "invalid" : "failed");
      input.focus();
    }
  });

  // /any-page#subscribe opens the pop-up, so the link can be shared.
  const fromAddress = () => {
    if (location.hash === "#subscribe") open(null);
  };
  window.addEventListener("hashchange", fromAddress);
  fromAddress();
}
