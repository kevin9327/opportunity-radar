"""Turning a spoken sentence into a tool call and an answer worth hearing.

This is the part Alexa+ normally performs: hear a person, pick the right MCP
tool, call it, say something useful. The simulator in `web/` drives this so the
whole loop can be demonstrated without an Echo on the desk — and so the routing
logic is testable, which it would not be if it lived inside a voice assistant.

Two stages, both deliberately small:

  route()  — decide which tool and arguments. A local model does this when one
             is available; a keyword router handles it otherwise. Either way the
             chosen tool name and arguments are returned, so the UI can show
             exactly what was called.
  speak()  — turn the tool's JSON into two or three sentences a person can hear
             without a screen. Numbers are copied from the result, never
             recomputed, and if the tool returned an error the reply says so.
"""
from __future__ import annotations

import json
import re
import urllib.request

from .rank import OLLAMA_HOST, OLLAMA_MODEL, _local_available
from .tools import (check_eligibility_fit, deadline_watch, find_opportunities,
                    opportunity_detail, rank_for_me, weekly_briefing)

TOOL_FNS = {
    "find_opportunities": find_opportunities,
    "opportunity_detail": opportunity_detail,
    "check_eligibility_fit": check_eligibility_fit,
    "deadline_watch": deadline_watch,
    "weekly_briefing": weekly_briefing,
    "rank_for_me": rank_for_me,
}

ROUTER_PROMPT = """Pick the tool that answers the user's request.

Tools:
- find_opportunities(interest): what funding is open on a topic
- deadline_watch(interest, within_days): what closes soon
- rank_for_me(profile, interest): which openings suit this person best
- check_eligibility_fit(opportunity_id, applicant_type): may this person apply
- opportunity_detail(opportunity_id): details of one opportunity
- weekly_briefing(interests): a sweep across several topics

User said: "{utterance}"

Reply with JSON only:
{{"tool": "<name>", "args": {{...}}}}
Use the user's own words for `interest`/`profile`. Only include arguments the tool takes."""

_NUM = re.compile(r"\b(\d{1,3})\s*(?:day|days)\b")


def route_by_keyword(utterance: str) -> dict:
    """Deterministic fallback router — also what the tests pin behaviour against."""
    u = utterance.lower().strip()
    interest = re.sub(
        r"^(hey |ok )?(alexa|radar)[,\s]*", "", u).strip()
    interest = re.sub(
        r"\b(what|which|any|are there|is there|show me|tell me|find|about|grants?|funding|"
        r"opportunit(?:y|ies)|for me|right now|open|available|please|can i|do i)\b", " ", interest)
    interest = re.sub(r"\s+", " ", interest).strip(" ?.,")

    if any(k in u for k in ("close", "closing", "deadline", "due", "this week", "urgent", "soon")):
        m = _NUM.search(u)
        days = int(m.group(1)) if m else (7 if "week" in u else 30)
        return {"tool": "deadline_watch", "args": {"interest": interest or "grants", "within_days": days}}
    if any(k in u for k in ("best fit", "suit", "worth", "for someone like", "rank", "should i")):
        return {"tool": "rank_for_me", "args": {"profile": interest or "an individual applicant"}}
    if any(k in u for k in ("eligible", "allowed", "can i apply", "qualify")):
        return {"tool": "find_opportunities", "args": {"interest": interest or "grants"}}
    if "briefing" in u or "this week" in u or "catch me up" in u:
        return {"tool": "weekly_briefing", "args": {"interests": interest or "grants"}}
    return {"tool": "find_opportunities", "args": {"interest": interest or "grants"}}


