// The only place that talks to the server.

import { STRINGS } from "./strings.js";

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" }, ...options });
  } catch {
    throw new ApiError(STRINGS.errors.network, "NETWORK", 0);
  }
  if (response.status === 204) return null;
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = body && body.error ? body.error : {};
    throw new ApiError(error.message || STRINGS.errors.generic, error.code || "HTTP", response.status);
  }
  return body;
}

function json(method, body) {
  return { method, headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  rules: () => request("/api/rules"),
  upload(files) {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request("/api/jobs", { method: "POST", body: form });
  },
  job: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
  saveMapping: (id, columns) => request(`/api/jobs/${encodeURIComponent(id)}/mapping`, json("PUT", { columns })),
  run: (id) => request(`/api/jobs/${encodeURIComponent(id)}/run`, { method: "POST" }),
  summary: (id) => request(`/api/jobs/${encodeURIComponent(id)}/summary`),
  changes: (id, params) => request(`/api/jobs/${encodeURIComponent(id)}/changes?${query(params)}`),
  review: (id, params) => request(`/api/jobs/${encodeURIComponent(id)}/review?${query(params)}`),
  remove: (id) => request(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  fileUrl: (id, kind) => `/api/jobs/${encodeURIComponent(id)}/files/${encodeURIComponent(kind)}`,
};

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  return search.toString();
}
