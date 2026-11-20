// Step 3: poll the job every second until it completes or fails.

import { api } from "../api.js";
import { STRINGS } from "../strings.js";
import { el, viewHeader } from "./dom.js";

const T = STRINGS.progress;
const POLL_MS = 1000;
const SLOW_AFTER_MS = 60_000;
const GIVE_UP_AFTER_MS = 10 * 60_000;

export function renderProgress(view, { jobId, onCompleted, onRestart }) {
  const status = el("p", { className: "muted", text: T.working, attrs: { role: "status" } });
  const alert = el("div", { className: "alert alert--error", attrs: { role: "alert" } });
  const restart = el("button", {
    className: "button button--secondary",
    text: T.tryAgain,
    attrs: { type: "button", hidden: true },
    on: { click: onRestart },
  });
  const spinner = el("div", { className: "spinner", attrs: { "aria-hidden": "true" } });
  const card = el(
    "div",
    { className: "card" },
    viewHeader(T.title),
    el("div", { className: "progress" }, spinner, status, alert, restart),
  );
  view.append(card);

  const started = Date.now();
  let stopped = false;

  function fail(message) {
    stopped = true;
    spinner.remove();
    status.textContent = T.failed;
    alert.textContent = message;
    restart.hidden = false;
    restart.focus();
  }

  async function poll() {
    if (stopped || !card.isConnected) return;  // user navigated away
    try {
      const job = await api.job(jobId);
      if (job.status === "completed") {
        stopped = true;
        onCompleted(jobId);
        return;
      }
      if (job.status === "failed") {
        fail(job.error_message || STRINGS.errors.generic);
        return;
      }
    } catch (error) {
      fail(error.message);
      return;
    }
    const elapsed = Date.now() - started;
    if (elapsed > GIVE_UP_AFTER_MS) {
      fail(T.slow);
      return;
    }
    if (elapsed > SLOW_AFTER_MS) status.textContent = T.slow;
    setTimeout(poll, POLL_MS);
  }

  poll();
}
