// Step 4: summary cards, downloads, review list and change log, delete job.

import { api } from "../api.js";
import { STRINGS } from "../strings.js";
import { el, formatNumber, table, viewHeader } from "./dom.js";

const T = STRINGS.results;
const PAGE_SIZE = 25;

function statCards(job) {
  const c = job.counts || {};
  const cards = [
    [T.cards.rows_in, job.rows_in, "primary"],
    [T.cards.ready, job.rows_out, "success"],
    [T.cards.fixed, c.FIXED, "success"],
    [T.cards.review, (c.REVIEW || 0) + (c.BLOCKED || 0), "warning"],
    [T.cards.merged, c.MERGED, "primary"],
    [T.cards.opted_out, c.SUPPRESSED, "warning"],
    [T.cards.rejected, c.REJECTED, "danger"],
  ];
  return el(
    "ul",
    { className: "stats", attrs: { role: "list" } },
    cards.map(([label, value, tone]) =>
      el(
        "li",
        { className: `stat stat--${tone}` },
        el("div", { className: "stat__value", text: formatNumber(value) }),
        el("div", { className: "stat__label", text: label }),
      ),
    ),
  );
}

function fileSummary(job) {
  const files = job.files || [];
  if (files.length < 2) return null;
  const F = T.files;
  const columns = [F.columns.file, F.columns.rows, F.columns.ready, F.columns.review, F.columns.merged, F.columns.opted_out, F.columns.rejected];
  const rows = files.map((file) => {
    const c = file.counts || {};
    if (file.duplicate_of !== null) {
      const note = F.ignored(files[file.duplicate_of].filename);
      return el("tr", {}, el("th", { text: file.filename, attrs: { scope: "row" } }), el("td", { text: note, attrs: { colspan: "6" } }));
    }
    const values = [file.rows_in, (c.OK || 0) + (c.FIXED || 0) + (c.REVIEW || 0), c.REVIEW, c.MERGED, c.SUPPRESSED, (c.REJECTED || 0) + (c.BLOCKED || 0)];
    return el("tr", {}, el("th", { text: file.filename, attrs: { scope: "row" } }), values.map((v) => el("td", { text: formatNumber(v) })));
  });
  return el("div", { className: "card" }, el("h2", { className: "section-title", text: F.title }), table(columns, rows), el("p", { className: "muted", text: F.mergeNote }));
}

function downloads(jobId) {
  return el(
    "div",
    { className: "card downloads" },
    el(
      "a",
      { className: "button button--primary button--large", text: T.downloadMain, attrs: { href: api.fileUrl(jobId, "report_xlsx"), download: "" } },
    ),
    el(
      "div",
      { className: "downloads__secondary" },
      Object.entries(T.downloads).map(([kind, label]) =>
        el("a", { className: "button button--secondary", text: label, attrs: { href: api.fileUrl(jobId, kind), download: "" } }),
      ),
    ),
  );
}

function valueCell(value) {
  return value ? el("span", { className: "cell-value", text: value }) : el("span", { className: "muted", text: T.empty });
}

function codeBadge(code, rules) {
  return el("span", { className: "badge badge--code", text: code, attrs: { title: rules.get(code) || "" } });
}

function reviewRow(item, rules) {
  const where = item.record_id ? `${item.record_id} · ${T.sourceRows(item.source_rows)}` : T.jobLevel;
  return el(
    "tr",
    {},
    el("td", { text: where }),
    el("td", { text: item.field ? STRINGS.fields[item.field] || item.field : "—" }),
    el(
      "td",
      {},
      el(
        "div",
        { className: "problem" },
        el("span", { text: item.problem }),
        el(
          "span",
          { className: "problem__meta" },
          el("span", { className: `badge badge--${item.severity}`, text: T.severity[item.severity] || item.severity }),
          codeBadge(item.rule_code, rules),
        ),
      ),
    ),
    el("td", {}, valueCell(item.original_value ?? item.current_value)),
    el("td", {}, valueCell(item.suggestion)),
  );
}

function changeRow(item, rules) {
  return el(
    "tr",
    {},
    el("td", { text: `${item.record_id} · ${T.sourceRows(item.source_row)}` }),
    el("td", { text: STRINGS.fields[item.field] || item.field }),
    el("td", {}, item.old_value ? el("span", { className: "cell-value cell-old", text: item.old_value }) : valueCell("")),
    el("td", {}, item.new_value ? el("span", { className: "cell-value cell-new", text: item.new_value }) : valueCell("")),
    el("td", {}, el("div", { className: "problem" }, el("span", { text: item.explanation }), el("span", { className: "problem__meta" }, codeBadge(item.rule_code, rules)))),
  );
}

function filterSelect(label, options, onChange) {
  const select = el(
    "select",
    { className: "select", on: { change: () => onChange(select.value) } },
    el("option", { text: T.all, attrs: { value: "" } }),
    options.map(([value, text]) => el("option", { text, attrs: { value } })),
  );
  return el("label", { className: "field" }, el("span", { text: label }), select);
}

