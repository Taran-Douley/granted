# Demo runbook

About seven minutes. Seven beats. The whole argument is that the tool stays quiet, so the
demo has to earn the moment it finally speaks.

## Before you start

```bash
./demo/prepare.sh          # ~13 min, needs AWS credentials. Do this the night before.
./demo/prepare.sh --live   # ~7 min: only beat 6's folder, against what is open that day
./demo/prepare.sh --check  # 2 sec, do this again right before you present
```

A decision run takes **104 seconds** and makes nine model calls; the silence case takes 85. That is correct
for a background agent on a cron and fatal in front of an audience, so
`prepare.sh` builds the finished folders in advance and the demo shows them.
`onboard` takes about a second and is the one thing safe to run live.

Set up before you start talking:

- Terminal font at 18pt or larger. Half of what you are showing is 11pt mono.
- Three browser tabs, already open: `demo/runs/decision/dashboard.html` (beat 5),
  `demo/runs/live/Granted/Dashboard.html` (beat 6), and the live page,
  http://granted-dashboard-2026.s3-website.us-west-2.amazonaws.com (beat 6; type the `http://`).
- `unset NO_COLOR`, and check the terminal is not on a light-on-white theme.
- `cd` to the project root. Every command below assumes it.

---

## 1 · The problem (30s, no terminal)

> A four-person food bank spends thirty hours on a bid to a funder who has never,
> in nine hundred recorded awards, funded an organisation of their legal form, in
> their region, at their size. That week is gone and nobody told them beforehand.
>
> Every other funding tool helps you write more applications. This one tells you
> when not to.

Do not touch the keyboard yet. Let that land.

## 2 · You do not fill in a form (45s, run this live)

```bash
granted onboard --archive fixtures/rye-lane-funding --out ORG.demo.md \
    --name "Rye Lane Community Kitchen"
```

Point at two things on screen:

- **21 applications read, 12 facts found, 18 gaps left.** It read their filing
  cabinet, not a form.
- **Questions 5 and 6.** These are not from a fixed list — it names the two files
  it could not attribute and the seven with no recorded outcome. Forty questions
  become six, and the six are specific to them.

> The only file you maintain is ORG.md. That is the entire interface.

## 3 · Most days it says nothing (60s)

```bash
cat demo/runs/silence/digests/*.md
```

A Bristol charity against a London funder.

> Nothing to report. And here is the part that matters: it tells you what it
> considered and why each one stopped. An empty screen is indistinguishable from
> a crash. A short accounting is trustworthy.

Read one rejection line aloud — they are specific, not generic:

> *The Clothworkers Foundation has funded only 1 of 300 awards in Bristol (0.3%).*

> Two of these three stopped before a model was ever called. Triage is arithmetic
> over the funder's published award history. Ruling them out cost nothing.

The last two lines are word-identical apart from the numbers. Read one, then say
"and the same again for the third" — reading both aloud sounds like a stuck record.

## 4 · When something fits (90s)

```bash
cat demo/runs/decision/digests/*.md
```

Same funder, a Southwark charity. Walk the sections in order and stop at five:

- **fit 78/100, ask £12,200** — anchored on this funder's real award
  distribution, not on their brochure. £12,200 is their median award.
- **What it checked** — the lookups the matcher chose before deciding. The
  markdown digest does not print these, so show them from the run record:

  ```bash
  grep -A7 '"lookups"' demo/runs/decision/digests/*.json
  ```

  Six lookups print. Read the first and the fifth aloud:

      awards_in_place(Southwark)   → nothing at borough level
      awards_in_place(London)      → checked the wider place before concluding

  > It looked for Southwark, found nothing, and did not stop there. This funder
  > records recipients as "London" — no row will ever say Southwark, however
  > much they fund here. A nil result at one level is a publishing convention,
  > not a refusal. It checked the level above before deciding.

- **Why this one** — reasons traceable to award history, and a separate
  **Your own record** block from their own filing cabinet, labelled *indicative*
  because fourteen decided applications is a thin sample and the tool says so.
- **Evidence and Audit** — two independent checks, on separate rows of the
  table. Slow down here and read both off your screen:

      Evidence            11/12 claims cited (92%)
      Independent audit   revise · 91% grounded

  Then drop to **Before you start**. Its last line is the sentence both checks
  refused:

  > *auditor could not ground: A refrigerated van will let us expand our Food
  > Pantry model beyond our current 190 member households*

  > ORG.md says they need a van. It says they have 190 member households. It
  > never says a van will grow that number. Two mechanisms caught this
  > independently. One re-derives every source line from ORG.md on disk and
  > found nothing that supports this sentence. The other is a different agent
  > reading the draft against that file, and it rejected the same sentence.
  > Agreement between two methods means more than either number on its own.

  > It is a plausible, sympathetic sentence that goes one step past the
  > evidence, and an assessor would ask "how?" The charity has no answer on
  > file, so it comes back as a question for a person, not a line in the bid.

  These numbers are from the current build. A rebuild with `prepare.sh` can
  change them, and can flag a different sentence. If you rebuild, read the
  numbers off the screen and check `04-evidence/audit.json` for the rejected
  claim before you rehearse.

- **Voice** — a term flagged against twenty of their own past bids. Not a house
  style. Theirs.

> Every figure resolves to a line number a trustee can check.

## 5 · The folder and the dashboard (75s)

