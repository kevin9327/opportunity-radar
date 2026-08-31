"""Live funding-opportunity sources. No API keys: every endpoint here is public.

grants.gov search2 is the primary source (US federal grants, ~everything an
individual or small business can apply for). Responses are cached on disk for
6 hours so repeated agent calls during one conversation stay fast and polite.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

GRANTS_SEARCH = "https://api.grants.gov/v1/api/search2"
GRANTS_FETCH = "https://api.grants.gov/v1/api/fetchOpportunity"
GRANTS_WEB = "https://grants.gov/search-results-detail/{id}"

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CACHE_TTL = 6 * 3600
TIMEOUT = 30


class SourceError(RuntimeError):
    """Raised when a live source cannot be reached — never faked into data."""


def _cache_path(payload: dict, endpoint: str) -> Path:
    key = hashlib.sha256((endpoint + json.dumps(payload, sort_keys=True)).encode()).hexdigest()[:16]
    return CACHE_DIR / f"{key}.json"


def _post(endpoint: str, payload: dict, fresh: bool = False) -> dict:
    cp = _cache_path(payload, endpoint)
    if not fresh and cp.exists() and time.time() - cp.stat().st_mtime < CACHE_TTL:
        return json.loads(cp.read_text(encoding="utf-8"))
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "opportunity-radar/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:  # noqa: PERF203 - explicit surface
        raise SourceError(f"grants.gov HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001 - network/DNS/timeout all surface the same way
        raise SourceError(f"grants.gov unreachable: {type(e).__name__}") from e
    if body.get("errorcode") not in (0, None):
        raise SourceError(f"grants.gov error {body.get('errorcode')}: {body.get('msg')}")
    CACHE_DIR.mkdir(exist_ok=True)
    cp.write_text(json.dumps(body), encoding="utf-8")
    return body


def search_grants(keyword: str = "", rows: int = 25, statuses: str = "posted",
                  eligibilities: str = "", agencies: str = "",
                  funding_categories: str = "", fresh: bool = False) -> dict:
    """Raw search against grants.gov. Returns the `data` block."""
    payload = {
        "keyword": keyword, "rows": max(1, min(rows, 100)), "oppStatuses": statuses,
        "eligibilities": eligibilities, "agencies": agencies,
        "fundingCategories": funding_categories,
    }
    return _post(GRANTS_SEARCH, {k: v for k, v in payload.items() if v != ""}, fresh)["data"]


def fetch_grant(opportunity_id: str, fresh: bool = False) -> dict:
    """Full detail for one opportunity (award ceiling, eligibility prose, dates)."""
    body = _post(GRANTS_FETCH, {"opportunityId": int(opportunity_id)}, fresh)
    return body.get("data", {})


def web_url(opportunity_id: str) -> str:
    return GRANTS_WEB.format(id=opportunity_id)
