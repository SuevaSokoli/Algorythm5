// Step 2: confirm or change the proposed column mapping of every file, then start cleaning.

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

function chipCell(column, target) {
  const reason = target === column.target_field ? T.reasons[column.reason] : null;
  return [confidenceChip(column, target), reason ? el("div", { className: "muted", text: reason }) : null];
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

function previewTable(file) {
  const headers = file.columns.map((c) => c.header);
  const rows = file.preview.map((row) => el("tr", {}, headers.map((h) => el("td", { text: row[h] ?? "" }))));
  return el("details", { className: "preview" }, el("summary", { text: T.preview }), table(headers, rows, "table table--compact"));
}

function fileSection(file, created, files, onEdit) {
  const info = el("p", { className: "file-info", text: T.fileInfo(file) });
  const heading = el("h2", { className: "section-title", text: file.filename });
  if (file.duplicate_of !== null) {
    const original = files[file.duplicate_of].filename;
    return { section: el("section", { className: "card" }, heading, el("div", { className: "alert alert--info", text: T.repeated(original) })), selects: [] };
  }
  const selects = [];
  const rows = file.columns.map((column) => {
    const cell = el("td", {}, chipCell(column, column.target_field));
    const select = fieldSelect(column, created.canonical_fields, () => {
      cell.replaceChildren(...chipCell(column, select.value || null).filter(Boolean));
      onEdit();
    });
    selects.push({ column, select });
    const samples = column.samples.length
      ? el("div", { className: "samples" }, column.samples.map((s) => el("span", { className: "sample", text: s, attrs: { title: s } })))
      : el("span", { className: "muted", text: T.empty });
    return el("tr", {}, el("th", { text: column.header, attrs: { scope: "row" } }), el("td", {}, samples), el("td", {}, select), cell);
  });
  const notice = file.header_row_index < 0 ? el("div", { className: "alert alert--info", text: T.noHeader }) : null;
  return {
    section: el("section", { className: "card" }, heading, info, notice, table([T.column, T.examples, T.becomes, ""], rows), previewTable(file)),
    selects,
  };
}

export function renderMapping(view, { created, onStarted, onBack }) {
  const alert = el("div", { className: "alert alert--error", attrs: { role: "alert" } });
  const clearAlert = () => {
    alert.textContent = "";
  };
  const sections = created.files.map((file) => ({ file, ...fileSection(file, created, created.files, clearAlert) }));

  const submit = el("button", { className: "button button--primary button--large", text: T.submit(created.files.length), attrs: { type: "submit" } });
  const back = el("button", { className: "button button--ghost", text: T.back, attrs: { type: "button" }, on: { click: onBack } });

  const form = el(
    "form",
    {
      on: {
        submit: async (event) => {
          event.preventDefault();
          for (const { file, selects } of sections) {
            if (!selects.length) continue;
            const problem = validate(selects.map(({ select }) => select.value || null));
            if (problem) {
              alert.textContent = created.files.length > 1 ? `${file.filename}: ${problem}` : problem;
              return;
            }
          }
          submit.disabled = true;
          try {
            const columns = sections.flatMap(({ file, selects }) =>
              selects.map(({ column, select }) => ({ file_index: file.index, index: column.index, target_field: select.value || null })),
            );
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
    sections.map((s) => s.section),
    el("div", { className: "card" }, alert, el("div", { className: "button-row button-row--split form-actions" }, back, submit)),
  );

  view.append(viewHeader(T.title, T.lead), form);
}
