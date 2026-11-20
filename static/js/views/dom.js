// Tiny DOM helpers. All text goes through textContent; HTML strings are never parsed.

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  const { className, text, attrs, on, dataset } = props;
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const [name, value] of Object.entries(dataset || {})) node.dataset[name] = String(value);
  for (const [event, handler] of Object.entries(on || {})) node.addEventListener(event, handler);
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function viewHeader(title, lead) {
  return el(
    "header",
    { className: "view__header" },
    el("h1", { className: "view__title", text: title, attrs: { tabindex: "-1" } }),
    lead ? el("p", { className: "view__lead", text: lead }) : null,
  );
}

export function focusTitle(container) {
  const title = container.querySelector(".view__title");
  if (title) title.focus({ preventScroll: false });
}

export function table(columns, rows, className = "table") {
  return el(
    "div",
    { className: "table-wrap" },
    el(
      "table",
      { className },
      el("thead", {}, el("tr", {}, columns.map((label) => el("th", { text: label, attrs: { scope: "col" } })))),
      el("tbody", {}, rows),
    ),
  );
}

export function formatNumber(value) {
  return new Intl.NumberFormat().format(value || 0);
}
