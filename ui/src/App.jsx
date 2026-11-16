import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { APPLIED_STAGES, STATUSES, api, fetchCvMarkdown, prettyAppliedStage, prettyStatus } from "./api";

// DB stores either a path relative to cv_generated/ or an absolute path —
// /files/ serves relative to cv_generated/, so normalize here.
function cvRel(path) {
  if (!path) return null;
  const norm = String(path).replace(/\\/g, "/");
  const i = norm.indexOf("cv_generated/");
  return i >= 0 ? norm.slice(i + "cv_generated/".length) : norm.replace(/^\/+/, "");
}

function pctBadge(pct, dealbreaker) {
  if (pct === null || pct === undefined)
    return <span className="pct pct-none">—</span>;
  const cls = dealbreaker ? "pct-lo" : pct >= 70 ? "pct-hi" : pct >= 45 ? "pct-mid" : "pct-lo";
  return (
    <span className={`pct ${cls}`} title={dealbreaker ? "dealbreaker — final forced to 0" : undefined}>
      {dealbreaker ? `⛔ ${pct}%` : `${pct}%`}
    </span>
  );
}

// Effective score: server coalesces final_weighted_score over legacy
// match_pct into `match_pct`; prefer the explicit v7 column when present.
function effScore(j) {
  return j?.final_weighted_score ?? j?.match_pct ?? null;
}

