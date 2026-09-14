# Dashboard brief — Granted

Paste this whole file into Claude Design.

---

## What changed and why

The dashboard currently renders hardcoded values. It must now render entirely from
a data file the agent writes on every run. Nothing on screen should be typed into
the template.

This is not cosmetic. `data/run.json` states in its own comment that it is written
by the agent. If a judge opens it and finds it cannot produce the numbers on screen,
every figure on the page loses credibility, including the true ones.

## Task

1. Replace all hardcoded values with reads from `run.json`, per the schema below.
2. Fetch `run.json` at page load and re-fetch on an interval so the page updates
   itself without a rebuild.
3. Handle the states the data can actually be in: silent run, stale data, fetch
   failure, cold start with no archive.
4. Keep every existing panel and all existing copy. The writing is good; only the
   data binding changes.
5. Add responsive breakpoints. There are currently none.

## Live update mechanism

The page must never call Find a grant or 360Giving directly. Neither service sets
CORS headers, so a browser fetch fails. The agent calls them; the page only reads
what the agent wrote.

```
  scheduled agent run  →  writes run.json to S3  →  dashboard fetches same-origin
```

Implement as:

```js
const POLL_MS = 60_000;

async function load() {
  const res = await fetch(`run.json?t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`run.json ${res.status}`);
  const data = await res.json();
  if (data.schema_version !== 2) console.warn('schema drift', data.schema_version);
  return data;
}
```

Cache-bust with the query param. Without it S3 serves a stale copy and the page
looks frozen. Poll rather than websocket; the agent runs on a cron measured in
hours, so a minute of latency is irrelevant and a socket is a dependency you do
not need.

## Required states

| State | Condition | What to show |
| --- | --- | --- |
| Silent | `surfaced` is empty | The main view. Not an error, not an empty state. "Nothing worth your time today" with the count considered and the running total not pursued. This is the product working. |
| Stale | `run_date` older than 2 days | A quiet marker next to the run timestamp. Do not hide the data. |
| Unreachable | fetch throws | Keep the last good render, mark it as last known. Never blank the page. |
| Cold start | `archive.n_decided` is 0 | Voice and track-record panels explain they need a bid archive, rather than showing zeroes. |
| Vetoed | `surfaced[].vetoed` non-null | Band shows Low with the veto string. This is the withdrawal recommendation and it should be visually distinct from an ordinary Low. |

## Schema — run.json, version 2

Every field is produced by a named module. `_produced_by` in the file maps groups
to modules; surface that mapping somewhere small, it is a credibility asset.

```
run_date            str   "2026-09-09"
run_time            str   "21:26"
model               str   "us.anthropic.claude-haiku-4-5-20251001-v1:0"
provider            str   "bedrock" | "anthropic"
duration_s          float
model_calls         int
threshold           int   triage fit threshold, currently 65
calls_considered    int
schema_version      int   2

bands               { High: int, Medium: int, Low: int }

surfaced            [ ]   opportunities that cleared the gate
  funder                    str
  programme                 str
  band                      "High" | "Medium" | "Low"
  fit                       int    0-100 composite
  alignment                 int    0-100
  plausibility              int    0-100
  plausibility_confidence   "anecdotal" | "indicative" | "reasonable"
  days_left                 int | null
  closes                    str | null   ISO date
  url                       str
  vetoed                    str | null   veto reason, e.g. hours vs days left
  reasons                   [str]  up to 3, from the alignment scorer

not_pursued         [ ]   same shape. Drives "Not pursued · N · cost £0"

watcher_state
  tracked                   int
  by_status                 { upcoming|open|closing_soon|final_call|closed|withdrawn : int }
  silent                    int    tracked minus surfaced changes
  in_flight                 object | absent
    deadline                str
    days_left               int
    overdue                 [str]
    due_now                 [str]
    hours_estimate          float
    feasible                bool     false drives "Withdraw and keep the time"
    summary                 str      pre-written sentence, render verbatim

changes             [ ]   what surfaced this run
  kind                      "new" | "deadline_moved" | "final_call" | "withdrawn" | "behind_schedule"
  funder                    str
  detail                    str
  urgent                    bool

archive
  n_files                   int
  n_decided                 int
  n_awarded                 int
  n_unknown_outcome         int    drives "Outcome is pending"
  win_rate                  float | null
  confidence                "anecdotal" | "indicative" | "reasonable"
  median_successful_ask     float | null
  median_unsuccessful_ask   float | null

revisit             [str]  "Won from, but not approached since"

scope
  files_seen                int
  files_read                int
  files_skipped             int
  skipped_detail            [ { name: str, reason: str } ]

voice
  corpus_documents          int
  corpus_sentences          int
  median_sentence_words     float
  sentence_word_sd          float
  beneficiary_term          str | null
  first_person_plural       bool
  flags
    tells                   [str]
    uniformity              str | null
    term_violations         [str]   drives the "it wrote X where you say Y" line
    clean                   bool

grounded            int
total_claims        int
gaps                int
```

## Binding notes

`win_rate` is a fraction. Render as a percentage and always alongside
`confidence` and `n_decided`. Never show the rate on its own; at n=17 it is
indicative, not a statistic.

`in_flight.summary` is written by the timeline module and already reads as a
sentence. Render it verbatim rather than reassembling it from parts.

`vetoed` is the most distinctive output in the product. Give it its own treatment.

`plausibility_confidence` must appear anywhere `plausibility` does. The
"Not a probability" line already in the design is the right framing; keep it.

`skipped_detail` is a trust asset, not a debug log. "21 read, 7 skipped" with the
reasons available on expand.

`bands` should be visible in the portfolio view. The current design shows a fit
score but never the band label, which is the user-facing vocabulary.

## Responsive

No breakpoints currently exist. Add at least one at 768px. Panels stack, tables
become stacked rows.

## Do not change

- The copy. "Not a probability", "recommending withdrawal is a legitimate output",
  "What would make it speak again", "Files on disk, not rows in a database",
  "nothing is saved until you say so". All of it stays.
- The design system. Tokens only, no hardcoded hex values.
- Semantic landmarks and aria attributes already added.

## Attribution required in the footer

> Contains public sector information licensed under the Open Government Licence
> v3.0. Source: Find a grant, GOV.UK. Grant award data from 360Giving, CC-BY-SA.

## Deliverable

One self-contained HTML file that runs from a static host with `run.json` beside
it. No build step, no bundler, no framework. It must work when opened from an S3
static site with nothing else deployed.
