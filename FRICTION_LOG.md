# Friction log

Three things that cost me real time while building Opportunity Radar, written up
the way the hackathon asks for them: task, steps, expected vs actual, severity,
workaround, suggestion. Two of the three produced bugs that shipped before I
caught them, and both of those bugs were silent.

---

## 1. Two date formats for one field, in one API — severity: HIGH

**Task.** Show how many days are left before an opportunity closes.

**Steps.** `search2` returns `closeDate` as `10/09/2026`. I wrote a parser for
that, tested it, shipped it. Then `opportunity_detail` — which calls
`fetchOpportunity` — started reporting `days_left: null` for everything.

**Expected.** The same field, in the same API, in the same format.
**Actual.** The detail endpoint returns `Oct 09, 2026 12:00:00 AM EDT`.

**Why it is severe despite being trivial to fix.** Nothing errored. The tool
returned a well-formed response with a `null` where a number belonged, and the
spoken answer simply omitted the deadline. In a funding assistant, a silently
missing deadline is the failure mode that costs someone the application. A
format mismatch that throws is a five-minute bug; one that returns `null` is a
bug you ship.

**Workaround.** Parse both formats, plus a regex that strips a trailing time and
zone (`radar/tools.py`, `_days_left`). A test pins the long form so the parser
cannot regress.

**Suggestion.** Return an ISO-8601 field (`close_date_iso`) alongside the display
strings. One machine-readable date across both endpoints removes an entire class
of quiet bug for every consumer.

---

## 2. "Others (see text field...)" as an eligibility answer — severity: HIGH

**Task.** Answer "can an individual apply for this?"

**Steps.** `applicantTypes` is a clean list, so I matched the requested applicant
type against it and returned true or false.

**Expected.** A list of who may apply.
**Actual.** For a real notice the entire list was
`Others (see text field entitled "Additional Information on Eligibility" for clarification)`.
My tool confidently returned **not eligible** — for an opportunity whose
eligibility rules it had not read.

**Why it matters.** This is the difference between a tool that is wrong and a
tool that is harmful. Telling a qualified applicant they cannot apply ends their
search. That bug became the `see_notice` state, which says the notice points to
its own prose and is worth reading, and a test now asserts the tool never claims
ineligibility from a pointer.

**Workaround.** Three-state eligibility (`eligible` / `see_notice` /
`not_listed`) instead of a boolean.

**Suggestion.** Expose a boolean such as `eligibility_in_prose_only: true` on the
opportunity. Consumers cannot distinguish "restricted to these types" from "see
the document" without string-matching English, and the two mean opposite things
to a user.

---

## 3. Screen recording is a privacy decision, not a video step — severity: MEDIUM

**Task.** Record the demo video required for submission.

**Steps.** Captured the desktop with ffmpeg's `gdigrab`, as most tutorials
suggest.

**Expected.** A recording of my app.
**Actual.** A recording of my app *and* an unrelated window that happened to be
on screen. I deleted the file and rewrote the recorder to attach to Chrome over
the DevTools Protocol and pull frames from the page itself (`demo/capture.py`),
so the capture cannot contain anything outside the tab.

**Suggestion — for hackathon guidance rather than for a product.** One line in
the submission instructions, "record the app window or the browser tab, not the
desktop", would protect entrants recording at 2am on a work machine. Two CDP
gotchas worth documenting alongside it: Chrome needs `--remote-allow-origins=*`
or the WebSocket handshake 403s, and `Page.screencastFrame` only fires when the
page changes, so a still moment collapses unless you record frame timestamps and
rebuild the timeline yourself.
