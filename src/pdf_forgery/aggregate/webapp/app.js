/* Claim Document Review — reviewer UI controller.
   Talks to the Stage 6 job API: upload -> poll per-stage progress -> verdict
   hero (from the scrubbed result) -> streamed advisory (SSE) -> drill-down.
   Everything it renders is a descriptor or advisory prose — never raw text. */

"use strict";

// --- Inline icons (stroke, currentColor; no emoji) ----------------------
const I = {
  triangle: `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4.5l8.5 14.5H3.5L12 4.5z"/><path d="M12 10v4"/><circle cx="12" cy="16.6" r=".4" fill="currentColor" stroke="none"/></svg>`,
  alertCircle: `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 8v4.5"/><circle cx="12" cy="16" r=".4" fill="currentColor" stroke="none"/></svg>`,
  shieldCheck: `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.4-3 7.3-7 8.5C8 17.3 5 14.4 5 10V6l7-3z"/><path d="M9.3 11.6l1.9 1.9 3.5-3.7"/></svg>`,
  help: `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.4 2.4 0 0 1 4.6.9c0 1.6-2.2 2-2.2 3.4"/><circle cx="12" cy="16.4" r=".4" fill="currentColor" stroke="none"/></svg>`,
  check: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4 4 10-10.5"/></svg>`,
  x: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 7l10 10M17 7L7 17"/></svg>`,
  chevron: `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5l6 6 6-6"/></svg>`,
  info: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v4.5"/><circle cx="12" cy="8" r=".4" fill="currentColor" stroke="none"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 8v4.5"/><circle cx="12" cy="16" r=".4" fill="currentColor" stroke="none"/></svg>`,
  arrowLeft: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="M11 6l-6 6 6 6"/></svg>`,
  doc: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>`,
};

// --- Static metadata ----------------------------------------------------
const STAGE_META = {
  revision_recovery: { name: "Revision history", desc: "Recovers earlier saved versions to detect edited text" },
  font_forensics: { name: "Font consistency", desc: "Detects mismatched or substituted fonts" },
  invoice_arithmetic: { name: "Invoice arithmetic", desc: "Re-checks totals and line-item math" },
  provenance_metadata: { name: "Document provenance", desc: "Examines metadata, producers and timestamps" },
  ocr_crosscheck: { name: "Text vs. image", desc: "Compares the text layer against the rendered page" },
};

const TIER_META = {
  high: { label: "High", icon: I.triangle, headline: "Strong indicators — review recommended" },
  medium: { label: "Medium", icon: I.alertCircle, headline: "Some indicators — review suggested" },
  low: { label: "Low", icon: I.shieldCheck, headline: "No tampering indicators found" },
  inconclusive: { label: "Inconclusive", icon: I.help, headline: "Could not be assessed — manual review needed" },
};

const STATE_LABEL = { queued: "Queued", running: "Running", done: "Done", error: "Could not run", skipped: "Skipped" };

// --- DOM helpers --------------------------------------------------------
const $ = (id) => document.getElementById(id);
const views = ["upload", "processing", "result", "error"];
function showView(name) {
  views.forEach((v) => $(`view-${v}`).classList.toggle("hidden", v !== name));
}
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}
function tierClass(t) { return `tier-${t || "inconclusive"}`; }
function stageName(s) { return (STAGE_META[s] && STAGE_META[s].name) || s; }
function humanize(s) { return s ? String(s).replace(/_/g, " ") : ""; }

// --- App state ----------------------------------------------------------
let pollTimer = null;
let advisorySource = null;
let currentFilename = "";

function reset() {
  if (pollTimer) clearTimeout(pollTimer);
  if (advisorySource) advisorySource.close();
  pollTimer = null;
  advisorySource = null;
}

function goToUpload() {
  reset();
  $("file-input").value = ""; // so re-selecting the same file re-fires change
  currentFilename = "";
  showView("upload");
}

// =====================================================================
// Upload
// =====================================================================
function initUpload() {
  const zone = $("dropzone");
  const input = $("file-input");
  const browse = $("browse-btn");

  browse.addEventListener("click", (e) => { e.stopPropagation(); input.click(); });
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) submit(input.files[0]);
  });

  ["dragenter", "dragover"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-dragging"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("is-dragging"); })
  );
  zone.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) submit(f);
  });
}

async function submit(file) {
  reset();
  currentFilename = file.name;
  renderProcessing(file.name);
  showView("processing");

  const body = new FormData();
  body.append("file", file, file.name);
  let res;
  try {
    res = await fetch("/v1/documents", { method: "POST", body });
  } catch (err) {
    return renderError("The document could not be uploaded. Check your connection and try again.");
  }
  if (res.status !== 202) {
    let detail = "This document could not be accepted for review.";
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    return renderError(detail);
  }
  const { job_id } = await res.json();
  poll(job_id);
}

// =====================================================================
// Processing
// =====================================================================
function renderProcessing(filename) {
  const rows = Object.keys(STAGE_META).map((s) => stageRow(s, "queued")).join("");
  $("view-processing").innerHTML = `
    <div class="proc__head">
      <h1 class="proc__title">Analysing document</h1>
      <span class="proc__file mono">${esc(filename)}</span>
    </div>
    <p class="proc__sub">Running each forensic detector. The verdict appears the moment they finish.</p>
    <div class="progress-track"><div class="progress-fill" id="proc-fill"></div></div>
    <div class="stage-list" id="stage-list">${rows}</div>`;
}

function stageRow(stage, state) {
  const meta = STAGE_META[stage] || { name: stage, desc: "" };
  return `
    <div class="stage-row is-${state}" data-stage="${stage}">
      <div class="stage-row__indicator">${stageIndicator(state)}</div>
      <div class="stage-row__text">
        <div class="stage-row__name">${esc(meta.name)}</div>
        <div class="stage-row__desc">${esc(meta.desc)}</div>
      </div>
      <div class="stage-row__state">${STATE_LABEL[state] || state}</div>
    </div>`;
}

function stageIndicator(state) {
  if (state === "running") return `<div class="spinner"></div>`;
  if (state === "done") return I.check;
  if (state === "error") return I.x;
  return `<div class="dot-idle"></div>`;
}

function updateProcessing(stages) {
  let done = 0;
  stages.forEach((s) => {
    if (s.state === "done" || s.state === "error" || s.state === "skipped") done += 1;
    const row = document.querySelector(`.stage-row[data-stage="${s.stage}"]`);
    if (!row) return;
    row.className = `stage-row is-${s.state}`;
    row.querySelector(".stage-row__indicator").innerHTML = stageIndicator(s.state);
    row.querySelector(".stage-row__state").textContent = STATE_LABEL[s.state] || s.state;
  });
  const fill = $("proc-fill");
  if (fill) fill.style.width = `${(done / stages.length) * 100}%`;
}

// =====================================================================
// Polling
// =====================================================================
function poll(jobId) {
  const tick = async () => {
    let status;
    try {
      const res = await fetch(`/v1/jobs/${jobId}`);
      if (!res.ok) throw new Error("status " + res.status);
      status = await res.json();
    } catch (err) {
      return renderError("Lost contact with the analysis service while processing.");
    }
    if (status.stages) updateProcessing(status.stages);

    if (status.state === "done") {
      renderResult(status.result);
      connectAdvisory(jobId);
      return;
    }
    if (status.state === "error") {
      return renderError(status.error || "The document could not be analysed.");
    }
    pollTimer = setTimeout(tick, 600);
  };
  tick();
}

// =====================================================================
// Result — verdict hero + advisory + breakdown
// =====================================================================
function renderResult(result) {
  const tier = result.tier || "inconclusive";
  const meta = TIER_META[tier] || TIER_META.inconclusive;
  const scoreShown = result.score === null || result.score === undefined;
  const scoreBlock = scoreShown
    ? `<div class="hero__score"><span class="hero__score-num mono">—</span></div>`
    : `<div class="hero__score"><span class="hero__score-num mono">${result.score}</span><span class="hero__score-max">/100</span></div>`;

  const actions = `
    <div class="result__actions">
      <button class="btn btn--ghost" type="button" id="new-review-btn">
        ${I.arrowLeft}Review another document
      </button>
    </div>`;

  const hero = `
    <div class="hero ${tierClass(tier)}">
      <div class="hero__band">
        <span class="hero__icon">${meta.icon}</span>
        <span class="hero__tier">${meta.label} confidence</span>
        <span class="hero__chip">${findingsSummary(result.findings)}</span>
      </div>
      <div class="hero__doc">
        ${I.doc}<span class="hero__file mono">${esc(currentFilename || "document.pdf")}</span>
      </div>
      <div class="hero__main">
        ${scoreBlock}
        <p class="hero__headline">${esc(meta.headline)}</p>
      </div>
      <p class="hero__caveat">${I.info}<span>Advisory only — a reviewer makes the final decision.</span></p>
    </div>`;

  const advisory = `
    <div class="card advisory">
      <div class="section-label">Assessment summary</div>
      <p class="advisory__body" id="adv-summary"><span class="caret"></span></p>
      <p class="advisory__tier-statement hidden" id="adv-tier"></p>
      <p class="advisory__meta hidden" id="adv-meta"></p>
    </div>`;

  const breakdown = `
    <div>
      <div class="section-label">Detector breakdown</div>
      <div class="breakdown__list">${result.stages.map((s) => stageCard(s, result.findings)).join("")}</div>
    </div>`;

  $("view-result").innerHTML = `<div class="result">${actions}${hero}${advisory}${breakdown}</div>`;
  bindAccordions();
  $("new-review-btn").addEventListener("click", goToUpload);
  showView("result");
}

function findingsSummary(findings) {
  const n = (findings || []).length;
  if (n === 0) return "No findings";
  return n === 1 ? "1 finding" : `${n} findings`;
}

// Group findings by (type, token_class) — client-side mirror of advisory._group_findings.
function groupFindings(findings) {
  const map = {};
  (findings || []).forEach((f) => {
    const key = `${f.type}|${f.token_class || ""}`;
    if (!map[key]) {
      map[key] = { type: f.type, token_class: f.token_class, tier: f.tier,
                   count: 0, pages: new Set(), finding_ids: [] };
    }
    map[key].count++;
    if (f.page !== null && f.page !== undefined) map[key].pages.add(f.page);
    map[key].finding_ids.push(f.finding_id);
    // Worst-case tier escalation.
    const order = { high: 3, medium: 2, low: 1, inconclusive: 0 };
    if ((order[f.tier] || 0) > (order[map[key].tier] || 0)) map[key].tier = f.tier;
  });
  return Object.values(map).map((g) => ({
    ...g, pages: [...g.pages].sort((a, b) => a - b),
  }));
}

function stageCard(stage, allFindings) {
  const findings = (allFindings || []).filter((f) => f.stage === stage.stage);
  const groups = groupFindings(findings);
  const open = groups.length > 0;
  const countText = stage.ok === false
    ? "Could not run"
    : groups.length === 0
      ? "No findings"
      : groups.length === 1 ? "1 finding group" : `${groups.length} finding groups`;

  let body;
  if (stage.ok === false) {
    body = `<p class="stage-note">This detector could not run on the document. The others are unaffected.</p>`;
  } else if (groups.length === 0) {
    body = `<p class="stage-note">No findings from this detector.</p>`;
  } else {
    body = groups.map(findingGroupRow).join("");
  }

  return `
    <div class="stage-card ${open ? "is-open" : ""} ${tierClass(stage.tier)}">
      <button class="stage-card__header" type="button" aria-expanded="${open}">
        <span class="mini-tier"><span class="mini-tier__dot"></span>${(TIER_META[stage.tier] || TIER_META.inconclusive).label}</span>
        <span class="stage-card__name">${esc(stageName(stage.stage))}</span>
        <span class="stage-card__count">${countText}</span>
        <span class="stage-card__chevron">${I.chevron}</span>
      </button>
      <div class="stage-card__panel"><div class="stage-card__panel-inner">
        <div class="stage-card__body">${body}</div>
      </div></div>
    </div>`;
}

function findingGroupRow(g) {
  const chips = [];
  chips.push(`<span class="chip chip--type">${esc(humanize(g.type))}</span>`);
  if (g.token_class) chips.push(`<span class="chip chip--token">${esc(g.token_class)} field</span>`);
  if (g.count > 1) chips.push(`<span class="chip chip--count">${g.count}&times;</span>`);
  if (g.pages.length === 1) {
    chips.push(`<span class="chip chip--page">page ${g.pages[0] + 1}</span>`);
  } else if (g.pages.length > 1) {
    chips.push(`<span class="chip chip--page">pages ${g.pages[0] + 1}–${g.pages[g.pages.length - 1] + 1}</span>`);
  }
  const groupIds = g.finding_ids.join(" ");
  return `
    <div class="finding" data-group-ids="${esc(groupIds)}">
      <div class="finding__top">${chips.join("")}</div>
      <div class="finding__expl" data-group-ids="${esc(groupIds)}">
        <p class="finding__rationale">Flagged for reviewer attention.</p>
      </div>
    </div>`;
}

function bindAccordions() {
  document.querySelectorAll(".stage-card__header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".stage-card");
      const open = card.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", String(open));
    });
  });
}

// =====================================================================
// Advisory (SSE) — streams in after the verdict is shown
// =====================================================================
function connectAdvisory(jobId) {
  const summaryEl = $("adv-summary");
  summaryEl.innerHTML = '<span class="caret"></span>';
  let text = "";

  advisorySource = new EventSource(`/v1/jobs/${jobId}/advisory`);

  advisorySource.addEventListener("chunk", (e) => {
    try { text += JSON.parse(e.data).text; } catch (err) { return; }
    summaryEl.innerHTML = esc(text) + '<span class="caret"></span>';
  });

  advisorySource.addEventListener("done", (e) => {
    let out;
    try { out = JSON.parse(e.data); } catch (err) { out = null; }
    if (out) finishAdvisory(out);
    advisorySource.close();
  });

  advisorySource.addEventListener("error", (e) => {
    // Named server error event OR a transport error — degrade gracefully.
    if (advisorySource.readyState !== EventSource.CLOSED && text === "") {
      summaryEl.textContent =
        "The plain-language explanation is unavailable, but the verdict and detector findings above stand on their own.";
    } else {
      summaryEl.innerHTML = esc(text); // drop the caret on disconnect
    }
    advisorySource.close();
  });
}

function finishAdvisory(out) {
  $("adv-summary").textContent = out.summary || "";

  const tierEl = $("adv-tier");
  if (out.tier_statement) {
    tierEl.textContent = out.tier_statement;
    tierEl.classList.remove("hidden");
  }

  const metaEl = $("adv-meta");
  if (out.model) {
    metaEl.textContent = `Generated by ${out.model}. Advisory only.`;
    metaEl.classList.remove("hidden");
  }

  // Fill group explanations: match DOM elements by finding_id intersection.
  (out.group_explanations || []).forEach((g) => {
    const idSet = new Set(g.finding_ids);
    document.querySelectorAll(".finding__expl[data-group-ids]").forEach((el) => {
      const elIds = (el.getAttribute("data-group-ids") || "").split(" ").filter(Boolean);
      if (!elIds.some((id) => idSet.has(id))) return;
      el.innerHTML = `
        <dl class="expl">
          <div class="expl__row"><dt class="expl__label">Found</dt><dd>${esc(g.what_we_found)}</dd></div>
          <div class="expl__row"><dt class="expl__label">Why it matters</dt><dd>${esc(g.why_it_matters)}</dd></div>
          <div class="expl__row"><dt class="expl__label">Check</dt><dd>${esc(g.what_to_check)}</dd></div>
        </dl>`;
    });
  });
}

// =====================================================================
// Error
// =====================================================================
function renderError(message) {
  reset();
  $("view-error").innerHTML = `
    <div class="card error-card">
      <div class="error-card__icon">${I.alert}</div>
      <h1 class="error-card__title">We couldn’t review this document</h1>
      <p class="error-card__msg">${esc(message)}</p>
      <button class="btn btn--ghost" type="button" id="retry-btn">Try another file</button>
    </div>`;
  $("retry-btn").addEventListener("click", goToUpload);
  showView("error");
}

// --- boot ---------------------------------------------------------------
initUpload();
