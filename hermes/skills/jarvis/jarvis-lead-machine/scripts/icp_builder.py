#!/usr/bin/env python3
"""Stage 1 — scrape a company site and derive an Ideal Customer Profile.

Free: no API keys, no paid calls. Fetches the site (plus the pages that usually carry ICP evidence —
pricing, customers, case studies, about, solutions), strips it to text, and extracts:

  * positioning: title, meta description, H1/H2s, the one-line promise
  * proof points: every hard number on the page with its surrounding sentence
  * product surface: named features / modules
  * customer signals: named industries, roles, company sizes, logos, testimonial job titles
  * a **draft ICP** mapped to the exact filter vocabulary the lead sources need

The draft ICP is a proposal, not a fact. Nothing downstream sends anything, and `run_pipeline.py`
prints the ICP for the owner to confirm before any lead is pulled.

Usage:
  python icp_builder.py --url https://turgo.ai --out <run>/icp.json [--max-pages 8]
  python icp_builder.py --url https://turgo.ai --print-only
"""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import log, die, write_json, slugify  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
# pages that usually carry ICP evidence, in priority order
CANDIDATE_PATHS = ["", "/pricing", "/customers", "/case-studies", "/about", "/product",
                   "/solutions", "/who-we-serve", "/industries", "/use-cases"]

SENIORITY_WORDS = ["founder", "co-founder", "ceo", "cto", "cmo", "coo", "vp", "vice president",
                   "head of", "director", "chief", "manager", "lead"]
DEPARTMENT_WORDS = {
    "marketing": ["marketing", "growth", "demand gen", "demand generation", "brand", "content"],
    "sales": ["sales", "revenue", "gtm", "go-to-market", "business development", "bdr", "sdr", "account executive"],
    "operations": ["operations", "ops", "revops", "marketing ops"],
    "engineering": ["engineering", "developer", "technical", "cto"],
    "founder": ["founder", "co-founder", "ceo", "owner"],
}
SIZE_HINTS = {
    "1-10": ["solo", "one person", "freelancer", "startup", "small team"],
    "11-50": ["small business", "smb", "early stage", "seed"],
    "51-200": ["mid market", "scale-up", "series a", "series b", "growing team"],
    "201-1000": ["enterprise", "large team", "series c"],
    "1001+": ["global enterprise", "fortune 500", "multinational"],
}


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("utf-8", "replace")


def visible_text(doc: str) -> str:
    t = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", doc, flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def tag_texts(doc: str, tag: str, limit: int = 14) -> list[str]:
    out, seen = [], set()
    for m in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", doc, re.S | re.I):
        s = html.unescape(re.sub(r"<[^>]+>", " ", m))
        s = re.sub(r"\s+", " ", s).strip()
        if s and len(s) < 200 and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= limit:
            break
    return out