function pagedList({ load, columns, renderRow, emptyText, filters }) {
  const body = el("div");
  const state = { page: 1, params: {} };
  const status = el("p", { className: "visually-hidden", attrs: { role: "status" } });

  async function refresh() {
    let result;
    try {
      result = await load({ ...state.params, page: state.page, page_size: PAGE_SIZE });
    } catch (error) {
      body.replaceChildren(el("div", { className: "alert alert--error", text: error.message, attrs: { role: "alert" } }));
      return;
    }
    if (!result.total) {
      body.replaceChildren(el("p", { className: "empty-state", text: emptyText }));
      status.textContent = emptyText;
      return;
    }
    const pages = Math.max(1, Math.ceil(result.total / PAGE_SIZE));
    const label = T.page(state.page, pages);
    status.textContent = label;
    const previous = el("button", {
      className: "button button--secondary",
      text: T.previous,
      attrs: { type: "button", disabled: state.page <= 1 },
      on: { click: () => { state.page -= 1; refresh(); } },
    });
    const next = el("button", {
      className: "button button--secondary",
      text: T.next,
      attrs: { type: "button", disabled: state.page >= pages },
      on: { click: () => { state.page += 1; refresh(); } },
    });
    body.replaceChildren(
      table(columns, result.items.map(renderRow)),
      el("div", { className: "pager" }, previous, el("span", { text: label }), next),
    );
  }

  const filterBar = el(
    "div",
    { className: "filters" },
    filters.map(({ label, name, options }) =>
      filterSelect(label, options, (value) => {
        state.params[name] = value;
        state.page = 1;
        refresh();
      }),
    ),
  );
  refresh();
  return el("div", {}, filterBar, status, body);
}

function tabs(panels, initial) {
  const tabList = el("div", { className: "tabs", attrs: { role: "tablist" } });
  const container = el("div");
  const buttons = panels.map((panel, index) => {
    const button = el(
      "button",
      {
        className: "tabs__tab",
        attrs: { type: "button", role: "tab", id: `tab-${panel.id}`, "aria-controls": `panel-${panel.id}` },
        on: { click: () => select(index) },
      },
      panel.label,
      el("span", { className: "tabs__count", text: formatNumber(panel.count) }),
    );
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const step = event.key === "ArrowRight" ? 1 : -1;
      const target = (index + step + panels.length) % panels.length;
      select(target);
      buttons[target].focus();
    });
    return button;
  });
  const bodies = panels.map((panel) =>
    el("div", { attrs: { role: "tabpanel", id: `panel-${panel.id}`, "aria-labelledby": `tab-${panel.id}`, tabindex: "0" } }, panel.content),
  );
  function select(index) {
    buttons.forEach((button, i) => {
      button.setAttribute("aria-selected", String(i === index));
      button.tabIndex = i === index ? 0 : -1;
      bodies[i].hidden = i !== index;
    });
  }
  tabList.append(...buttons);
  container.append(...bodies);
  select(initial);
  return el("div", { className: "card" }, tabList, container);
}

export async function renderResults(view, { jobId, rules, confirm, onRestart, onDeleted }) {
  let job;
  let reviewTotal = 0;
  let changeTotal = 0;
  try {
    [job, { total: reviewTotal }, { total: changeTotal }] = await Promise.all([
      api.job(jobId),
      api.review(jobId, { page_size: 1 }),
      api.changes(jobId, { page_size: 1 }),
    ]);
  } catch (error) {
    view.append(el("div", { className: "card" }, el("div", { className: "alert alert--error", text: error.message, attrs: { role: "alert" } })));
    return;
  }
  const ruleText = new Map(rules.map((r) => [r.code, r.explanation]));
  const reviewRules = rules.filter((r) => r.severity !== "info").map((r) => [r.code, `${r.explanation} (${r.code})`]);
  const allRules = rules.map((r) => [r.code, `${r.explanation} (${r.code})`]);
  const fieldOptions = Object.entries(STRINGS.fields).map(([value, text]) => [value, text]);

  const reviewPanel = pagedList({
    load: (params) => api.review(jobId, params),
    columns: [T.columns.record, T.columns.field, T.columns.problem, T.columns.value, T.columns.suggestion],
    renderRow: (item) => reviewRow(item, ruleText),
    emptyText: T.noReview,
    filters: [{ label: T.filterProblem, name: "rule_code", options: reviewRules }],
  });
  const changesPanel = pagedList({
    load: (params) => api.changes(jobId, params),
    columns: [T.columns.record, T.columns.field, T.columns.before, T.columns.after, T.columns.why],
    renderRow: (item) => changeRow(item, ruleText),
    emptyText: T.noChanges,
    filters: [
      { label: T.filterRule, name: "rule_code", options: allRules },
      { label: T.filterField, name: "field", options: fieldOptions },
    ],
  });

  const deleteButton = el("button", {
    className: "button button--danger-ghost",
    text: T.deleteJob,
    attrs: { type: "button" },
    on: {
      click: async () => {
        const ok = await confirm(T.deleteTitle, T.deleteBody, T.deleteConfirm, T.cancel);
        if (!ok) return;
        try {
          await api.remove(jobId);
          onDeleted();
        } catch (error) {
          deleteButton.after(el("div", { className: "alert alert--error", text: error.message, attrs: { role: "alert" } }));
        }
      },
    },
  });

  view.append(
    viewHeader(T.title, T.lead),
    statCards(job),
    downloads(jobId),
    fileSummary(job),
    tabs(
      [
        { id: "review", label: T.tabs.review, count: reviewTotal, content: reviewPanel },
        { id: "changes", label: T.tabs.changes, count: changeTotal, content: changesPanel },
      ],
      reviewTotal > 0 ? 0 : 1,
    ),
    el(
      "div",
      { className: "button-row button-row--split form-actions" },
      deleteButton,
      el("button", { className: "button button--primary", text: T.another, attrs: { type: "button" }, on: { click: onRestart } }),
    ),
  );
}
