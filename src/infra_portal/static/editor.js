// Write and Preview tabs in the post editor.
//
// Without this file the tabs still work: they submit the form, and the server sends the editor
// back on the other tab. With it, the page switches tabs itself and asks /preview for the HTML,
// so nothing reloads. /preview uses the same Markdown renderer as the published post.

const form = document.querySelector("form[data-editor]");

if (form) {
  const title = form.querySelector("#title");
  const body = form.querySelector("#body");
  const pane = form.querySelector("[data-preview]");
  const paneTitle = pane.querySelector("[data-preview-title]");
  const paneBody = pane.querySelector("[data-preview-body]");
  const paneEmpty = pane.querySelector("[data-preview-empty]");
  const tabs = form.querySelectorAll("button.tab");

  const show = (which) => {
    body.hidden = which !== "write";
    pane.hidden = which !== "preview";
    tabs.forEach((tab) => tab.setAttribute("aria-pressed", String(tab.value === which)));
  };

  const preview = async () => {
    paneTitle.textContent = title.value.trim() || "Untitled";
    const empty = body.value.trim() === "";
    paneEmpty.hidden = !empty;
    paneBody.replaceChildren();
    show("preview");
    if (empty) return;

    const data = new FormData();
    data.set("body", body.value);
    try {
      const response = await fetch("/preview", { method: "POST", body: data, credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
      // The server escapes raw HTML in posts, so this is only the HTML Markdown produces.
      paneBody.innerHTML = await response.text();
    } catch {
      paneBody.textContent = "The preview could not load. Your text is still here; switch back to Write.";
    }
  };

  form.addEventListener("submit", (event) => {
    const action = event.submitter?.value;
    if (action === "write") {
      event.preventDefault();
      show("write");
      body.focus();
    } else if (action === "preview") {
      event.preventDefault();
      preview();
    }
  });
}
