## The chore nobody has time for

Every year, billions in public funding goes unclaimed. Not because people don't qualify — because finding out whether you qualify means reading a government database on a Tuesday night, and nobody does that.

The information is public. `grants.gov` publishes every federal opportunity with its deadline, its award ceiling, and the list of who may apply. It is all there. It is just not in a form a person can *ask*.

Alexa+ speaks MCP. So I built the missing half: **a self-hosted MCP server that turns that database into something you can ask out loud.**

## What it does

Six tools, each answering a question someone actually asks:

| Tool | The question |
|---|---|
| `find_opportunities` | "What's open for rural health right now?" |
| `deadline_watch` | "What do I have to act on this week?" |
| `opportunity_detail` | "How much is it, and who runs it?" |
| `check_eligibility_fit` | "Can an individual apply, or only universities?" |
| `rank_for_me` | "Of these, which are worth my Saturday?" |
| `weekly_briefing` | The Monday sweep across everything you care about |

The repo also ships an **Alexa+ simulator** (`web/`) so the whole loop can be seen without an Echo on the desk: speak or type, watch it route to a tool, hear the answer — with the tool name, its arguments, the elapsed time, and the raw JSON on screen next to it. Everything in the demo video is a live call.

## The part I actually care about: it refuses to guess

A wrong funding answer costs someone a weekend, or a filing fee, or a shot at rent money. So the honesty rules here are structural, not a line in a prompt:

- **Source unreachable** → returns `error: source_unavailable` and says "I have no numbers for you", never an estimate.
- **A notice that publishes no applicant list** (just `Others (see text field…)`) → reports `status: see_notice`. It does *not* say "you're ineligible". Turning a pointer-to-prose into a rejection would be the agent inventing a fact.
- **An opportunity with no published deadline** → counted and reported separately, never silently dropped from a "closing soon" list.
- **The model that ranks fit** → may set a *score* and a *reason*. It is never asked for a number. Every deadline and dollar figure is attached from grants.gov *after* the model has spoken, so a hallucinating model cannot invent money.

`tests/test_radar.py::HonestyUnderFailure` asserts each of those. They are tests, not promises.

## How I built it

**The transport, by hand.** `radar/server.py` implements MCP Streamable HTTP (spec 2025-11-25) directly: one endpoint, `initialize` handing out a session id, an optional SSE stream, `tools/list` schemas generated from Python type hints and docstrings, `tools/call` returning both spoken text and `structuredContent`. No framework in the way — which made the spec easy to read off the wire while debugging, and means the repo has no dependency you have to trust.

**The data, live and keyless.** `grants.gov` search needs no API key. Responses cache to disk for six hours so a conversation stays fast and the source stays un-hammered.

**The judgement, on your own machine.** `rank_for_me` prefers a local model through Ollama. No key, no cloud account, no card — and the sentence describing your situation never leaves the house. That last part is not a footnote: the queries people bring to this are *"grants for a family caring for a disabled child"*. Amazon Bedrock and plain keyword overlap are automatic fallbacks, and the engine that actually ran is reported in every response.

## What I learned

**Two date formats in one API.** Search returns `10/09/2026`; detail returns `Oct 09, 2026 12:00:00 AM EDT`. My first parser silently produced `days_left: null` for every detail lookup — the honest-looking failure is the dangerous one, because nothing errors. Now both are parsed and a test pins the long form.

**"Not listed" is not "not eligible".** My first eligibility check returned `False` whenever the applicant type wasn't in the published list. Then I read a real notice: the list said only `Others (see text field entitled "Additional Information on Eligibility")`. My tool would have told a qualified person to give up. That bug became the `see_notice` state.

**Sequential model calls are a design flaw in a voice product.** Judging eight opportunities one after another took most of a minute. Nobody stands in a kitchen for that. The fetches and the judging now run together.

**Recording a demo is a privacy decision.** My first screen capture grabbed the desktop — and swept in an unrelated window. I deleted it and rewrote the recorder to attach to Chrome over CDP and pull frames from the page itself. `demo/capture.py` is in the repo.

## What's next

The weekly briefing is the shape this wants to grow into: not a thing you remember to ask, but something that has already looked while you slept, and tells you the one deadline that matters on Monday morning. `weekly_briefing` is the tool; a scheduled call is all that's missing.