Switch to the browser tab.

```
demo/runs/decision/
  dashboard.html          rebuilt on every run
  digests/                what happened, for a person and for the dashboard
  workspaces/<grant>/     00-brief … 06-submitted
```

Click through: **Decision → Direction → Timeline → Drafts → Evidence → Files.**

Two things to say while clicking:

> Tick a timeline step and the feasibility verdict recalculates. That is the same
> gesture as a line in `05-timeline/done.txt` — the next run reads it.

> On a call that never cleared the gate, these tabs are struck through. There is
> no workspace, because nothing downstream ran.

End on the **Files** tab:

> These are real files on disk. Not a view of a database. Open them, edit them,
> put them in a shared drive, keep them after this tool is gone.

## 6 · A real morning, every source (75s)

This is not a fixture. `demo/runs/live/Granted` is an installed Granted folder for
the same Southwark charity, run against everything open on 10 September: every open
government grant on Find a grant, the National Lottery Community Fund's programmes,
and Innovate UK's competitions.

```bash
grep -E '^- \*\*[0-9]+ calls' "demo/runs/live/Granted/Daily notes/"*.md
```

Each line is a whole class of call ruled out before a model was called:

    38 calls   nothing in the call matches what you do
    32 calls   only UK research organisations can apply
     8 calls   only businesses can apply or lead
     6 calls   too little time left to do it properly
     5 calls   this call is for Scotland

> 126 calls this morning. 106 of them stopped for nothing: wrong nation, wrong
> kind of organisation, wrong subject, too little time. Eight reached a model.
> That is beat 3's arithmetic at the scale of a real morning, and it is why a
> run costs pennies.

Then the one it kept. Switch to the second tab, `demo/runs/live/Granted/Dashboard.html`:

> This is the dashboard sitting in the charity's own folder, opened with a
> double-click. No server. National Lottery Awards for All England, fit 79, ask
> £7,706. Leave it open and tomorrow's run appears here within a minute. Its
> folder is already open and drafted, with the Word documents a trustee would edit.

```bash
ls "demo/runs/live/Granted/Bids/"*/*/*.docx
```

And the money that is on no list:

```bash
sed -n '/## Funders active near you/,/^_Location/p' "demo/runs/live/Granted/Daily notes/"*.md
```

> Most money for a charity this size is local, and local funders do not publish
> their calls anywhere central. So it reads their award histories instead: the
> London Community Foundation made 9 of 100 sampled awards near Southwark. That
> is a list worth a phone call, not a decision.

Last, the third tab: the live page.

> The same run, published. The page only reads the file the agent wrote; it
> never calls a funder's site. And an organisation gets all of this as one
> folder: unzip it, double-click Install, answer four questions.

These numbers are from the current build of `demo/runs/live`. `./demo/prepare.sh --live`
rebuilds it against whatever is open that day, so read the counts off the screen and
check which call it kept before you rehearse. If a rebuild keeps nothing, skip the
decision lines: the free stops and the local funders still make the point.

Say "sampled", never "recent". 360Giving returns awards in no particular order, and
a judge who knows that will ask.

## 7 · It is a background agent (45s)

```bash
cat demo/runs/drift/digests/*.md | head -20
```

Same bid, a week later, after the funder pulled the deadline forward.

> One thing already in flight needs you. It leads with the bid they are three
> weeks into, above any new opportunity — work underway outranks work not
> started. Run it again with nothing changed and it says nothing at all. The
> register remembers what it has already told you.

## Close (20s)

> It runs on a cron. On most days it produces nothing, and that is the product
> working. It surfaces twice: when something genuinely fits, and when something
> already in flight is slipping.

---

## If something breaks

| Symptom | Do this |
| --- | --- |
| A live command hangs | Ctrl-C. Everything is pre-built — `cat` the folder instead. |
| `onboard` says the file exists | `rm ORG.demo.md`, or add `--force`. |
| Bedrock throws AccessDenied | You do not need it. Every beat except #2 reads a file. |
| The dashboard looks empty | You opened the repo copy, not `demo/runs/decision/dashboard.html`. |
| The live page will not load | It is `http://`, not `https://`: retype it. Or show `demo/runs/live/Granted/Dashboard.html` from the folder. With no internet at all, show `demo/runs/live/Granted/Bid folders.html`, which works offline. |
| The live page shows an older run | Republish, with AWS keys loaded: `python scripts/publish.py --bucket granted-dashboard-2026 --run demo/runs/live/Granted/.granted/run.json` |
| Beat 6's counts differ from this page | The live folder was rebuilt. Read them off the screen: the point is the proportion, not the number. |
| A fit score reads differently | Say nothing and carry on. The built folder shows **78**; measured 72&ndash;82 across runs. The decision has not moved once. |

## Do not

- **Do not run a decision live.** 104 seconds of silence will kill the room.
- **Do not skip beat 3.** The silence case *is* the product. Showing only the
  decision makes this an ordinary grant-writing tool.
- **Do not claim it predicts success.** 360Giving publishes awards only — there
  is no rejection data in it. Say the archive is the only rejection record that
  exists, and that the score is fit, not probability. Volunteering that reads as
  rigour; being caught on it does not.
- **Do not call the local funders open calls.** They are funders whose award
  histories show they fund near you. Their calls are on their own websites, and
  saying otherwise is the kind of overclaim this project exists not to make.