def route_by_model(utterance: str) -> dict | None:
    """Ask the local model to route. Returns None if it is unavailable or unsure."""
    if not _local_available():
        return None
    body = json.dumps({
        "model": OLLAMA_MODEL, "prompt": ROUTER_PROMPT.format(utterance=utterance),
        "stream": False, "format": "json",
        "options": {"temperature": 0.0, "num_predict": 160},
    }).encode()
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.loads(r.read().decode()).get("response", "")
        m = re.search(r"\{.*\}", text, re.S)
        plan = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001 - routing must never be the thing that fails
        return None
    tool = plan.get("tool")
    if tool not in TOOL_FNS or not isinstance(plan.get("args"), dict):
        return None
    return {"tool": tool, "args": plan["args"]}


def route(utterance: str) -> dict:
    plan = route_by_model(utterance)
    if plan:
        plan["router"] = "local-model"
        return plan
    plan = route_by_keyword(utterance)
    plan["router"] = "keyword"
    return plan


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many}"


def speak(tool: str, result: dict) -> str:
    """Compose a spoken answer. Every figure here is copied from `result`."""
    if not isinstance(result, dict):
        return "Something came back that I could not read."
    if result.get("error"):
        return result.get("say") or "That lookup failed, so I have no numbers for you."

    if tool == "find_opportunities":
        rows = result.get("opportunities") or []
        if not rows:
            return f"Nothing open right now for {result.get('interest')}."
        head = rows[0]
        when = f"closing in {head['days_left']} days" if head.get("days_left") is not None else "with no published deadline"
        return (f"I found {_plural(result.get('total_matches', 0), 'opening', 'openings')} "
                f"for {result.get('interest')}. The first is {head['title']}, from {head['agency']}, {when}.")

    if tool == "deadline_watch":
        rows = result.get("closing_soon") or []
        if not rows:
            return (f"Nothing on {result.get('interest')} closes within {result.get('within_days')} days. "
                    f"I checked {result.get('scanned')} openings.")
        first = rows[0]
        rest = f" After that, {len(rows) - 1} more." if len(rows) > 1 else ""
        return (f"{_plural(result.get('count', 0), 'opening closes', 'openings close')} within "
                f"{result.get('within_days')} days. The soonest is {first['title']}, "
                f"in {first['days_left']} days.{rest}")

    if tool == "rank_for_me":
        rows = result.get("ranked") or []
        if not rows:
            return "I could not find anything to rank for that."
        top = rows[0]
        engine = "on this machine" if result.get("engine") == "local" else f"using {result.get('engine')}"
        reason = f" {top['reason']}." if top.get("reason") else ""
        return (f"Ranked {result.get('candidates_considered', len(rows))} openings {engine}. "
                f"Best fit is {top['title']}, scoring {top['score']} out of a hundred.{reason}")

    if tool == "check_eligibility_fit":
        return f"{result.get('title')}: {result.get('verdict')}"

    if tool == "opportunity_detail":
        bits = [str(result.get("title"))]
        if result.get("award_ceiling"):
            bits.append(f"awards up to {result['award_ceiling']:,} dollars")
        if result.get("days_left") is not None:
            bits.append(f"closes in {result['days_left']} days")
        return ", ".join(bits) + "."

    if tool == "weekly_briefing":
        rows = result.get("closing_first") or []
        if not rows:
            return "Nothing open across those interests this week."
        first = rows[0]
        return (f"{_plural(result.get('total_open_found', 0), 'opening is', 'openings are')} open across "
                f"your interests. The one to watch is {first['title']}, closing in {first['days_left']} days.")

    return "Done."


def ask(utterance: str) -> dict:
    """One turn: hear a sentence, call a tool, return what to say and what was called."""
    plan = route(utterance)
    fn = TOOL_FNS[plan["tool"]]
    try:
        result = fn(**plan["args"])
    except TypeError:  # a router hallucinated an argument; retry with the safe route
        plan = route_by_keyword(utterance)
        plan["router"] = "keyword (recovered)"
        result = TOOL_FNS[plan["tool"]](**plan["args"])
    return {
        "utterance": utterance,
        "router": plan["router"],
        "tool": plan["tool"],
        "arguments": plan["args"],
        "say": speak(plan["tool"], result),
        "result": result,
    }
