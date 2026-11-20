// Step 2: confirm or change the proposed column mapping, then start cleaning.

import { api } from "../api.js";
import { CONTACT_FIELDS, MULTI_SOURCE_FIELDS, STRINGS } from "../strings.js";
import { el, table, viewHeader } from "./dom.js";

const T = STRINGS.mapping;

function confidenceChip(column, target) {
  if (!target) return el("span", { className: "chip chip--none", text: T.confidence.none });
  if (column.method === "manual" || target !== column.target_field) {
    return el("span", { className: "chip chip--manual", text: T.confidence.manual });
  }
  if (column.confidence >= 1) return el("span", { className: "chip chip--sure", text: T.confidence[1] });
  if (column.confidence >= 0.8) return el("span", { className: "chip chip--likely", text: T.confidence[0.8] });
  return el("span", { className: "chip chip--guess", text: T.confidence[0.6] });
}

function fieldSelect(column, fields, onChange) {
  const select = el(
    "select",
    { className: "select", attrs: { "aria-label": `${T.becomes}: ${column.header}` }, on: { change: onChange } },
    el("option", { text: T.keepExtra, attrs: { value: "" } }),
    fields.map((field) => el("option", { text: STRINGS.fields[field] || field, attrs: { value: field } })),
  );
  select.value = column.target_field || "";
  return select;
}

function validate(targets) {
  const chosen = targets.filter(Boolean);
  if (!chosen.some((t) => CONTACT_FIELDS.has(t))) return T.needContact;
  const seen = new Set();
  for (const target of chosen) {
    if (seen.has(target) && !MULTI_SOURCE_FIELDS.has(target)) return T.duplicate(STRINGS.fields[target]);
    seen.add(target);
  }
  return null;
}

function previewTable(created) {
  const headers = created.columns.map((c) => c.header);
  const rows = created.preview.map((row) => el("tr", {}, headers.map((h) => el("td", { text: row[h] ?? "" }))));
  return table(headers, rows, "table table--compact");
}

export function renderMapping(view, { created, fileName, rowCount, onStarted, onBack }) {
  const selects = [];
  const alert = el("div", { className: "alert alert--error", attrs: { role: "alert" } });

  const rows = created.columns.map((column, position) => {
    const chipCell = el("td", {}, confidenceChip(column, column.target_field));
    const select = fieldSelect(column, created.canonical_fields, () => {
      chipCell.replaceChildren(confidenceChip(column, select.value || null));
      alert.textContent = "";
    });
    selects[position] = select;
    const samples = column.samples.length
      ? el("div", { className: "samples" }, column.samples.map((s) => el("span", { className: "sample", text: s, attrs: { title: s } })))
      : el("span", { className: "muted", text: T.empty });
    return el(
      "tr",
      {},
      el("th", { text: column.header, attrs: { scope: "row" } }),
      el("td", {}, samples),
      el("td", {}, select),
      chipCell,
    );
  });

  const submit = el("button", { className: "button button--primary button--large", text: T.submit, attrs: { type: "submit" } });
  const back = el("button", { className: "button button--ghost", text: T.back, attrs: { type: "button" }, on: { click: onBack } });

  const form = el(
    "form",
    {
      on: {
        submit: async (event) => {
          event.preventDefault();
          const targets = selects.map((s) => s.value || null);
          const problem = validate(targets);
          if (problem) {
            alert.textContent = problem;
            return;
          }
          submit.disabled = true;
          try {
            const columns = created.columns.map((c, i) => ({ index: c.index, target_field: targets[i] }));
            await api.saveMapping(created.job_id, columns);
            await api.run(created.job_id);
            onStarted(created.job_id);
          } catch (error) {
            alert.textContent = error.message;
            submit.disabled = false;
          }
        },
      },
    },
    table([T.column, T.examples, T.becomes, ""], rows),
    alert,
    el("div", { className: "button-row button-row--split form-actions" }, back, submit),
  );

  view.append(
    el(
      "div",
      { className: "card" },
      viewHeader(T.title, T.lead),
      el("p", { className: "file-info", text: T.fileInfo(fileName, rowCount) }),
      form,
    ),
    el("div", { className: "card" }, el("h2", { className: "section-title", text: T.preview }), previewTable(created)),
  );
}
