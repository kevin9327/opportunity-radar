"""Fit ranking — the AWS layer (AWS Builder mini challenge).

Search gives you what *mentions* your words. It does not tell you what is worth
your Saturday. This module asks Amazon Bedrock to read each opportunity against
one sentence about the person and score how well it fits, with a reason.

The model is only ever allowed to judge text the tools already fetched; it is
never asked for a number, a deadline, or a dollar figure. Those come from
grants.gov and are attached to the result afterwards, so a hallucinating model
cannot invent money or dates — the worst it can do is misjudge relevance.

Without AWS credentials this degrades to a transparent keyword overlap score
(`engine: "local-overlap"`), so the radar still answers on a laptop.
"""
from __future__ import annotations

import json
import os
import re

BEDROCK_MODEL = os.environ.get("RADAR_BEDROCK_MODEL", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
REGION = os.environ.get("RADAR_REGION") or os.environ.get("AWS_REGION", "us-east-1")

RUBRIC = """You rate how well a funding opportunity fits one applicant.

Applicant: {profile}

Opportunity:
title: {title}
agency: {agency}
who may apply: {applicants}
summary: {summary}

Reply with JSON only: {{"score": 0-100, "reason": "<=20 words", "blocker": "<=12 words or empty"}}
score 80+ only if the applicant is plausibly allowed to apply AND the topic matches.
Judge fit only. Do not mention or invent amounts, deadlines, or counts."""

_WORD = re.compile(r"[a-z0-9]+")


def _overlap(profile: str, text: str) -> int:
    a = {w for w in _WORD.findall(profile.lower()) if len(w) > 3}
    b = {w for w in _WORD.findall(text.lower()) if len(w) > 3}
    if not a:
        return 0
    return int(100 * len(a & b) / len(a))


def _client():
    import boto3  # lazy: only this path needs boto3
    return boto3.client("bedrock-runtime", region_name=REGION)


def _bedrock_score(client, profile: str, opp: dict) -> dict:
    prompt = RUBRIC.format(
        profile=profile[:400],
        title=opp.get("title", "")[:200],
        agency=opp.get("agency", "")[:80],
        applicants="; ".join(opp.get("who_can_apply") or [])[:200] or "not published",
        summary=(opp.get("summary") or "")[:1200],
    )
    resp = client.converse(
        modelId=BEDROCK_MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    return {
        "score": max(0, min(100, int(data.get("score", 0)))),
        "reason": str(data.get("reason", ""))[:120],
        "blocker": str(data.get("blocker", ""))[:80],
    }


def rank_for_me(profile: str, opportunities: list[dict], top: int = 5) -> dict:
    """Score fetched opportunities against a one-line description of the applicant.

    `profile` is plain language: "solo software developer in Korea building AI
    tools for small clinics". Returns the best matches with a reason for each.
    """
    engine, client = "bedrock", None
    try:
        client = _client()
    except Exception:  # noqa: BLE001 - no boto3 / no creds / no region
        engine = "local-overlap"

    scored = []
    for opp in opportunities[: max(1, min(len(opportunities), 12))]:
        blob = " ".join(str(opp.get(k, "")) for k in ("title", "agency", "summary"))
        if client is not None:
            try:
                judged = _bedrock_score(client, profile, opp)
            except Exception as e:  # noqa: BLE001 - fall back rather than fail the turn
                engine = f"local-overlap (bedrock unavailable: {type(e).__name__})"
                client = None
                judged = {"score": _overlap(profile, blob), "reason": "keyword overlap", "blocker": ""}
        else:
            judged = {"score": _overlap(profile, blob), "reason": "keyword overlap", "blocker": ""}
        scored.append({
            # facts stay verbatim from the source; only score/reason are model output
            "id": opp.get("id"), "title": opp.get("title"), "agency": opp.get("agency"),
            "close_date": opp.get("close_date"), "days_left": opp.get("days_left"),
            "award_ceiling": opp.get("award_ceiling"), "url": opp.get("url"),
            **judged,
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return {
        "profile": profile,
        "engine": engine,
        "model": BEDROCK_MODEL if engine == "bedrock" else None,
        "ranked": scored[:top],
        "note": ("Scores are a model's judgement of fit. Deadlines and award figures "
                 "come from grants.gov, not from the model."),
    }
