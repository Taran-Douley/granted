"""Build the folder's dashboard.

The agent lives in a folder. Everything it has ever decided is in there as
plain files, and `dashboard.html` sits at the top of it as a way in:

    granted/
      dashboard.html                  <- this
      digests/2026-09-09.{md,json}    <- what happened on a given day
      workspaces/
        2026-09-09-lcf-cornerstone/   <- one folder per grant
          00-brief/  01-decision/  02-direction/
          03-drafts/ 04-evidence/  05-timeline/  06-submitted/

The data is READ OFF DISK AND BAKED IN at build time rather than fetched at
open time. A page opened from `file://` cannot fetch its neighbours -- browsers
refuse it as a cross-origin read -- so a dashboard that loaded its own JSON
would work behind a web server and silently show nothing when someone simply
double-clicks it. Baking in means no server, no install, no configuration: the
file opens and the run is on screen.

The files stay the product. Every panel links to the real markdown or JSON
underneath it, so nothing here is a place where information only exists in a
window. Delete this file and rerun to get it back.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

DASHBOARD = "dashboard.html"

# label, relative path inside a workspace, how to read it
_WORKSPACE_FILES: list[tuple[str, str, str]] = [
    ("Brief", "00-brief/call.md", "text"),
    ("Decision", "01-decision/match.json", "json"),
    ("Direction", "02-direction/angle.md", "text"),
    ("Draft", "03-drafts/answer-1.md", "text"),
    ("Draft (structured)", "03-drafts/answer-1.json", "json"),
    ("Style card", "03-drafts/style-card.md", "text"),
    ("Evidence", "04-evidence/claims.md", "text"),
    ("Timeline", "05-timeline/schedule.json", "json"),
    ("Outcome", "06-submitted/outcome.meta.json", "json"),
]


def _read(path: Path, kind: str) -> Any:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if kind != "json":
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def collect(root: Path, bids: str = "workspaces", digests: str = "digests") -> dict:
    """Everything the folder knows, as one structure.

    `bids` and `digests` name the two folders: `workspaces/` and `digests/` in a
    run folder, `Bids/` and `Daily notes/` in an organisation's Granted folder.
    """
    root = Path(root)
    runs = []
    for path in sorted((root / digests).glob("*.json"), reverse=True):
        data = _read(path, "json")
        if isinstance(data, dict):
            data["digest_md"] = f"{digests}/{path.stem}.md"
            runs.append(data)

    grants = []
    for folder in sorted((root / bids).glob("*/"), reverse=True):
        if not folder.is_dir():
            continue
        grant: dict = {"slug": folder.name, "folder": f"{bids}/{folder.name}", "files": []}
        for label, rel, kind in _WORKSPACE_FILES:
            target = folder / rel
            content = _read(target, kind)
            if content is None:
                continue
            key = label.lower().replace(" ", "_").replace("(", "").replace(")", "")
            grant[key] = content
            grant["files"].append({"label": label, "path": f"{bids}/{folder.name}/{rel}"})
        # The Word copies are what a person opens; list them too.
        for docx in sorted(folder.glob("*/*.docx")):
            rel = docx.relative_to(folder).as_posix()
            grant["files"].append({"label": docx.stem, "path": f"{bids}/{folder.name}/{rel}"})
        # The decision file carries the numbers worth showing on a card.
        match = grant.get("decision") or {}
        grant["fit"] = match.get("fit_score")
        grant["ask"] = match.get("suggested_ask_gbp")
        grant["blocking"] = match.get("blocking") or []
        grant["title"] = _title_of(folder.name)
        grants.append(grant)

    return {
        "built": date.today().isoformat(),
        "root": str(root.resolve()),
        "runs": runs,
        "grants": grants,
    }


def _title_of(slug: str) -> str:
    """`2026-09-09-the-london-community-foundation-cornerstone-fund` -> readable.

    A folder already named for people (`2026-09-10 The Clothworkers Foundation -
    Small Grants Programme`) reads as it stands, so only the date comes off.
    """
    body = slug[11:] if len(slug) > 11 and slug[:4].isdigit() else slug
    if " " in body:
        return body.strip()
    return body.replace("-", " ").strip().title()


def build(root: Path, bids: str = "workspaces", digests: str = "digests",
          filename: str = DASHBOARD) -> Path:
    """Write the dashboard at the top of the folder. Safe to call every run."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data = collect(root, bids=bids, digests=digests)
    payload = json.dumps(data, indent=1, ensure_ascii=False)
    # </script> inside embedded content would close the block early.
    payload = payload.replace("</", "<\\/")
    out = root / filename
    out.write_text(TEMPLATE.replace("__GRANTED_DATA__", payload), encoding="utf-8")
    return out


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog or "granted.dashboard",
        description="Rebuild the dashboard from what is already in the folder.")
    ap.add_argument("--out", default=".", help="the Granted folder")
    args = ap.parse_args(argv)

    root = Path(args.out)
    layout: dict = {}
    if (root / "granted.json").is_file():
        from . import home
        layout = {"bids": home.BIDS, "digests": home.NOTES, "filename": home.BID_FOLDERS}
    bids = layout.get("bids", "workspaces")
    digests = layout.get("digests", "digests")
    if not (root / digests).exists() and not (root / bids).exists():
        print(f"{root} does not look like a Granted folder "
              f"(no {digests}/ or {bids}/). Run granted run first.")
        return 2
    path = build(root, **layout)
    if layout:
        # A Granted folder also gets the designed dashboard and its run script.
        home.write_dashboard(root)
        home.refresh_run_js(root)
    data = collect(root, bids=bids, digests=digests)
    print(f"\n  {path}")
    print(f"  {len(data['runs'])} run(s), {len(data['grants'])} grant folder(s). "
          "Open it in a browser.\n")
    return 0


TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Granted</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600&family=Caprasimo&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--ground:#f9f4ed;--raised:#eee7db;--ink:#201e1d;--body:#645c50;--muted:#82796a;
--faint:#a19786;--rule:#dcd3c4;--strong:#2e2b25;--accent:#b2622d;--soft:#ffd0b3;
--warn:#b07a2b;--focus:#b2622d}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--ground:#171612;
--raised:#201e18;--ink:#eae5d9;--body:#c6bfb0;--muted:#9b9384;--faint:#6f695d;
--rule:#332f26;--strong:#6f695d;--accent:#e08a52;--soft:#6b3f24;--warn:#d6a256;--focus:#e08a52}}
:root[data-theme=dark]{--ground:#171612;--raised:#201e18;--ink:#eae5d9;--body:#c6bfb0;
--muted:#9b9384;--faint:#6f695d;--rule:#332f26;--strong:#6f695d;--accent:#e08a52;
--soft:#6b3f24;--warn:#d6a256;--focus:#e08a52}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:15px/1.55 Figtree,system-ui,sans-serif}
.top h1,.banner .n{font-family:Caprasimo,Georgia,serif;font-weight:400;letter-spacing:0}
.mono,.lbl{font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
.lbl{font-size:10px;letter-spacing:.1em;color:var(--muted);text-transform:uppercase}
button{font:inherit;color:inherit;background:none;border:none;padding:0;cursor:pointer;text-align:left}
a{color:var(--accent)} a:hover{opacity:.75}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
.shell{max-width:1280px;margin:0 auto;padding:30px 26px 52px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:20px;flex-wrap:wrap;
border-bottom:1px solid var(--strong);padding-bottom:12px}
.top h1{margin:0;font-size:25px;font-weight:500;letter-spacing:-.01em}
.banner{display:flex;gap:14px;align-items:baseline;padding:20px 0 22px;border-bottom:1px solid var(--rule)}
.banner .n{font-size:42px;font-weight:300;line-height:1;color:var(--accent)}
.banner p{margin:0;font-size:15px;line-height:1.55;color:var(--body);max-width:66ch;text-wrap:pretty}
.alert{display:flex;gap:12px;align-items:baseline;padding:12px 14px;background:var(--raised);
border-left:3px solid var(--warn);margin:16px 0 0}
.alert p{margin:0;font-size:14px;line-height:1.5;color:var(--body);text-wrap:pretty}
.cols{display:grid;grid-template-columns:minmax(0,300px) minmax(0,1fr);gap:40px;
align-items:start;padding-top:24px}
@media (max-width:920px){.cols{grid-template-columns:minmax(0,1fr)}}
.grant{display:flex;flex-direction:column;gap:5px;width:100%;border-top:1px solid var(--rule);
padding:13px 10px 13px 12px;border-left:3px solid transparent;transition:background .12s}
.grant:hover{background:var(--raised)}
.grant[aria-current=true]{background:var(--raised);border-left-color:var(--accent)}
.grant .gn{font-size:15px;line-height:1.3}
.grant .gs{display:flex;justify-content:space-between;gap:8px;font-size:10px;letter-spacing:.07em;
text-transform:uppercase;color:var(--faint);font-family:'IBM Plex Mono',Menlo,monospace}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--rule);margin-bottom:22px;
overflow-x:auto;scrollbar-width:thin}
.tab{padding:9px 14px;font-family:'IBM Plex Mono',Menlo,monospace;font-size:11px;letter-spacing:.07em;
text-transform:uppercase;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;
white-space:nowrap}
.tab[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.tab[disabled]{color:var(--faint);cursor:not-allowed;text-decoration:line-through}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:24px;padding-bottom:20px;
border-bottom:1px solid var(--rule);margin-bottom:22px}
@media (max-width:640px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
.stat{display:flex;flex-direction:column;gap:4px}
.stat .v{font-size:30px;font-weight:300;line-height:1}
.stat .u{font-size:15px;color:var(--faint)}
h2.sec{font-size:17px;font-style:italic;font-weight:400;margin:0 0 11px}
.rows{display:flex;flex-direction:column;gap:9px;margin-bottom:24px}
.row{display:flex;gap:12px;align-items:baseline}
.row .sg{font-family:'IBM Plex Mono',Menlo,monospace;font-size:11px;flex-shrink:0;width:10px}
.row p{margin:0;font-size:15px;line-height:1.55;color:var(--body);text-wrap:pretty}
.pos{color:var(--accent)}.neg,.flag{color:var(--warn)}
.prose{font-size:16px;line-height:1.68;max-width:68ch;text-wrap:pretty}
.prose p{margin:0 0 14px}
.evid{display:flex;gap:3px;height:6px;margin:10px 0 12px}
.evid i{flex-grow:1;background:var(--rule)}.evid i.on{background:var(--accent)}
.steps{display:flex;flex-direction:column}
.step{display:flex;align-items:baseline;gap:15px;width:100%;border-top:1px solid var(--rule);
padding:12px 8px 12px 0;transition:background .12s}
.step:hover{background:var(--raised)}
.step .bx{font-family:'IBM Plex Mono',Menlo,monospace;font-size:13px;width:14px;flex-shrink:0}
.step .dy{font-family:'IBM Plex Mono',Menlo,monospace;font-size:11px;width:44px;flex-shrink:0}
.step .tk{flex-grow:1;font-size:15px;line-height:1.4;text-wrap:pretty}
.step .ow{font-family:'IBM Plex Mono',Menlo,monospace;font-size:10px;letter-spacing:.06em;
color:var(--faint);width:104px;text-align:right;flex-shrink:0}
.step.done .tk{color:var(--muted);text-decoration:line-through}
@media (max-width:640px){.step .ow{display:none}}
.kv{display:flex;justify-content:space-between;gap:14px;align-items:baseline;padding:6px 0;
border-bottom:1px solid var(--rule)}
.kv .k{font-family:'IBM Plex Mono',Menlo,monospace;font-size:10px;letter-spacing:.06em;
color:var(--faint);text-transform:uppercase}
.kv .v{font-family:'IBM Plex Mono',Menlo,monospace;font-size:12px;text-align:right}
.files{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:2px}
.file{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:11px 12px;
border:1px solid var(--rule);text-decoration:none;color:var(--ink);transition:background .12s}
.file:hover{background:var(--raised);opacity:1}
.file .fl{font-size:15px}
.file .fp{font-family:'IBM Plex Mono',Menlo,monospace;font-size:9px;color:var(--faint)}
.quiet{font-size:14px;font-style:italic;line-height:1.6;color:var(--muted);max-width:66ch;text-wrap:pretty}
pre.raw{background:var(--raised);padding:14px 16px;overflow-x:auto;font-family:'IBM Plex Mono',Menlo,monospace;
font-size:12px;line-height:1.6;color:var(--body);margin:0;border-left:2px solid var(--rule)}
.foot{margin-top:38px;padding-top:13px;border-top:1px solid var(--rule);display:flex;
justify-content:space-between;gap:16px;flex-wrap:wrap}
.empty{padding:40px 0;color:var(--muted);font-style:italic}
</style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div style="display:flex;align-items:baseline;gap:15px;flex-wrap:wrap">
      <h1>Granted</h1><span class="lbl" id="org"></span>
    </div>
    <span class="lbl" id="built"></span>
  </div>
  <div id="banner"></div>
  <div class="cols">
    <div>
      <div class="lbl" style="margin-bottom:7px">Grants in this folder</div>
      <div id="grants"></div>
      <div style="margin-top:24px">
        <div class="lbl" style="margin-bottom:7px">Runs</div>
        <div id="runs"></div>
      </div>
      <div style="margin-top:24px" id="local-wrap" hidden>
        <div class="lbl" style="margin-bottom:7px">Funders active near you</div>
        <div id="local"></div>
      </div>
    </div>
    <div><div class="tabs" id="tabs" role="tablist"></div><div id="pane"></div></div>
  </div>
  <div class="foot">
    <span class="lbl" id="root"></span>
    <span class="lbl">Grant data from 360Giving, CC-BY-SA</span>
  </div>
</div>
<script id="gr-data" type="application/json">__GRANTED_DATA__</script>
<script>
const D = JSON.parse(document.getElementById("gr-data").textContent);
const esc = s => String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const TABS = [["decision","Decision"],["direction","Direction"],["timeline","Timeline"],
              ["drafts","Drafts"],["evidence","Evidence"],["brief","Brief"],["files","Files"]];
const S = { grant: 0, tab: "decision", done: {} };
try { Object.assign(S, JSON.parse(localStorage.getItem("gr-dash") || "{}")); } catch (e) {}
const save = () => { try { localStorage.setItem("gr-dash", JSON.stringify(S)); } catch (e) {} };
const g = () => D.grants[S.grant] || null;
const latest = () => D.runs[0] || null;

function banner() {
  const r = latest(), el = document.getElementById("banner");
  if (!r) { el.innerHTML = ""; return; }
  const dec = r.decision;
  const alerts = (r.alerts || []).map(a =>
    `<div class="alert"><span class="mono" style="color:var(--warn)">${a.urgent ? "!" : "·"}</span>
     <p><strong style="font-weight:500">${esc(a.title)}</strong> &mdash; ${esc(a.detail)}</p></div>`).join("");
  const head = dec
    ? `<span class="n">1</span><p><strong style="font-weight:500">One decision.</strong>
       ${esc(dec.title)} cleared the gate at ${dec.fit}/100.
       ${r.calls_considered} call${r.calls_considered === 1 ? "" : "s"} considered on ${esc(r.run_date)}.</p>`
    : `<span class="n" style="color:var(--muted)">0</span><p><strong style="font-weight:500">Nothing to report.</strong>
       ${r.calls_considered} call${r.calls_considered === 1 ? "" : "s"} considered on ${esc(r.run_date)}, none clearing
       ${r.threshold}/100. Silence is the expected result.</p>`;
  el.innerHTML = `<div class="banner">${head}</div>${alerts}`;
}

function rail() {
  document.getElementById("org").textContent =
    latest() ? `${latest().org} · ${D.grants.length} grant folder${D.grants.length === 1 ? "" : "s"}` : D.root;
  document.getElementById("built").textContent = `Built ${D.built}`;
  document.getElementById("root").textContent = D.root;
  document.getElementById("grants").innerHTML = D.grants.length
    ? D.grants.map((x, i) => `<button class="grant" data-grant="${i}" aria-current="${i === S.grant}">
        <span class="gn">${esc(x.title)}</span>
        <span class="gs"><span>${x.fit != null ? "fit " + x.fit + "/100" : "no decision file"}</span>
        <span>${x.ask != null ? "£" + Number(x.ask).toLocaleString() : ""}</span></span></button>`).join("")
    : `<p class="quiet">No grant folders yet. One is created the first time the gate fires.</p>`;
  document.getElementById("runs").innerHTML = D.runs.length
    ? D.runs.slice(0, 8).map(r => `<div class="kv"><span class="k">${esc(r.run_date)}</span>
        <span class="v"><a href="${esc(r.digest_md)}">${r.decision ? "1 decision" : "silent"}</a></span></div>`).join("")
    : `<p class="quiet">No digests yet.</p>`;
  const near = (latest() && latest().local_funders) || [];
  document.getElementById("local-wrap").hidden = !near.length;
  document.getElementById("local").innerHTML = near.slice(0, 8).map(f => {
    const link = f.website || f.grantnav;
    const name = link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(f.name)}</a>` : esc(f.name);
    const n = f.awards_near_you ? `${f.awards_near_you} near you` : `${f.awards_in_region} in region`;
    return `<div class="kv"><span class="k">${name}</span><span class="v">${n}</span></div>`;
  }).join("");
}

function tabs() {
  const x = g();
  document.getElementById("tabs").innerHTML = TABS.map(([id, label]) => {
    const off = !x || !hasTab(x, id);
    return `<button class="tab" role="tab" data-tab="${id}" ${off ? "disabled" : ""}
      aria-selected="${!off && S.tab === id}"
      title="${off ? "Not produced for this grant" : ""}">${label}</button>`;
  }).join("");
}

function hasTab(x, id) {
  if (id === "files") return (x.files || []).length > 0;
  if (id === "drafts") return !!x.draft_structured || !!x.draft;
  if (id === "evidence") return !!x.evidence;
  if (id === "direction") return !!x.direction;
  if (id === "timeline") return !!x.timeline;
  if (id === "brief") return !!x.brief;
  return !!x.decision;
}

function link(x, label) {
  const f = (x.files || []).find(f => f.label === label);
  return f ? `<a class="lbl" style="color:var(--accent)" href="${esc(f.path)}">Open ${esc(f.path)}</a>` : "";
}

function rows(list, sign, cls) {
  return `<div class="rows">` + list.map(t =>
    `<div class="row"><span class="sg ${cls}">${sign}</span><p>${esc(t)}</p></div>`).join("") + `</div>`;
}

function paneDecision(x) {
  const m = x.decision || {}, r = latest(), dec = r && r.decision;
  const why = dec && dec.title && x.title.toLowerCase().includes(dec.programme ? dec.programme.toLowerCase() : "@")
    ? dec.why : (m.eligibility_met || []);
  return `<div class="stats">
    <div class="stat"><span class="lbl">Fit</span><span class="v" style="color:var(--accent)">${m.fit_score ?? "—"}<span class="u">/100</span></span></div>
    <div class="stat"><span class="lbl">Suggested ask</span><span class="v">${m.suggested_ask_gbp != null ? "£" + Number(m.suggested_ask_gbp).toLocaleString() : "—"}</span></div>
    <div class="stat"><span class="lbl">Recommend apply</span><span class="v" style="font-size:19px">${m.recommend_apply ? "Yes" : "No"}</span></div>
    <div class="stat"><span class="lbl">Blockers</span><span class="v">${(m.blocking || []).length}</span></div>
  </div>
  ${why && why.length ? `<h2 class="sec">Why this one</h2>${rows(why, "+", "pos")}` : ""}
  ${(m.eligibility_gaps || []).length ? `<h2 class="sec" style="color:var(--warn)">Before you start</h2>
    ${rows(m.eligibility_gaps, "—", "flag")}` : ""}
  ${m.reasoning ? `<h2 class="sec">The matcher's reasoning</h2><div class="prose"><p>${esc(m.reasoning)}</p></div>` : ""}
  ${link(x, "Decision")}`;
}

function paneDirection(x) {
  return `<h2 class="sec">The angle to lead with</h2>
    <div class="prose">${esc(x.direction).split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, " ")}</p>`).join("")}</div>
    ${link(x, "Direction")}`;
}

function paneTimeline(x) {
  const t = x.timeline || {}, steps = t.steps || [];
  const left = steps.reduce((a, s, i) => a + (S.done[x.slug + i] ? 0 : (s.hours || 0)), 0);
  const nDone = steps.filter((_, i) => S.done[x.slug + i]).length;
  return `<div class="stats">
    <div class="stat"><span class="lbl">Steps</span><span class="v">${nDone}<span class="u"> of ${steps.length}</span></span></div>
    <div class="stat"><span class="lbl">Estimated effort</span><span class="v">${t.total_hours_estimate ?? "—"}<span class="u"> hrs</span></span></div>
    <div class="stat"><span class="lbl">Deadline</span><span class="v" style="font-size:19px">${esc(t.deadline || "—")}</span></div>
    <div class="stat"><span class="lbl">Remaining</span><span class="v">${left || "—"}<span class="u"> hrs</span></span></div>
  </div>
  <h2 class="sec">Work back from the deadline</h2>
  <div class="steps">${steps.map((s, i) => {
    const d = !!S.done[x.slug + i];
    return `<button class="step ${d ? "done" : ""}" data-step="${i}">
      <span class="bx" style="color:${d ? "var(--accent)" : "var(--faint)"}">${d ? "×" : "○"}</span>
      <span class="dy">${s.days_before_deadline}d</span>
      <span class="tk">${esc(s.task)}</span>
      <span class="ow">${esc(String(s.owner_role || "").toUpperCase())}</span></button>`;
  }).join("")}</div>
  <p class="quiet" style="margin-top:16px">Ticking here is the same gesture as a line in
    <span class="mono">05-timeline/done.txt</span>. Granted reads that file on the next run
    and stays quiet while the plan still fits the time left.</p>
  ${link(x, "Timeline")}`;
}

function paneDrafts(x) {
  const d = x.draft_structured || {};
  const claims = d.claims || [];
  const grounded = claims.filter(c => c.grounded).length;
  return `<h2 class="sec">The question this answers</h2>
    <div class="prose"><p style="font-style:italic">${esc(d.question || "—")}</p></div>
    <div class="evid">${claims.map((c, i) => `<i class="${c.grounded ? "on" : ""}"></i>`).join("")}</div>
    <p class="quiet" style="margin:0 0 20px">${grounded} of ${claims.length} claims trace to a line in ORG.md.</p>
    <h2 class="sec">Draft answer</h2>
    <div class="prose">${esc(x.draft || d.answer || "").split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, " ")}</p>`).join("")}</div>
    ${(d.unevidenced_gaps || []).length ? `<h2 class="sec" style="color:var(--warn)">Needs a human</h2>
      ${rows(d.unevidenced_gaps, "—", "flag")}` : ""}
    ${x.style_card ? `<h2 class="sec">Your measured voice</h2><pre class="raw">${esc(x.style_card)}</pre>` : ""}
    <div style="margin-top:14px">${link(x, "Draft")}</div>`;
}

function paneEvidence(x) {
  const d = x.draft_structured || {}, claims = (d.claims || []).filter(c => c.source_line);
  return `<h2 class="sec">Every figure, and the line it came from</h2>
    ${claims.length ? claims.map(c => `<div style="padding:11px 0;border-bottom:1px solid var(--rule)">
        <p style="margin:0 0 5px;font-size:15px;line-height:1.5">${esc(c.text)}</p>
        <span class="lbl" style="color:var(--accent)">${esc(c.source_heading || "ORG.md")}</span>
        <p style="margin:3px 0 0;font-size:13px;font-style:italic;color:var(--muted)">&ldquo;${esc(c.source_line)}&rdquo;</p>
      </div>`).join("") : `<pre class="raw">${esc(x.evidence || "")}</pre>`}
    <div style="margin-top:16px">${link(x, "Evidence")}</div>`;
}

function paneBrief(x) {
  return `<h2 class="sec">The call, as it stood the day the gate fired</h2>
    <pre class="raw">${esc(x.brief)}</pre>${link(x, "Brief")}`;
}

function paneFiles(x) {
  return `<h2 class="sec">Everything in this grant folder</h2>
    <div class="files">${(x.files || []).map(f =>
      `<a class="file" href="${esc(f.path)}"><span class="fl">${esc(f.label)}</span>
       <span class="fp">${esc(f.path.split("/").slice(2).join("/"))}</span></a>`).join("")}</div>
    <p class="quiet" style="margin-top:18px">These are real files on disk, not a view of a database.
      Open them, edit them, put them in a shared drive, keep them after this tool is gone.</p>`;
}

function render() {
  const x = g();
  if (x && !hasTab(x, S.tab)) S.tab = "decision";
  banner(); rail(); tabs();
  const pane = document.getElementById("pane");
  if (!x) { pane.innerHTML = `<p class="empty">No grant folder selected.</p>`; save(); return; }
  pane.innerHTML =
    S.tab === "direction" ? paneDirection(x) :
    S.tab === "timeline"  ? paneTimeline(x)  :
    S.tab === "drafts"    ? paneDrafts(x)    :
    S.tab === "evidence"  ? paneEvidence(x)  :
    S.tab === "brief"     ? paneBrief(x)     :
    S.tab === "files"     ? paneFiles(x)     : paneDecision(x);
  save();
}

document.addEventListener("click", e => {
  const gr = e.target.closest("[data-grant]"), tb = e.target.closest("[data-tab]"),
        st = e.target.closest("[data-step]");
  if (gr) { S.grant = +gr.dataset.grant; render(); }
  else if (tb && !tb.disabled) { S.tab = tb.dataset.tab; render(); }
  else if (st) { const x = g(), k = x.slug + st.dataset.step; S.done[k] = !S.done[k]; render(); }
});

render();
</script>
</body>
</html>

"""


if __name__ == "__main__":
    raise SystemExit(main())