def meta(doc: str, name: str) -> str:
    for pat in (rf'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)',
                rf'<meta[^>]+property=["\']og:{name}["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, doc, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def proof_points(text: str, limit: int = 20) -> list[str]:
    """Sentences containing a hard number — the raw material for outreach credibility."""
    out, seen = [], set()
    for m in re.finditer(r"[^.!?]{0,110}?\b\d[\d,.]*\s*(?:%|x\b|k\b|m\b|\+|million|billion|hours?|days?|weeks?|"
                         r"leads?|meetings?|customers?|sign-?ups?|users?)[^.!?]{0,90}", text, re.I):
        s = m.group(0).strip()
        # kill the odometer-style animation counters ("0 + 1 + 2 + 3 ...") that marketing sites render
        if re.search(r"(\b\d+\s*[+%]\s*){4,}", s):
            continue
        key = re.sub(r"\s+", " ", s.lower())[:60]
        if len(s) < 15 or key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def guess_departments(text: str) -> list[str]:
    low = text.lower()
    hits = [(dept, sum(low.count(w) for w in words)) for dept, words in DEPARTMENT_WORDS.items()]
    return [d for d, n in sorted(hits, key=lambda x: -x[1]) if n > 0][:4]


def guess_titles(text: str, limit: int = 12) -> list[str]:
    low, found = text.lower(), []
    for w in SENIORITY_WORDS:
        for m in re.finditer(rf"\b{re.escape(w)}\b[ ,]{{0,2}}(of |)([a-z]{{3,18}}(?: [a-z]{{3,18}})?)", low):
            phrase = f"{w} {m.group(2)}".strip()
            if 4 < len(phrase) < 40 and phrase not in found:
                found.append(phrase)
            if len(found) >= limit:
                return found
    return found


def guess_sizes(text: str) -> list[str]:
    low = text.lower()
    return [band for band, words in SIZE_HINTS.items() if any(w in low for w in words)] or ["11-50", "51-200"]


def build(url: str, max_pages: int) -> dict:
    base = url.rstrip("/")
    host = urllib.parse.urlparse(base).netloc
    pages, errors = {}, {}
    for path in CANDIDATE_PATHS[:max_pages]:
        full = base + path
        try:
            pages[full] = fetch(full)
            log(f"  fetched {full} ({len(pages[full])//1024}KB)")
        except Exception as e:
            errors[full] = f"{type(e).__name__}"
    if not pages:
        die(f"could not fetch any page from {base}: {errors}")

    home = pages.get(base) or next(iter(pages.values()))
    all_text = " ".join(visible_text(d) for d in pages.values())
    home_text = visible_text(home)

    icp = {
        "source_url": base,
        "company": {
            "name": (tag_texts(home, "title", 1) or [host])[0].split("—")[0].split("|")[0].strip(),
            "title_tag": (tag_texts(home, "title", 1) or [""])[0],
            "promise": meta(home, "description"),
            "h1": tag_texts(home, "h1", 8),
            "h2": tag_texts(home, "h2", 12),
            "h3": tag_texts(home, "h3", 14),
        },
        "proof_points": proof_points(all_text),
        "pages_scraped": list(pages),
        "pages_failed": errors,
        "draft_icp": {
            "_note": "DRAFT — derived from page text by keyword heuristics. Confirm with the owner before sourcing leads.",
            "departments": guess_departments(all_text),
            "titles_include": guess_titles(home_text),
            "seniority": ["founder", "c_suite", "vp", "director", "head"],
            "employee_count": guess_sizes(all_text),
            "locations": [],
            "industries_include": [],
            "keywords_include": [],
            "exclusions": {"titles_exclude": ["intern", "student", "assistant"], "industries_exclude": []},
        },
        "outreach_angle": {
            "pain": "",
            "promise": meta(home, "description"),
            "proof": (proof_points(all_text) or [""])[:3],
            "cta": "Worth a 15-minute look?",
        },
    }
    return icp


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape a site and draft an ICP (free, no API keys)")
    ap.add_argument("--url", required=True)
    ap.add_argument("--out")
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()

    log(f"scraping {a.url}")
    icp = build(a.url, a.max_pages)

    log("\n--- draft ICP ---")
    log(f"  company : {icp['company']['name']}")
    log(f"  promise : {icp['company']['promise'][:150]}")
    log(f"  depts   : {icp['draft_icp']['departments']}")
    log(f"  titles  : {icp['draft_icp']['titles_include'][:6]}")
    log(f"  size    : {icp['draft_icp']['employee_count']}")
    log(f"  proof   : {len(icp['proof_points'])} numeric claims found")
    for p in icp["proof_points"][:5]:
        log(f"            - {p[:110]}")
    log("\n  locations / industries are intentionally EMPTY — ask the owner, never guess a market.")

    if not a.print_only:
        out = pathlib.Path(a.out or f"icp-{slugify(icp['company']['name'])}.json")
        write_json(out, icp)
        log(f"\nwrote {out}")


if __name__ == "__main__":
    main()
