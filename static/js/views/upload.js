// Step 1: drag-and-drop upload with a client-side pre-check (the server is the authority).

import { api } from "../api.js";
import { ACCEPTED_EXTENSIONS, MAX_UPLOAD_BYTES, STRINGS } from "../strings.js";
import { el, viewHeader } from "./dom.js";

const T = STRINGS.upload;

function precheck(file) {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) return T.wrongType;
  if (file.size > MAX_UPLOAD_BYTES) return T.tooLarge;
  return null;
}

export function renderUpload(view, { onUploaded }) {
  const input = el("input", {
    className: "visually-hidden",
    attrs: { type: "file", accept: ACCEPTED_EXTENSIONS.join(","), id: "file-input", tabindex: "-1" },
  });
  const alert = el("div", { className: "alert alert--error", attrs: { role: "alert" } });
  const title = el("p", { className: "dropzone__title", text: T.drop });
  const zone = el(
    "div",
    {
      className: "dropzone",
      attrs: { role: "button", tabindex: "0", "aria-describedby": "dropzone-hint" },
    },
    el("span", { className: "dropzone__icon", attrs: { "aria-hidden": "true" } }),
    title,
    el("p", { className: "muted", text: T.or }),
    el("span", { className: "button button--primary", text: T.choose }),
    el("p", { className: "dropzone__hint", text: T.hint, attrs: { id: "dropzone-hint" } }),
  );
  let busy = false;

  async function handle(file) {
    if (!file || busy) return;
    alert.textContent = "";
    const problem = precheck(file);
    if (problem) {
      alert.textContent = problem;
      return;
    }
    busy = true;
    zone.classList.add("dropzone--busy");
    zone.setAttribute("aria-busy", "true");
    title.textContent = T.reading;
    try {
      const created = await api.upload(file);
      onUploaded(created, file.name);
    } catch (error) {
      alert.textContent = error.message;
      title.textContent = T.drop;
    } finally {
      busy = false;
      zone.classList.remove("dropzone--busy");
      zone.removeAttribute("aria-busy");
      input.value = "";
    }
  }

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => handle(input.files[0]));
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("dropzone--active");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, () => zone.classList.remove("dropzone--active"));
  }
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    handle(event.dataTransfer.files[0]);
  });

  view.append(
    el(
      "div",
      { className: "card" },
      viewHeader(T.title, T.lead),
      zone,
      input,
      alert,
    ),
  );
}
