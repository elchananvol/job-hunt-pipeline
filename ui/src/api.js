// Thin wrapper over the FastAPI backend (server/server.py). No AI here — pure CRUD.
// BASE defaults to "" (same origin): in Docker the UI is served by FastAPI
// itself, so /api + /files just work. Local `npm run dev` proxies them via
// vite.config.js. Set VITE_API_URL only to point the UI at a remote backend.
const BASE = import.meta.env.VITE_API_URL ?? "";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 300)}`);
  }
  return res.json();
}

export const api = {
  stats: () => req("/api/stats"),
  jobs: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "")
    ).toString();
    return req(`/api/jobs?${qs}`);
  },
  job: (id) => req(`/api/jobs/${id}`),
  patchJob: (id, body) => req(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  manualJob: (body) => req("/api/jobs/manual", { method: "POST", body: JSON.stringify(body) }),
  startSearch: (body) => req("/api/search", { method: "POST", body: JSON.stringify(body) }),
  pollSearch: (runId) => req(`/api/search/${runId}`),
  runs: (limit = 20) => req(`/api/runs?limit=${limit}`),
  run: (id) => req(`/api/runs/${id}`),
  lessons: (topic, jobUrl) =>
    req(`/api/lessons?topic=${topic}${jobUrl ? `&job_url=${encodeURIComponent(jobUrl)}` : ""}`),
  addNote: (body) => req("/api/notes", { method: "POST", body: JSON.stringify(body) }),
  fileUrl: (path) => `${BASE}/files/${path}`,
};

export async function fetchCvMarkdown(mdPath) {
  const res = await fetch(api.fileUrl(mdPath));
  if (!res.ok) throw new Error(`CV markdown not found: ${mdPath}`);
  return res.text();
}

export const STATUSES = ["new", "scored", "score_review", "ready_for_cv",
  "cv_ready", "cv_review", "applied", "dismissed"];
export const STATUS_LABELS = {
  new: "New",
  scored: "Scored",
  score_review: "Score Review",
  ready_for_cv: "Ready for CV",
  cv_ready: "CV Ready",
  cv_review: "CV Review",
  applied: "Applied",
  dismissed: "Dismissed",
};
export const prettyStatus = (s) => STATUS_LABELS[s] || s;

// Post-application tracking: sub-stage of an applied job (server/jobs_db.py
// APPLIED_STAGE_LABELS mirrors this list — keep both in sync).
export const APPLIED_STAGES = [
  ["phone_screen", "Phone screen"],
  ["interview", "Interviewing"],
  ["offer", "Offer"],
  ["rejected", "Rejected"],
  ["rejected_fit", "Rejected — not fit for position"],
  ["rejected_better", "Rejected — better candidates"],
  ["rejected_chemistry", "Rejected — they didn't go with me"],
  ["ghosted", "Ghosted — process died"],
  ["withdrawn", "Withdrawn — I pulled out"],
];
export const prettyAppliedStage = (s) =>
  (APPLIED_STAGES.find(([v]) => v === s) || [])[1] || s;
