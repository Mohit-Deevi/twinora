#!/usr/bin/env python3
"""Stage 2 — source leads that match the ICP.

Two providers behind one interface, because Apollo's Free plan blocks the endpoints this needs:

  instantly  (default)  Instantly Supersearch — POST /api/v2/supersearch-enrichment/*
                        count -> preview -> enrich. Works with a normal Instantly key.
  apollo                Apollo People Search — POST /api/v1/mixed_people/search
                        **Free plan returns 403 on this endpoint** (and on people/match,
                        people/bulk_match, organizations/show, mixed_companies/search).
                        `--provider apollo` runs a preflight and tells you plainly if it is blocked.

Cost/credit safety:
  * `count` and `preview` are cheap and run first; the real `enrich` call consumes credits and is
    therefore gated behind --live AND the `lead-pull` approval ticket.
  * --limit is hard-capped by --max-limit (default 200) so a typo cannot drain an account.

Usage:
  python lead_sources.py --icp <run>/icp.json --out <run>/leads.json --limit 100          # dry-run preview
  python lead_sources.py --icp <run>/icp.json --out <run>/leads.json --limit 100 --live   # real pull
  python lead_sources.py --provider apollo --preflight                                    # check plan access
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (ApiError, die, http_json, log, read_json, require_approval,  # noqa: E402
                    require_env, write_json)

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"
APOLLO_BASE = "https://api.apollo.io/api/v1"

APOLLO_HOW = "Get it from Apollo → Settings → Integrations → API. Note: search needs a PAID plan."
INSTANTLY_HOW = "Get it from Instantly → Settings → Integrations → API keys (v2)."

# Apollo endpoints that a Free plan refuses, so the error message can be specific rather than a bare 403.
APOLLO_PAID_ONLY = ["api/v1/mixed_people/api_search", "api/v1/mixed_companies/search",
                    "api/v1/people/match", "api/v1/people/bulk_match",
                    "api/v1/people/show", "api/v1/organizations/show"]

SENIORITY_MAP = {"founder": "owner", "c_suite": "c_suite", "vp": "vp",
                 "director": "director", "head": "head", "manager": "manager"}

# ---- Instantly Supersearch vocabularies (verified against the LIVE index, 2026-08-24) ------------
# These are closed enums: anything outside them is rejected with FST_ERR_VALIDATION, so loose ICP
# wording ("vp", "c-suite", "11-50") is coerced onto them rather than passed through.
#
# HARD-WON: the spec's enum lists more `level` values than the index actually holds. Measured counts:
#     C-Level 0 · VP-Level 0 · Director-Level 0 · Manager-Level 0 · Staff 0     <- always empty
#     Owner 1M · Chief X Officer (CxO) 1M · Vice President (VP) 1M · Director 1M · Manager 1M
#     Executive 1M · Senior 1M · Associate 1M · Mid-Senior level 1M · Partner 417K
# Sending a "*-Level" value silently returns zero leads with no error, which looks like "no matches"
# and wastes a run. Only the values below are ever sent.
IN_LEVELS = ["Entry level", "Mid-Senior level", "Director", "Associate", "Owner", "Executive",
             "Manager", "Senior", "Chief X Officer (CxO)", "Internship", "Vice President (VP)",
             "Unpaid / Internship", "Partner"]
# values that parse but match nothing — mapped away, never sent
IN_LEVELS_DEAD = ["C-Level", "VP-Level", "Director-Level", "Manager-Level", "Staff"]
IN_DEPARTMENTS = ["Engineering", "Finance & Administration", "Human Resources", "IT & IS",
                  "Marketing", "Operations", "Sales", "Support", "Other"]
IN_EMPLOYEE_BANDS = ["0 - 25", "25 - 100", "100 - 250", "250 - 1000", "1K - 10K",
                     "10K - 50K", "50K - 100K", "> 100K"]
IN_REVENUE_BANDS = ["$0 - 1M", "$1 - 10M", "$10 - 50M", "$50 - 100M", "$100 - 250M",
                    "$250 - 500M", "$500M - 1B", "> $1B"]

_LEVEL_ALIASES = {
    # every target below is a value the live index actually returns leads for
    "c_suite": "Chief X Officer (CxO)", "c-suite": "Chief X Officer (CxO)",
    "csuite": "Chief X Officer (CxO)", "c level": "Chief X Officer (CxO)",
    "c-level": "Chief X Officer (CxO)", "cxo": "Chief X Officer (CxO)",
    "chief": "Chief X Officer (CxO)", "ceo": "Chief X Officer (CxO)",
    "cto": "Chief X Officer (CxO)", "cmo": "Chief X Officer (CxO)", "coo": "Chief X Officer (CxO)",
    "founder": "Owner", "co-founder": "Owner", "owner": "Owner",
    "vp": "Vice President (VP)", "vp-level": "Vice President (VP)",
    "vice president": "Vice President (VP)",
    "director": "Director", "director-level": "Director", "head": "Director", "head of": "Director",
    "manager": "Manager", "manager-level": "Manager", "lead": "Manager",
    "partner": "Partner", "senior": "Senior", "executive": "Executive",
    "associate": "Associate", "staff": "Mid-Senior level", "entry": "Entry level",
}
_DEPT_ALIASES = {
    "marketing": "Marketing", "growth": "Marketing", "demand gen": "Marketing",
    "demand generation": "Marketing", "brand": "Marketing", "content": "Marketing",
    "sales": "Sales", "revenue": "Sales", "gtm": "Sales", "go-to-market": "Sales",
    "business development": "Sales", "bdr": "Sales", "sdr": "Sales",
    "operations": "Operations", "ops": "Operations", "revops": "Operations",
    "engineering": "Engineering", "developer": "Engineering", "technical": "Engineering",
    "it": "IT & IS", "finance": "Finance & Administration", "hr": "Human Resources",
    "people": "Human Resources", "support": "Support", "founder": "Other",
}
# upper bound of each loose band -> the Instantly band it belongs in
_SIZE_BOUNDS = [(25, "0 - 25"), (100, "25 - 100"), (250, "100 - 250"),
                (1000, "250 - 1000"), (10000, "1K - 10K"), (50000, "10K - 50K"),
                (100000, "50K - 100K")]


def _coerce_levels(values: list[str]) -> list[str]:
    """Map loose seniority wording onto the values the live index actually holds.

    Anything that resolves to a known-empty "*-Level" value is redirected, never sent.
    """
    out = []
    for v in values or []:
        s = str(v).strip()
        if s in IN_LEVELS:
            hit = s
        elif s in IN_LEVELS_DEAD:
            hit = _LEVEL_ALIASES.get(s.lower())      # e.g. "C-Level" -> "Chief X Officer (CxO)"
        else:
            hit = _LEVEL_ALIASES.get(s.lower().replace("_", " "))
            if not hit:
                low = s.lower()
                hit = next((m for alias, m in _LEVEL_ALIASES.items() if alias in low), None)
        if hit and hit in IN_LEVELS and hit not in out:
            out.append(hit)
    return out


def _coerce_departments(values: list[str]) -> list[str]:
    out = []
    for v in values or []:
        s = str(v).strip()
        hit = s if s in IN_DEPARTMENTS else _DEPT_ALIASES.get(s.lower())
        if not hit:
            low = s.lower()
            hit = next((mapped for alias, mapped in _DEPT_ALIASES.items() if alias in low), None)
        if hit and hit not in out:
            out.append(hit)
    return out


def _coerce_sizes(values: list[str]) -> list[str]:
    """'11-50', '51-200', '1001+' -> the closed Instantly bands they overlap."""
    out = []
    for v in values or []:
        s = str(v).strip()
        if s in IN_EMPLOYEE_BANDS:
            if s not in out:
                out.append(s)
            continue
        nums = [int(n) for n in re.findall(r"\d+", s.replace(",", ""))]
        if not nums:
            continue
        lo = nums[0]
        hi = nums[1] if len(nums) > 1 else (10 ** 9 if "+" in s or ">" in s else lo)
        for bound, band in _SIZE_BOUNDS:
            band_lo = 0 if band == "0 - 25" else _SIZE_BOUNDS[_SIZE_BOUNDS.index((bound, band)) - 1][0]
            if lo <= bound and hi >= band_lo and band not in out:
                out.append(band)
        if hi > 100000 and "> 100K" not in out:
            out.append("> 100K")
    return out


def _coerce_locations(values) -> list[dict]:
    """Accept 'United States', 'Bangalore, India' or an already-shaped dict.

    Instantly wants objects keyed city / state / country (or a Google place_id).
    """
    out = []
    for v in values or []:
        if isinstance(v, dict):
            keep = {k: v[k] for k in ("city", "state", "country", "place_id", "label") if v.get(k)}
            if keep:
                out.append(keep)
            continue
        parts = [p.strip() for p in str(v).split(",") if p.strip()]
        if not parts:
            continue
        if len(parts) == 1:
            out.append({"country": parts[0]})
        elif len(parts) == 2:
            out.append({"city": parts[0], "country": parts[1]})
        else:
            out.append({"city": parts[0], "state": parts[1], "country": parts[2]})
    return out


# --------------------------------------------------------------------------- filter mapping

def icp_to_instantly_filters(icp: dict, overrides: dict | None = None) -> dict:
    """Map the draft ICP onto Instantly Supersearch's `search_filters` shape.

    Every enum-backed field is coerced onto the vocabulary above; unmapped values are dropped rather
    than sent, because Supersearch rejects the whole request on one bad value.
    """
    d = {**icp.get("draft_icp", {}), **(overrides or {})}
    f: dict = {}

    titles_in = [t for t in (d.get("titles_include") or []) if t]
    titles_ex = [t for t in (d.get("exclusions", {}).get("titles_exclude") or []) if t]
    if titles_in or titles_ex:
        f["title"] = {"include": titles_in, "exclude": titles_ex}

    depts = _coerce_departments(d.get("departments"))
    if depts:
        f["department"] = depts

    levels = _coerce_levels(d.get("seniority"))
    if levels:
        f["level"] = levels

    sizes = _coerce_sizes(d.get("employee_count"))
    if sizes:
        f["employeeCount"] = sizes

    revenue = [r for r in (d.get("revenue") or []) if r in IN_REVENUE_BANDS]
    if revenue:
        f["revenue"] = revenue

    locs = _coerce_locations(d.get("locations"))
    if locs:
        f["locations"] = locs
        f["location_mode"] = d.get("location_mode", "company")

    inds_in = [i for i in (d.get("industries_include") or []) if i]
    inds_ex = [i for i in (d.get("exclusions", {}).get("industries_exclude") or []) if i]
    if inds_in or inds_ex:
        f["industry"] = {"include": inds_in, "exclude": inds_ex}

    if d.get("keywords_include"):
        f["keyword_filter"] = {"include": " ".join(d["keywords_include"]), "exclude": "", "include_mode": "ANY"}
    if d.get("technologies"):
        f["technologies"] = d["technologies"]

    f["skip_owned_leads"] = True          # never re-pull someone already in the workspace
    f["show_one_lead_per_company"] = d.get("one_per_company", True)
    return f


def icp_to_apollo_query(icp: dict, limit: int, page: int = 1, overrides: dict | None = None) -> dict:
    d = {**icp.get("draft_icp", {}), **(overrides or {})}
    q: dict = {"page": page, "per_page": min(limit, 100)}
    if d.get("titles_include"):
        q["person_titles"] = d["titles_include"]
    if d.get("seniority"):
        q["person_seniorities"] = [SENIORITY_MAP.get(s, s) for s in d["seniority"]]
    if d.get("locations"):
        q["person_locations"] = d["locations"]
    if d.get("employee_count"):
        q["organization_num_employees_ranges"] = [b.replace("-", ",").replace("+", ",1000000")
                                                  for b in d["employee_count"]]
    if d.get("keywords_include"):
        q["q_organization_keyword_tags"] = d["keywords_include"]
    return q


# --------------------------------------------------------------------------- normalisation

def normalise(raw: dict, provider: str) -> dict:
    """Flatten a provider record into the one shape the rest of the pipeline uses."""
    if provider == "apollo":
        org = raw.get("organization") or {}
        return {
            "source": "apollo",
            "first_name": raw.get("first_name") or "",
            "last_name": raw.get("last_name") or "",
            "email": raw.get("email") or "",          # often masked until enriched on a paid plan
            "job_title": raw.get("title") or "",
            "company_name": org.get("name") or "",
            "website": org.get("website_url") or org.get("primary_domain") or "",
            "linkedin_url": raw.get("linkedin_url") or "",
            "location": ", ".join(x for x in (raw.get("city"), raw.get("state"), raw.get("country")) if x),
            "industry": org.get("industry") or "",
            "employee_count": org.get("estimated_num_employees") or "",
            "company_description": (org.get("short_description") or "")[:400],
            "raw_id": raw.get("id") or "",
        }
    # Supersearch returns camelCase (firstName/jobTitle/companyName/linkedIn); the /leads endpoints
    # return snake_case. Accept both so preview records and enriched records normalise identically.
    org = raw.get("company") or raw.get("organization") or {}

    def pick(*keys, default=""):
        for k in keys:
            v = raw.get(k)
            if v not in (None, ""):
                return v
        return default

    linkedin = str(pick("linkedin_url", "linkedIn", "linkedin"))
    if linkedin and not linkedin.startswith("http"):
        linkedin = "https://" + linkedin.lstrip("/")
    return {
        "source": "instantly",
        "first_name": pick("first_name", "firstName"),
        "last_name": pick("last_name", "lastName"),
        "full_name": pick("full_name", "fullName"),
        "email": pick("email"),
        "job_title": pick("title", "job_title", "jobTitle"),
        "company_name": pick("company_name", "companyName") or org.get("name") or "",
        "website": pick("website", "company_domain", "companyDomain", "companyUrl") or org.get("domain") or "",
        "linkedin_url": linkedin,
        "location": pick("location") or ", ".join(x for x in (raw.get("city"), raw.get("country")) if x),
        "industry": pick("industry", "companyIndustry") or org.get("industry") or "",
        "employee_count": pick("employee_count", "companyEmployeeCount", "employeeCount") or org.get("employee_count") or "",
        "company_description": str(pick("company_description", "companyDescription") or org.get("description") or "")[:400],
        "raw_id": pick("id", "lead_id", "leadId"),
    }


def dedupe(leads: list[dict]) -> list[dict]:
    seen, out = set(), []
    for l in leads:
        key = (l.get("email") or "").lower().strip() or \
              f"{l.get('first_name','').lower()}|{l.get('last_name','').lower()}|{l.get('company_name','').lower()}"
        if key in ("", "||") or key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out


# --------------------------------------------------------------------------- providers

def instantly_headers() -> dict:
    return {"Authorization": f"Bearer {require_env('INSTANTLY_API_KEY', INSTANTLY_HOW)}"}


def instantly_pull(icp: dict, limit: int, live: bool, list_name: str, overrides=None) -> list[dict]:
    filters = icp_to_instantly_filters(icp, overrides)
    h = instantly_headers()
    log("  filters: " + json.dumps(filters, ensure_ascii=False)[:400])

    # 1. count — free, so always real. The API caps the reported figure at 1,000,000.
    total = None
    try:
        cnt = http_json(f"{INSTANTLY_BASE}/supersearch-enrichment/count-leads-from-supersearch",
                        "POST", h, {"search_filters": filters, "skip_owned_leads": True}, dry_run=False)
        total = cnt.get("number_of_leads", cnt.get("count", cnt.get("total")))
        log(f"  matching pool: {total:,}" if isinstance(total, int) else f"  matching pool: {total}")
        if total == 1_000_000:
            log("    (1,000,000 is the API's display cap — the real pool is larger; tighten the filters)")
    except ApiError as e:
        log(f"  count failed (HTTP {e.status}) — continuing to preview")

    if total == 0:
        log("  ! zero matches. Loosen one filter at a time — `level` and `title` are the usual culprits.\n"
            "    Tip: run with only `department` + `locations` first, then add constraints back.")

    # 2. preview — a sample of real records, no credits burned
    prev = http_json(f"{INSTANTLY_BASE}/supersearch-enrichment/preview-leads-from-supersearch",
                     "POST", h, {"search_filters": filters, "skip_owned_leads": True,
                                 "show_one_lead_per_company": filters.get("show_one_lead_per_company", True)},
                     dry_run=False)
    sample = prev.get("leads") or prev.get("items") or prev.get("data") or []
    redacted = prev.get("number_of_redacted_results")
    log(f"  preview returned {len(sample)} sample records"
        + (f" ({redacted:,} results redacted until enrichment)" if isinstance(redacted, int) and redacted else ""))

    if not live:
        log("  [dry-run] stopping before the credit-consuming enrich call. Re-run with --live to pull for real.")
        return [normalise(r, "instantly") for r in sample][:limit]

    # 3. enrich — this consumes credits, so it needs an explicit ticket
    require_approval("lead-pull", f"pulling {limit} leads from Instantly Supersearch (consumes credits)")
    res = http_json(f"{INSTANTLY_BASE}/supersearch-enrichment/enrich-leads-from-supersearch",
                    "POST", h, {"search_filters": filters, "limit": limit, "list_name": list_name,
                                "search_name": list_name, "work_email_enrichment": True,
                                "skip_rows_without_email": True},
                    timeout=180, dry_run=False)
    items = res.get("items") or res.get("leads") or res.get("data") or []
    if not items and res.get("resource_id"):
        log(f"  enrichment queued (resource_id={res['resource_id']}). "
            f"Leads land in the Instantly list '{list_name}'; re-run stage 3 once it finishes.")
    return [normalise(r, "instantly") for r in items]


def apollo_preflight() -> dict:
    """Detect whether this Apollo key can actually search. Free plans cannot."""
    key = require_env("APOLLO_API_KEY", APOLLO_HOW)
    h = {"x-api-key": key, "Content-Type": "application/json"}
    try:
        http_json(f"{APOLLO_BASE}/mixed_people/search", "POST", h, {"page": 1, "per_page": 1}, dry_run=False)
        return {"ok": True, "search": True}
    except ApiError as e:
        blocked = e.status in (401, 403) or "not included in your" in e.body.lower() or "upgrade" in e.body.lower()
        return {"ok": False, "search": False, "status": e.status, "blocked_by_plan": blocked,
                "paid_only_endpoints": APOLLO_PAID_ONLY, "detail": e.body[:300]}


def apollo_pull(icp: dict, limit: int, live: bool, overrides=None) -> list[dict]:
    key = require_env("APOLLO_API_KEY", APOLLO_HOW)
    h = {"x-api-key": key, "Content-Type": "application/json"}
    out: list[dict] = []
    page = 1
    while len(out) < limit and page <= 10:
        q = icp_to_apollo_query(icp, limit - len(out), page, overrides)
        try:
            res = http_json(f"{APOLLO_BASE}/mixed_people/search", "POST", h, q, dry_run=not live)
        except ApiError as e:
            if e.status in (401, 403):
                die("Apollo refused the search endpoint (HTTP %s).\n"
                    "  Your key is on a plan without API search access. Blocked endpoints:\n    %s\n"
                    "  Either upgrade Apollo, or use the default provider:  --provider instantly"
                    % (e.status, "\n    ".join(APOLLO_PAID_ONLY)))
            raise
        if not live:
            return []
        people = res.get("people") or res.get("contacts") or []
        if not people:
            break
        out.extend(normalise(p, "apollo") for p in people)
        page += 1
    return out[:limit]


# --------------------------------------------------------------------------- cli

def main() -> None:
    ap = argparse.ArgumentParser(description="Source ICP-matched leads (Instantly Supersearch or Apollo)")
    ap.add_argument("--icp", help="path to icp.json from stage 1")
    ap.add_argument("--out")
    ap.add_argument("--provider", default="instantly", choices=["instantly", "apollo"])
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--max-limit", type=int, default=200, help="hard ceiling; a bigger --limit is refused")
    ap.add_argument("--list-name", default="test mohit")
    ap.add_argument("--overrides", help="JSON file of ICP fields to override (locations, industries…)")
    ap.add_argument("--live", action="store_true", help="actually call the API (default is dry-run)")
    ap.add_argument("--preflight", action="store_true", help="only check provider access, pull nothing")
    a = ap.parse_args()

    if a.preflight:
        if a.provider == "apollo":
            r = apollo_preflight()
            log(json.dumps(r, indent=2))
            if r.get("blocked_by_plan"):
                log("\n  -> Apollo search is NOT available on this key's plan. Use --provider instantly.")
        else:
            try:
                http_json(f"{INSTANTLY_BASE}/campaigns?limit=1", "GET", instantly_headers(), dry_run=False)
                log(json.dumps({"ok": True, "provider": "instantly"}, indent=2))
            except ApiError as e:
                log(json.dumps({"ok": False, "status": e.status, "detail": e.body[:200]}, indent=2))
        return

    if not a.icp:
        die("--icp is required (run icp_builder.py first)")
    if a.limit > a.max_limit:
        die(f"--limit {a.limit} exceeds --max-limit {a.max_limit}. Raise the ceiling deliberately if you mean it.")

    icp = read_json(pathlib.Path(a.icp))
    if not icp:
        die(f"could not read {a.icp}")
    overrides = read_json(pathlib.Path(a.overrides)) if a.overrides else None

    d = icp.get("draft_icp", {})
    if not d.get("locations") and not (overrides or {}).get("locations"):
        log("  ! ICP has no locations. Supersearch will return a global pool — confirm this is intended.")

    log(f"provider={a.provider} limit={a.limit} live={a.live}")
    leads = (instantly_pull(icp, a.limit, a.live, a.list_name, overrides) if a.provider == "instantly"
             else apollo_pull(icp, a.limit, a.live, overrides))
    leads = dedupe(leads)

    with_email = sum(1 for l in leads if l.get("email"))
    log(f"\n  {len(leads)} unique leads ({with_email} with an email address)")
    for l in leads[:5]:
        log(f"    - {l['first_name']} {l['last_name']} | {l['job_title']} | {l['company_name']}")

    if a.out:
        write_json(pathlib.Path(a.out), {"provider": a.provider, "live": a.live, "count": len(leads),
                                         "with_email": with_email, "leads": leads})
        log(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
