"""The radar's capabilities, as plain functions.

Every number these return comes from a live grants.gov response in this call.
Nothing is estimated, remembered, or interpolated: when the source is down the
tools say so instead of guessing, which is what makes the answers safe to read
aloud in someone's kitchen.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .sources import SourceError, fetch_grant, search_grants, web_url

MONEY = re.compile(r"[^0-9.]")


def _money(v) -> int | None:
    if v in (None, "", 0, "0"):
        return None
    try:
        return int(float(MONEY.sub("", str(v))))
    except ValueError:
        return None


def _days_left(close: str | None) -> int | None:
    """Days until a close date. grants.gov ships two different formats:
    search returns "10/09/2026", detail returns "Oct 09, 2026 12:00:00 AM EDT"."""
    if not close:
        return None
    text = str(close).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return (datetime.strptime(text, fmt).date() - date.today()).days
        except ValueError:
            pass
    m = re.match(r"([A-Za-z]{3,9} \d{1,2}, \d{4})", text)  # strip trailing time/zone
    if m:
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return (datetime.strptime(m.group(1), fmt).date() - date.today()).days
            except ValueError:
                pass
    return None


def _row(hit: dict) -> dict:
    return {
        "id": hit.get("id"),
        "title": hit.get("title"),
        "agency": hit.get("agency") or hit.get("agencyCode"),
        "number": hit.get("number"),
        "close_date": hit.get("closeDate") or None,
        "days_left": _days_left(hit.get("closeDate")),
        "url": web_url(hit.get("id", "")),
    }


def _guard(fn):
    try:
        return fn()
    except SourceError as e:
        return {
            "error": "source_unavailable",
            "detail": str(e),
            "say": "I could not reach the grants database just now, so I have no numbers to give you.",
        }


def find_opportunities(interest: str, max_results: int = 8, open_only: bool = True) -> dict:
    """Find currently open funding opportunities matching a plain-language interest.

    Use this for questions like "any grants for a small AI company?" or
    "what's open for rural health right now?". `interest` is free text and is
    matched against opportunity titles and descriptions.
    """
    def run():
        statuses = "posted" if open_only else "posted|forecasted"
        d = search_grants(interest, rows=max_results, statuses=statuses)
        rows = [_row(h) for h in d.get("oppHits", [])]
        return {
            "interest": interest,
            "total_matches": d.get("hitCount", 0),
            "showing": len(rows),
            "opportunities": rows,
            "note": "Counts and dates come from a live grants.gov search.",
        }
    return _guard(run)


def opportunity_detail(opportunity_id: str) -> dict:
    """Get the full picture for one opportunity: award size, who may apply, dates.

    Call this after `find_opportunities` when the person asks about a specific
    one, e.g. "tell me more about the second one".
    """
    def run():
        det = fetch_grant(str(opportunity_id))
        syn = det.get("synopsis", {}) or {}
        applicants = [a.get("description") for a in (syn.get("applicantTypes") or []) if a.get("description")]
        desc = re.sub(r"<[^>]+>", " ", syn.get("synopsisDesc") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        return {
            "id": det.get("id"),
            "title": det.get("opportunityTitle"),
            "number": det.get("opportunityNumber"),
            "agency": syn.get("agencyName") or det.get("owningAgencyCode"),
            "award_ceiling": _money(syn.get("awardCeiling")),
            "award_floor": _money(syn.get("awardFloor")),
            "expected_awards": syn.get("numberOfAwards"),
            "close_date": syn.get("responseDate"),
            "days_left": _days_left(syn.get("responseDate")),
            "who_can_apply": applicants,
            "cost_sharing_required": syn.get("costSharing"),
            "summary": desc[:900],
            "url": web_url(str(det.get("id", ""))),
        }
    return _guard(run)


APPLICANT_ALIASES = {
    "individual": ["individual"],
    "small business": ["small business", "for profit", "for-profit"],
    "nonprofit": ["nonprofit", "non-profit"],
    "university": ["education", "university", "college"],
    "city government": ["city", "municipal", "local government"],
    "state government": ["state government"],
    "tribal": ["tribal"],
}


def check_eligibility_fit(opportunity_id: str, applicant_type: str) -> dict:
    """Check whether a given kind of applicant is actually allowed to apply.

    `applicant_type` is plain language: "individual", "small business",
    "nonprofit", "city government", "university". This reads the official
    applicant list published with the notice — it does not guess.
    """
    def run():
        det = opportunity_detail(opportunity_id)
        if "error" in det:
            return det
        allowed = det.get("who_can_apply") or []
        blob = " ".join(allowed).lower()
        want = applicant_type.lower().strip()
        keys = next((v for k, v in APPLICANT_ALIASES.items() if k in want), [want])
        hit = any(k in blob for k in keys) or "unrestricted" in blob
        # Some notices publish only "Others (see text field ...)" — that is a
        # pointer to prose, not a rejection. Saying "not eligible" there would
        # be the agent inventing a fact, so this reports it as unknown instead.
        deferred = (not hit) and ("others" in blob or not allowed)
        status = "eligible" if hit else ("see_notice" if deferred else "not_listed")
        verdict = {
            "eligible": "Listed as eligible.",
            "see_notice": ("The notice does not publish a plain applicant list; it points to its "
                           "own eligibility section. Worth reading, not ruled out."),
            "not_listed": "Not listed. Read the notice before spending time on it.",
        }[status]
        return {
            "id": det["id"],
            "title": det["title"],
            "applicant_type": applicant_type,
            "eligible": hit if not deferred else None,
            "status": status,
            "official_applicant_list": allowed,
            "verdict": verdict,
            "url": det["url"],
        }
    return _guard(run)


def deadline_watch(interest: str, within_days: int = 30, max_results: int = 10) -> dict:
    """List matching opportunities whose deadline falls inside the next N days.

    This answers "what do I have to act on this week". Opportunities with no
    published close date are counted separately rather than silently dropped.
    """
    def run():
        d = search_grants(interest, rows=60, statuses="posted")
        soon, undated = [], 0
        for h in d.get("oppHits", []):
            r = _row(h)
            if r["days_left"] is None:
                undated += 1
            elif 0 <= r["days_left"] <= within_days:
                soon.append(r)
        soon.sort(key=lambda r: r["days_left"])
        return {
            "interest": interest,
            "within_days": within_days,
            "closing_soon": soon[:max_results],
            "count": len(soon),
            "no_published_deadline": undated,
            "scanned": len(d.get("oppHits", [])),
        }
    return _guard(run)


def weekly_briefing(interests: str, applicant_type: str = "individual") -> dict:
    """Build this week's radar sweep across several interests at once.

    `interests` is a comma-separated list such as "ai, small business, clean
    energy". This is what the scheduled background run calls; it returns the
    raw material for a spoken summary, already filtered to things still open.
    """
    def run():
        out, seen = [], set()
        for raw in [i.strip() for i in interests.split(",") if i.strip()][:5]:
            d = search_grants(raw, rows=12, statuses="posted")
            for h in d.get("oppHits", []):
                r = _row(h)
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                r["matched_interest"] = raw
                if r["days_left"] is None or r["days_left"] >= 0:
                    out.append(r)
        dated = [r for r in out if r["days_left"] is not None]
        dated.sort(key=lambda r: r["days_left"])
        return {
            "interests": interests,
            "applicant_type": applicant_type,
            "total_open_found": len(out),
            "closing_first": dated[:8],
            "generated_for": str(date.today()),
        }
    return _guard(run)


def rank_for_me(profile: str, interest: str = "", max_candidates: int = 8, top: int = 5) -> dict:
    """Rank live opportunities by how well they fit YOU, with a reason for each.

    `profile` is one sentence about the applicant: "solo developer building AI
    tools for small clinics". `interest` narrows the search first; leave it
    empty to reuse the profile as the search term. Fit scores are judged by a
    model on Amazon Bedrock; every deadline and dollar figure attached to a
    result still comes from grants.gov.
    """
    def run():
        from .rank import rank_for_me as _rank
        query = interest.strip() or profile
        found = find_opportunities(query, max_results=max_candidates)
        if "error" in found:
            return found
        detailed = []
        for row in found["opportunities"]:
            det = opportunity_detail(row["id"])
            detailed.append(row if "error" in det else {**row, **det})
        out = _rank(profile, detailed, top=top)
        out["searched"] = query
        out["candidates_considered"] = len(detailed)
        out["total_matches"] = found["total_matches"]
        return out
    return _guard(run)


ALL_TOOLS = [find_opportunities, opportunity_detail, check_eligibility_fit,
             deadline_watch, weekly_briefing, rank_for_me]
