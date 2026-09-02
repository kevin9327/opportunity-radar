# Devpost submission fields — Opportunity Radar

Paste-ready text for each field on the "Additional info" step.

---

## Track

**Alexa+** (primary) — a self-hosted MCP server, spec 2025-11-25, Streamable HTTP.

## Mini challenge

**Open Source.**

- Repo URL: https://github.com/kevin9327/opportunity-radar
- GitHub username: **kevin9327**
- Licence: MIT, visible at the top of the repo
- Contribution URL: https://github.com/kevin9327/opportunity-radar/commits/main

**What I did:** Opportunity Radar is a new open-source project, published under MIT
during the hackathon window and developed entirely in the open — every commit is on
`main`, including the ones that fixed my own bugs.

**How it works:** the repo is the whole product, not a demo shell. It contains the MCP
server (`radar/server.py`), the six tools (`radar/tools.py`), the local-model ranking
layer (`radar/rank.py`), the Alexa+ simulator (`web/`), the test suite
(`tests/test_radar.py`, 21 tests), the CI that runs it offline on every push and hits
the live source weekly, and the recording pipeline used to make the demo video
(`demo/capture.py`) so the video itself is reproducible.

**Why it matters:** anyone can self-host this and point their own Alexa+ at it — no key,
no cloud account, no card. The honesty gates are the part I hope gets reused: the pattern
of letting a model set a *score* while facts stay pinned to the source is general, and
the tests that enforce it are only a few dozen lines.

---

## Product feedback (required)

**MCP (spec 2025-11-25, Streamable HTTP)** — *would build with again: yes.*

I implemented the transport by hand rather than taking an SDK, and the spec was clear
enough that this took an evening. The single-endpoint design with an optional SSE stream
is a good fit for a small self-hosted server: no long-lived connection to babysit, and
`curl` is a working client, which made debugging trivial. Generating `tools/list` schemas
from Python type hints and docstrings meant tool descriptions could never drift from the
code — the docstring the model reads *is* the docstring a developer reads.

What worked less well: the spec is precise about the shape of messages but quiet about
what a *good* tool description contains, and that turns out to matter more than the
transport. My first tool descriptions were accurate and useless — the router picked
plausible-but-wrong tools until I rewrote each description around the question a person
would ask ("What do I have to act on this week?") instead of what the function does.
A short "writing tool descriptions for voice" section in the docs would have saved me
two hours, because on a screen a wrong tool call is visible and on a speaker it is not.

Onboarding was genuinely easy: no account, no key, no SDK. That is rare and worth keeping.

**Alexa+ / Agent Skills** — *no blockers, but one gap.*

The MCP-as-integration-surface decision is the right one; it meant this project works
with Alexa+ and with every other MCP client at once, which is why I could develop against
a local client and a browser simulator rather than a device.

The gap: the docs describe *how* to expose a tool but not how the assistant behaves around
a slow one. `rank_for_me` takes about 26 seconds because it runs a local model over several
opportunities. That is fine on a screen with a spinner and probably unacceptable out loud,
but I could not find guidance on the expected budget for a spoken turn, whether partial or
progressive responses are supported, or whether a tool should return "working on it" and
push a result later. I designed around it by making a fast path the default, but that was
a guess. Documented latency expectations — and a supported pattern for long-running work —
would change how people build voice tools more than any SDK feature.

**Ollama + Llama 3.1 8B (local ranking)** — *would use again.*

Chosen deliberately over a hosted model: the sentence describing someone's situation
("grants for a family caring for a disabled child") should not have to leave their house.
The 8B model is good enough to judge relevance and write a one-line reason, and JSON mode
made parsing reliable. It correctly identified that a solo developer is not eligible for a
tribal-organisation grant — a judgement I did not hand-code.

Rough edge, and it cost me the most time in this project: **Ollama serialises requests by
default**. Parallelising my calls changed nothing until I found `OLLAMA_NUM_PARALLEL`, and
setting it as a user environment variable plus restarting the app did not take effect in
my session either. For an interactive product, the default of one-at-a-time is a
performance cliff you hit only after your architecture assumes otherwise.

**grants.gov API** — *the reason this project is possible.*

Public, keyless, generous. `search2` and `fetchOpportunity` gave me everything: counts,
close dates, award ceilings, applicant types. Being able to build a working product with
zero credentials is exactly why I could ship this without a cloud account.

Two real problems, both of which produced bugs in my code before I caught them — see the
friction log.

---

## Friction log

### 1. Two date formats in one API (severity: high)

**Task:** show how many days are left before an opportunity closes.

**Steps:** `search2` returns `closeDate` as `10/09/2026`. I wrote a parser for that,
tested it, shipped it. Then `opportunity_detail` (which calls `fetchOpportunity`) started
reporting `days_left: null` for everything.

**Expected:** the same field in the same API in the same format.
**Actual:** detail returns `Oct 09, 2026 12:00:00 AM EDT`.

**Why it is severe despite being trivial to fix:** nothing errored. The tool returned a
well-formed response with a null where a number belonged, and the spoken answer simply
omitted the deadline. In a funding assistant, a silently missing deadline is the failure
mode that costs someone the application. A format mismatch that throws is a five-minute
bug; one that returns `null` is a bug you ship.

**Workaround:** parse both, plus a regex that strips a trailing time and zone. A test now
pins the long form.

**Suggestion:** return an ISO-8601 field (`close_date_iso`) alongside the display strings.
One machine-readable date across both endpoints removes an entire class of quiet bug.

### 2. "Others (see text field…)" as an eligibility answer (severity: high)

**Task:** answer "can an individual apply for this?"

**Steps:** `applicantTypes` is a clean list, so I matched the requested applicant type
against it and returned true or false.

**Expected:** a list of who may apply.
**Actual:** for a real notice the entire list was
`Others (see text field entitled "Additional Information on Eligibility" for clarification)`.
My tool confidently returned **not eligible** — for an opportunity whose eligibility rules
it had not read.

**Why it matters:** this is the difference between a tool that is wrong and a tool that is
harmful. Telling a qualified applicant they cannot apply ends their search. That bug
became the `see_notice` state, which now says the notice points to its own prose and is
worth reading — and a test asserts the tool never claims ineligibility from a pointer.

**Suggestion:** expose a boolean like `eligibility_in_prose_only: true` on the
opportunity. Consumers cannot distinguish "restricted to these types" from "see the
document" without string-matching English, and the two mean opposite things to a user.

### 3. Screen recording is a privacy decision, not a video step (severity: medium)

**Task:** record the demo video required for submission.

**Steps:** captured the desktop with ffmpeg's `gdigrab`, as every tutorial suggests.

**Expected:** a recording of my app.
**Actual:** a recording of my app *and* an unrelated window that happened to be on screen.
I deleted it and rewrote the recorder to attach to Chrome over the DevTools protocol and
pull frames from the page itself (`demo/capture.py`, in the repo).

**Suggestion for hackathon guidance rather than a product:** a line in the submission
instructions — "record the app window or the browser tab, not the desktop" — would protect
entrants who are recording at 2am on a work machine. Two CDP gotchas worth documenting
alongside it: Chrome needs `--remote-allow-origins=*` or the WebSocket handshake 403s, and
`Page.screencastFrame` only fires when the page changes, so a still moment collapses unless
you record frame timestamps and rebuild the timeline yourself.