function StatusBoxes({ stats, selected, onToggle, unscoredOnly, onUnscored,
  savedOnly, onSaved }) {
  return (
    <div className="boxes">
      {STATUSES.map((s) => (
        <button
          key={s}
          className={`box${selected.has(s) ? " on" : ""}`}
          onClick={() => onToggle(s)}
          title="toggle filter"
        >
          {prettyStatus(s)} <b>{stats?.by_status?.[s] ?? 0}</b>
        </button>
      ))}
      <button className={`box${savedOnly ? " on" : ""}`} onClick={onSaved}>
        ★ saved <b>{stats?.saved ?? 0}</b>
      </button>
      <button className={`box${unscoredOnly ? " on" : ""}`} onClick={onUnscored}>
        unscored only <b>{stats?.unscored ?? 0}</b>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- board

function Board({ onOpen, highlightSince, refreshKey }) {
  const [stats, setStats] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(new Set(["scored"]));
  const [unscoredOnly, setUnscoredOnly] = useState(false);
  const [savedOnly, setSavedOnly] = useState(false);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      // Filters run server-side: the DB (1500+ jobs) is bigger than any
      // page the board fetches, so client-only filtering would hide rows
      // (e.g. unscored "new" jobs sort below the fetch limit).
      const params = { sort: "-final_weighted_score", limit: 5000 };
      if (selected.size > 0) params.status = [...selected].join(",");
      if (unscoredOnly) params.has_score = false;
      if (savedOnly) params.saved = true;
      const [s, j] = await Promise.all([api.stats(), api.jobs(params)]);
      setStats(s);
      setJobs(j.jobs);
      setErr("");
    } catch (e) {
      setErr("Backend unreachable — is server/server.py running? " + e.message);
    }
  }, [selected, unscoredOnly, savedOnly]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const toggle = (s) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return jobs.filter((j) => {
      if (selected.size > 0 && !selected.has(j.status)) return false;
      if (unscoredOnly && effScore(j) !== null) return false;
      if (savedOnly && !j.is_saved) return false;
      if (needle && !`${j.title} ${j.company} ${j.location}`.toLowerCase().includes(needle))
        return false;
      return true;
    });
  }, [jobs, selected, unscoredOnly, savedOnly, q]);

  const toggleSave = async (e, j) => {
    e.stopPropagation();
    await api.patchJob(j.id, { is_saved: !j.is_saved });
    load();
  };

  const quickAct = async (e, j, body) => {
    e.stopPropagation();
    await api.patchJob(j.id, body);
    load();
  };

  return (
    <div>
      <StatusBoxes
        stats={stats}
        selected={selected}
        onToggle={toggle}
        unscoredOnly={unscoredOnly}
        onUnscored={() => setUnscoredOnly((v) => !v)}
        savedOnly={savedOnly}
        onSaved={() => setSavedOnly((v) => !v)}
      />
      <div className="toolbar">
        <input
          placeholder="search title / company / location"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="muted">
          {shown.length} / {jobs.length} jobs
        </span>
        <button onClick={load}>refresh</button>
      </div>
      {err && <p className="error">{err}</p>}
      <table>
        <thead>
          <tr>
            <th title="saved">★</th>
            <th>%</th>
            <th>title</th>
            <th>company</th>
            <th>site</th>
            <th>status</th>
            <th>actions</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((j) => (
            <tr
              key={j.id}
              onClick={() => onOpen(j.id)}
              className={highlightSince && j.last_seen_at >= highlightSince ? "fresh" : ""}
            >
              <td>
                <button
                  className={`star${j.is_saved ? " lit" : ""}`}
                  onClick={(e) => toggleSave(e, j)}
                  title={j.is_saved ? "unsave" : "save"}
                >
                  {j.is_saved ? "★" : "☆"}
                </button>
              </td>
              <td>{pctBadge(effScore(j), j.is_dealbreaker)}</td>
              <td>{j.title}</td>
              <td>{j.company}</td>
              <td className="muted">{j.site}</td>
              <td>
                <span className={`pill st-${j.status}`}>{prettyStatus(j.status)}</span>
                {j.status === "applied" && j.applied_stage && (
                  <span className="muted"> · {prettyAppliedStage(j.applied_stage)}</span>
                )}
              </td>
              <td className="actions">
                {j.status !== "dismissed" && (
                  <button
                    onClick={(e) => quickAct(e, j, { status: "dismissed" })}
                    title="dismiss"
                  >
                    🚫
                  </button>
                )}{" "}
                {j.status !== "applied" && j.status !== "dismissed" && (
                  <button
                    className="apply"
                    onClick={(e) => quickAct(e, j, { status: "applied" })}
                    title="mark applied"
                  >
                    ☑️
                  </button>
                )}
                {j.status === "dismissed" && (
                  <button
                    onClick={(e) => quickAct(e, j, { status: "scored" })}
                    title="reopen"
                  >
                    ↩️
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- detail

function Bullets({ text }) {
  if (!text) return <span className="muted">—</span>;
  const lines = String(text)
    .split("\n")
    .map((l) => l.replace(/^[-*•]\s*/, "").trim())
    .filter(Boolean);
  return (
    <ul>
      {lines.map((l, i) => (
        <li key={i}>{l}</li>
      ))}
    </ul>
  );
}

function Detail({ id, onBack, onChanged }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [matchFb, setMatchFb] = useState("");
  const [cvFb, setCvFb] = useState("");
  const [cvMd, setCvMd] = useState(null);
  const [cvErr, setCvErr] = useState("");
  const [showDesc, setShowDesc] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.job(id);
      setData(d);
      setErr("");
      setCvMd(null);
      setCvErr("");
    } catch (e) {
      setErr(e.message);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (err) return <div><button onClick={onBack}>← board</button><p className="error">{err}</p></div>;
  if (!data) return <div><button onClick={onBack}>← board</button><p>loading…</p></div>;
  const { job, match, lesson_entries } = data;

  const act = async (body) => {
    await api.patchJob(id, body);
    await load();
    onChanged();
  };

  const loadCvMd = async () => {
    try {
      setCvMd(await fetchCvMarkdown(cvRel(match.cv_md_path)));
    } catch (e) {
      setCvErr(e.message);
    }
  };

  const st = match.status;
  const STAGE_ACTIONS = {
    new: [["ready_for_cv", "request CV tailoring"], ["applied", "mark applied"], ["dismissed", "dismiss"]],
    scored: [["ready_for_cv", "request CV tailoring"], ["applied", "mark applied"], ["dismissed", "dismiss"]],
    score_review: [["applied", "mark applied"], ["dismissed", "dismiss"]],
    ready_for_cv: [["applied", "mark applied"], ["dismissed", "dismiss"]],
    cv_ready: [["applied", "mark applied"], ["dismissed", "dismiss"]],
    cv_review: [["applied", "mark applied"], ["dismissed", "dismiss"]],
    applied: [["dismissed", "dismiss"]],
    dismissed: [["scored", "reopen"]],
  };
  const STAGE_HINT = {
    score_review: "with the AI — tell it to rescore",
    cv_review: "with the AI — tell it to rework the CV",
    ready_for_cv: "queued — tell the AI to make the CV",
  };
  return (
    <div>
      <button onClick={onBack}>← board</button>
      <h2>
        {pctBadge(effScore(match), match.is_dealbreaker)} {job.title}{" "}
        <button
          className={`star${match.is_saved ? " lit" : ""}`}
          onClick={() => act({ is_saved: !match.is_saved })}
          title={match.is_saved ? "unsave" : "save (tracking only, keeps stage)"}
        >
          {match.is_saved ? "★" : "☆"}
        </button>
      </h2>
      <p className="muted">
        {job.company} · {job.location} · {job.site} ·{" "}
        <a href={job.job_url} target="_blank" rel="noreferrer">
          original posting
        </a>{" "}
        · <span className={`pill st-${st}`}>{prettyStatus(st)}</span>
      </p>

      <h3>the job in brief</h3>
      <p>{match.job_brief || <span className="muted">not scored yet — request scoring in AI chat</span>}</p>
      <button onClick={() => setShowDesc((v) => !v)}>
        {showDesc ? "hide posting text" : "show posting text"}
      </button>
      {showDesc && (
        <div className="md jobdesc">
          <ReactMarkdown>{job.description || "(none saved)"}</ReactMarkdown>
        </div>
      )}

      <h3>why this score</h3>
      {match.is_dealbreaker ? (
        <p><b>⛔ dealbreaker.</b> {match.dealbreaker_reason || "blocked — see gaps."}</p>
      ) : null}
      {(match.c2j_score !== null && match.c2j_score !== undefined) ||
       (match.j2c_score !== null && match.j2c_score !== undefined) ? (
        <>
          <p>
            <b>final {effScore(match) ?? "—"}%</b>
            {match.c2j_score !== null && match.j2c_score !== null &&
              ` = round(${match.c2j_score} × 0.4 + ${match.j2c_score} × 0.6)`}
          </p>
          <h4>C2J — can I do this job? {match.c2j_score ?? "—"}%</h4>
          <p>{match.c2j_reasoning || <span className="muted">—</span>}</p>
          <h4>J2C — is this job right for me? {match.j2c_score ?? "—"}%</h4>
          <p>{match.j2c_reasoning || <span className="muted">—</span>}</p>
        </>
      ) : (
        <p><span className="muted">not scored yet — request scoring in AI chat</span></p>
      )}
      <div className="cols">
        <div>
          <h4>fits</h4>
          <Bullets text={match.matched_points} />
        </div>
        <div>
          <h4>compensable gaps</h4>
          <Bullets text={match.compensable_gaps} />
        </div>
        <div>
          <h4>major gaps</h4>
          <Bullets text={match.major_gaps} />
        </div>
      </div>

      <h3>stage: {prettyStatus(st)}{match.in_process ? " · in process" : ""}</h3>
      {STAGE_HINT[st] && <p className="muted">{STAGE_HINT[st]}</p>}
      <div className="row">
        {(STAGE_ACTIONS[st] || []).map(([target, label]) => (
          <button key={target} onClick={() => act({ status: target })}>
            {label}
          </button>
        ))}
      </div>

      {st === "applied" && (
        <>
          <h3>application tracking</h3>
          <p>
            <select
              value={match.applied_stage || ""}
              onChange={(e) => act({ applied_stage: e.target.value })}
            >
              <option value="">Applied — waiting to hear</option>
              {APPLIED_STAGES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </p>
          <p className="muted">
            sub-stage only — the job stays in Applied. Pick “waiting to hear” to clear.
          </p>
        </>
      )}

      <h3>AI feedback</h3>
      <div className="cols">
        {["new", "scored", "score_review"].includes(st) && (
        <div>
          <h4>scoring feedback</h4>
          <textarea
            rows={3}
            placeholder="what's wrong with the score?"
            value={matchFb}
            onChange={(e) => setMatchFb(e.target.value)}
          />
          <button
            onClick={() => act({ match_feedback: matchFb }).then(() => setMatchFb(""))}
            disabled={!matchFb.trim()}
          >
            save → score_review
          </button>
          <p className="muted">hint: tell the AI to rescore</p>
          {match.match_feedback && <p className="kept">kept: {match.match_feedback}</p>}
        </div>
        )}
        {["cv_ready", "cv_review"].includes(st) && (
        <div>
          <h4>CV feedback</h4>
          <textarea
            rows={3}
            placeholder="what's wrong with the CV?"
            value={cvFb}
            onChange={(e) => setCvFb(e.target.value)}
          />
          <button
            onClick={() => act({ cv_feedback: cvFb }).then(() => setCvFb(""))}
            disabled={!cvFb.trim()}
          >
            save → cv_review
          </button>
          <p className="muted">hint: tell the AI to rework the CV</p>
          {match.cv_feedback && <p className="kept">kept: {match.cv_feedback}</p>}
        </div>
        )}
      </div>

      <h3>tailored CV</h3>
      {match.cv_pdf_path ? (
        <p>
          <a href={api.fileUrl(cvRel(match.cv_pdf_path))} target="_blank" rel="noreferrer">
            open PDF in new tab
          </a>
        </p>
      ) : (
        <p className="muted">no PDF yet</p>
      )}
      {match.cv_md_path ? (
        cvMd ? (
          <div className="md">
            <ReactMarkdown>{cvMd}</ReactMarkdown>
          </div>
        ) : (
          <button onClick={loadCvMd}>render CV markdown</button>
        )
      ) : (
        <p className="muted">not generated — request in AI chat</p>
      )}
      {cvErr && <p className="error">{cvErr}</p>}

      <h3>my notes about this job</h3>
      {match.notes && <p>{match.notes}</p>}
      {lesson_entries.length === 0 && !match.notes && <p className="muted">none yet</p>}
      <ul>
        {lesson_entries.map((e, i) => (
          <li key={i}>
            <span className="pill">{e.topic}</span> {e.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- search

const ALL_SITES = ["linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "bdjobs"];

function SearchView({ onDone }) {
  const [form, setForm] = useState({
    search_term: "software engineer",
    sites: ["linkedin", "indeed"],
    location: "israel",
    results_wanted: 100,
    hours_old: 72,
    job_type: "fulltime",
    page_size: 25,
    target_new: "",
    max_passes: 6,
  });
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState([]);
  const [expanded, setExpanded] = useState(null);

  const loadHistory = useCallback(async () => {
    try {
      const r = await api.runs();
      setHistory(r.runs);
    } catch {
      /* backend down — board shows the error */
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const toggleSite = (s) =>
    setForm((f) => ({
      ...f,
      sites: f.sites.includes(s) ? f.sites.filter((x) => x !== s) : [...f.sites, s],
    }));

  const start = async () => {
    setErr("");
    setRun(null);
    try {
      const { run_id } = await api.startSearch({
        ...form,
        hours_old: form.hours_old === "" ? null : Number(form.hours_old),
        results_wanted: Number(form.results_wanted),
        page_size: Number(form.page_size),
        target_new: form.target_new === "" ? null : Number(form.target_new),
        max_passes: Number(form.max_passes),
      });
      const poll = async () => {
        const state = await api.pollSearch(run_id);
        setRun(state);
        if (state.state === "running") setTimeout(poll, 3000);
        else if (state.state === "done") {
          onDone(new Date().toISOString().slice(0, 19));
          loadHistory();
        }
      };
      poll();
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div>
      <h2>fetch jobs</h2>
      <label>
        search term <input value={form.search_term} onChange={(e) => set("search_term", e.target.value)} />
      </label>
      <div className="row">
        {ALL_SITES.map((s) => (
          <label key={s} className="check">
            <input type="checkbox" checked={form.sites.includes(s)} onChange={() => toggleSite(s)} /> {s}
          </label>
        ))}
      </div>
      <div className="row">
        <label>location <input value={form.location} onChange={(e) => set("location", e.target.value)} /></label>
        <label>results <input type="number" value={form.results_wanted} onChange={(e) => set("results_wanted", e.target.value)} /></label>
        <label>hours_old <input type="number" value={form.hours_old} onChange={(e) => set("hours_old", e.target.value)} /></label>
        <label>job_type <input value={form.job_type} onChange={(e) => set("job_type", e.target.value)} /></label>
        <label>page_size <input type="number" value={form.page_size} onChange={(e) => set("page_size", e.target.value)} /></label>
        <label>target_new (empty = single pass) <input type="number" value={form.target_new} onChange={(e) => set("target_new", e.target.value)} /></label>
        <label>max_passes <input type="number" value={form.max_passes} onChange={(e) => set("max_passes", e.target.value)} /></label>
      </div>
      <button onClick={start}>run fetch</button>
      {err && <p className="error">{err}</p>}
      {run && (
        <div>
          <p>
            state: <b>{run.state}</b>
            {run.state === "done" && (
              <> — {run.total_inserted} new jobs ({run.total_rows} rows fetched)</>
            )}
            {run.state === "error" && <> — {run.error}</>}
          </p>
          {run.per_site && (
            <table>
              <thead><tr><th>site</th><th>rows</th><th>new</th><th>error</th></tr></thead>
              <tbody>
                {Object.entries(run.per_site).map(([s, info]) => (
                  <tr key={s}>
                    <td>{s}</td>
                    <td>{info.rows}</td>
                    <td>{info.saved?.inserted ?? "—"}</td>
                    <td className="muted">{info.error || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <h3>fetch history</h3>
      <button onClick={loadHistory}>refresh history</button>
      <table>
        <thead>
          <tr><th>#</th><th>started</th><th>term</th><th>sites</th><th>state</th>
            <th>passes</th><th>new</th><th>target</th><th></th></tr>
        </thead>
        <tbody>
          {history.map((h) => (
            <Fragment key={h.id}>
              <tr>
                <td>{h.id}</td>
                <td className="muted">{(h.started_at || "").slice(0, 16).replace("T", " ")}</td>
                <td>{h.search_term}</td>
                <td className="muted">{h.sites}</td>
                <td><span className="pill">{h.state}</span></td>
                <td>{h.passes} ({h.attempts} attempts)</td>
                <td><b>{h.total_inserted}</b> / {h.total_rows}</td>
                <td>{h.target_new ?? "—"}</td>
                <td>
                  <button onClick={() => setExpanded(expanded === h.id ? null : h.id)}>
                    {expanded === h.id ? "hide" : "passes"}
                  </button>
                </td>
              </tr>
              {expanded === h.id && (
                <tr key={`${h.id}-x`}>
                  <td colSpan={9}><RunAttempts id={h.id} /></td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunAttempts({ id }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    api.run(id).then(setDetail).catch(() => setDetail({ attempts: [] }));
  }, [id]);
  if (!detail) return <span className="muted">loading…</span>;
  return (
    <table>
      <thead>
        <tr><th>pass</th><th>site</th><th>pages</th><th>failed</th>
          <th>rows</th><th>new</th><th>upd</th><th>error</th></tr>
      </thead>
      <tbody>
        {detail.attempts.map((a) => (
          <tr key={a.id}>
            <td>{a.pass_no}</td>
            <td>{a.site}</td>
            <td>{a.pages}</td>
            <td>{a.failed_pages || ""}</td>
            <td>{a.rows}</td>
            <td><b>{a.inserted}</b></td>
            <td>{a.updated}</td>
            <td className="muted">{a.error || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- manual + lessons

function ManualView() {
  const [form, setForm] = useState({ title: "", company: "", location: "", job_url: "", description: "" });
  const [msg, setMsg] = useState("");
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const submit = async () => {
    try {
      const r = await api.manualJob(form);
      setMsg(`saved as job #${r.id}`);
      setForm({ title: "", company: "", location: "", job_url: "", description: "" });
    } catch (e) {
      setMsg(e.message);
    }
  };
  return (
    <div>
      <h2>manual entry</h2>
      {["title", "company", "location", "job_url"].map((k) => (
        <label key={k}>
          {k} <input value={form[k]} onChange={(e) => set(k, e.target.value)} />
        </label>
      ))}
      <label>
        description <textarea rows={6} value={form.description} onChange={(e) => set("description", e.target.value)} />
      </label>
      <button onClick={submit} disabled={!form.title.trim() || !form.job_url.trim()}>save job</button>
      {msg && <p>{msg}</p>}
    </div>
  );
}

function LessonsView() {
  const [topic, setTopic] = useState("scoring");
  const [text, setText] = useState("");
  const [draft, setDraft] = useState("");
  const [jobId, setJobId] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const r = await api.lessons(topic);
    setText(r.text);
  }, [topic]);

  useEffect(() => {
    load();
  }, [load]);

  const append = async () => {
    try {
      await api.addNote({
        topic,
        text: draft,
        job_id: jobId === "" ? undefined : Number(jobId),
      });
      setDraft("");
      setJobId("");
      setMsg("appended");
      load();
    } catch (e) {
      setMsg(e.message);
    }
  };

  return (
    <div>
      <h2>lessons</h2>
      <div className="row">
        {(["scoring", "cv"]).map((t) => (
          <button key={t} className={topic === t ? "on" : ""} onClick={() => setTopic(t)}>
            {t}
          </button>
        ))}
      </div>
      <pre className="desc">{text || "(empty)"}</pre>
      <h3>append a job-free lesson</h3>
      <textarea rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="lesson text" />
      <div className="row">
        <label>optional job id <input type="number" value={jobId} onChange={(e) => setJobId(e.target.value)} /></label>
        <button onClick={append} disabled={!draft.trim()}>append</button>
        {msg && <span>{msg}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shell

export default function App() {
  const [view, setView] = useState("board");
  const [openId, setOpenId] = useState(null);
  const [highlightSince, setHighlightSince] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="app">
      <nav>
        <b>job pipeline</b>
        <button className={view === "board" && !openId ? "on" : ""} onClick={() => { setView("board"); setOpenId(null); }}>board</button>
        <button className={view === "search" ? "on" : ""} onClick={() => { setView("search"); setOpenId(null); }}>fetch</button>
        <button className={view === "manual" ? "on" : ""} onClick={() => { setView("manual"); setOpenId(null); }}>manual entry</button>
        <button className={view === "lessons" ? "on" : ""} onClick={() => { setView("lessons"); setOpenId(null); }}>lessons</button>
      </nav>
      {view === "board" &&
        (openId ? (
          <Detail id={openId} onBack={() => setOpenId(null)} onChanged={() => setRefreshKey((k) => k + 1)} />
        ) : (
          <Board onOpen={setOpenId} highlightSince={highlightSince} refreshKey={refreshKey} />
        ))}
      {view === "search" && (
        <SearchView
          onDone={(since) => {
            setHighlightSince(since);
            setRefreshKey((k) => k + 1);
          }}
        />
      )}
      {view === "manual" && <ManualView />}
      {view === "lessons" && <LessonsView />}
    </div>
  );
}
