"""Fit ranking — deciding what is actually worth your Saturday.

Search tells you what *mentions* your words. It does not tell you what fits.
This module asks a language model to read each opportunity against one sentence
about the person and score the fit, with a reason.

Three engines, tried in order, all optional:

  local   — a model on your own machine through Ollama (default). No key, no
            account, no card, and the text of what you are looking for never
            leaves the house. That last part matters when the query is
            "grants for a family caring for a disabled child".
  bedrock — Amazon Bedrock, if AWS credentials happen to be configured.
  overlap — plain keyword overlap. Always available, clearly labelled, so the
            radar still answers on a plane.

Whatever engine runs, the model is only ever allowed to judge *text the tools
already fetched*. It is never asked for a number, a deadline, or a dollar
figure: those are attached from grants.gov afterwards. A hallucinating model
cannot invent money or dates here — the worst it can do is misjudge relevance.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("RADAR_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("RADAR_LOCAL_MODEL", "llama3.1:8b")
BEDROCK_MODEL = os.environ.get("RADAR_BEDROCK_MODEL", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
REGION = os.environ.get("RADAR_REGION") or os.environ.get("AWS_REGION", "us-east-1")
ENGINE_PREF = os.environ.get("RADAR_RANK_ENGINE", "auto")  # auto | local | bedrock | overlap

RUBRIC = """You rate how well a funding opportunity fits one applicant.

Applicant: {profile}

Opportunity:
title: {title}
agency: {agency}
who may apply: {applicants}
summary: {summary}

Reply with JSON only, no other text:
{{"score": <0-100 integer>, "reason": "<=20 words", "blocker": "<=12 words or empty"}}

Score 80+ only if the applicant is plausibly allowed to apply AND the topic matches.
Judge fit only. Never mention or invent amounts, deadlines, or counts."""

_WORD = re.compile(r"[a-z0-9]+")


def _overlap(profile: str, text: str) -> int:
    a = {w for w in _WORD.findall(profile.lower()) if len(w) > 3}
    b = {w for w in _WORD.findall(text.lower()) if len(w) > 3}
    return int(100 * len(a & b) / len(a)) if a else 0


def _parse_verdict(text: str) -> dict:
    """Pull the JSON verdict out of a model reply, tolerating chatter around it."""
    m = re.search(r"\{.*?\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    try:
        score = int(float(data.get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    return {
        "score": max(0, min(100, score)),
        "reason": str(data.get("reason", ""))[:120],
        "blocker": str(data.get("blocker", ""))[:80],
    }


# --- engines ---------------------------------------------------------------

def _local_available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 - not running is the normal case
        return False


def _local_score(prompt: str) -> dict:
    body = json.dumps({
        "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
        "format": "json", "options": {"temperature": 0.0, "num_predict": 160},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return _parse_verdict(json.loads(r.read().decode()).get("response", ""))


def _bedrock_client():
    import boto3  # lazy: only this path needs boto3
    return boto3.client("bedrock-runtime", region_name=REGION)


def _bedrock_score(client, prompt: str) -> dict:
    resp = client.converse(
        modelId=BEDROCK_MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.0},
    )
    return _parse_verdict(resp["output"]["message"]["content"][0]["text"])


def _pick_engine() -> tuple[str, object | None]:
    """Return (engine name, client). Never raises: falls back to overlap."""
    if ENGINE_PREF == "overlap":
        return "overlap", None
    if ENGINE_PREF in ("auto", "local") and _local_available():
        return "local", None
    if ENGINE_PREF in ("auto", "bedrock"):
        try:
            return "bedrock", _bedrock_client()
        except Exception:  # noqa: BLE001 - no boto3 / no creds
            pass
    return "overlap", None


def rank_for_me(profile: str, opportunities: list[dict], top: int = 5) -> dict:
    """Score fetched opportunities against a one-line description of the applicant.

    `profile` is plain language: "solo software developer building AI tools for
    small clinics". Returns the best matches with a reason for each.
    """
    engine, client = _pick_engine()
    degraded = None

    scored = []
    for opp in opportunities[: max(1, min(len(opportunities), 12))]:
        prompt = RUBRIC.format(
            profile=profile[:400],
            title=str(opp.get("title", ""))[:200],
            agency=str(opp.get("agency", ""))[:80],
            applicants="; ".join(opp.get("who_can_apply") or [])[:200] or "not published",
            summary=str(opp.get("summary") or "")[:1200],
        )
        blob = " ".join(str(opp.get(k, "")) for k in ("title", "agency", "summary"))
        try:
            if engine == "local":
                judged = _local_score(prompt)
            elif engine == "bedrock":
                judged = _bedrock_score(client, prompt)
            else:
                judged = {"score": _overlap(profile, blob), "reason": "keyword overlap", "blocker": ""}
        except Exception as e:  # noqa: BLE001 - degrade rather than fail the turn
            degraded = f"{engine} unavailable: {type(e).__name__}"
            engine, client = "overlap", None
            judged = {"score": _overlap(profile, blob), "reason": "keyword overlap", "blocker": ""}
        scored.append({
            # facts stay verbatim from the source; only score/reason/blocker are model output
            "id": opp.get("id"), "title": opp.get("title"), "agency": opp.get("agency"),
            "close_date": opp.get("close_date"), "days_left": opp.get("days_left"),
            "award_ceiling": opp.get("award_ceiling"), "url": opp.get("url"),
            **judged,
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return {
        "profile": profile,
        "engine": engine,
        "model": {"local": OLLAMA_MODEL, "bedrock": BEDROCK_MODEL}.get(engine),
        "degraded_from": degraded,
        "ranked": scored[:top],
        "note": ("Scores and reasons are a model's judgement of fit. Deadlines and award "
                 "figures come from grants.gov, never from the model."),
    }
