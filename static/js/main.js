// App bootstrap and step routing. Views never call fetch directly (see api.js).

import { api } from "./api.js";
import { STRINGS } from "./strings.js";
import { clear, el, focusTitle } from "./views/dom.js";
import { renderMapping } from "./views/mapping.js";
import { renderProgress } from "./views/progress.js";
import { renderResults } from "./views/results.js";
import { renderUpload } from "./views/upload.js";

const STEP = { upload: 0, mapping: 1, progress: 2, results: 3 };

const view = document.getElementById("view");
const stepper = document.querySelector(".stepper");
const dialog = document.getElementById("confirm-dialog");
let rules = [];

function applyStaticStrings() {
  document.querySelectorAll("[data-string]").forEach((node) => {
    node.textContent = STRINGS[node.dataset.string] || "";
  });
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.textContent = STRINGS.theme[button.dataset.themeChoice];
  });
  document.querySelector(".theme-switch").setAttribute("aria-label", STRINGS.theme.label);
}

function renderStepper(current) {
  stepper.replaceChildren(
    ...STRINGS.steps.map((label, index) => {
      const state = index < current ? "done" : index === current ? "current" : "todo";
      return el(
        "li",
        { className: `stepper__item stepper__item--${state}`, attrs: { "aria-current": index === current ? "step" : null } },
        el("span", { className: "stepper__bar", attrs: { "aria-hidden": "true" } }),
        el("span", { className: "stepper__label", text: label }),
      );
    }),
  );
}

function confirm(title, body, confirmLabel, cancelLabel) {
  dialog.querySelector(".dialog__title").textContent = title;
  dialog.querySelector(".dialog__text").textContent = body;
  dialog.querySelector("[data-role=confirm]").textContent = confirmLabel;
  dialog.querySelector("[data-role=cancel]").textContent = cancelLabel;
  dialog.returnValue = "";
  dialog.showModal();
  dialog.querySelector("[data-role=cancel]").focus();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

async function show(step, render) {
  renderStepper(step);
  clear(view);
  await render(view);
  focusTitle(view);
}

function goUpload(notice) {
  show(STEP.upload, (container) => {
    renderUpload(container, { onUploaded: goMapping });
    if (notice) container.prepend(el("div", { className: "alert alert--info", text: notice, attrs: { role: "status" } }));
  });
}

function goMapping(created) {
  show(STEP.mapping, (container) => renderMapping(container, { created, onStarted: goProgress, onBack: () => goUpload() }));
}

function goProgress(jobId) {
  show(STEP.progress, (container) =>
    renderProgress(container, { jobId, onCompleted: goResults, onRestart: () => goUpload() }),
  );
}

function goResults(jobId) {
  show(STEP.results, (container) =>
    renderResults(container, {
      jobId,
      rules,
      confirm,
      onRestart: () => goUpload(),
      onDeleted: () => goUpload(STRINGS.results.deleted),
    }),
  );
}

async function start() {
  applyStaticStrings();
  try {
    rules = await api.rules();
  } catch {
    rules = [];
  }
  goUpload();
}

start();
