import csv
import re
from decimal import Decimal
from datetime import datetime, date
from collections import defaultdict
import time
import random

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────
def normalize_title(title: str) -> str:
    """Lowercase, treat & same as 'and', strip non a-z0-9."""
    s = str(title).lower().replace('&', 'and')
    return re.sub(r'[^a-z0-9]', '', s)

def normalize(s: str) -> str:
    return normalize_title(s)

# ─────────────────────────────────────────────
# Distributor normalization
# ─────────────────────────────────────────────
def normalize_distributor(name: str) -> str:
    if not name:
        return "Unknown"
    n = name.strip()
    mapping = {
        "Walt Disney Studios Motion Pictures": "Walt Disney",
        "Walt Disney Pictures":                "Walt Disney",
        "Disney":                              "Walt Disney",
        "Buena Vista":                         "Walt Disney",
        "20th Century Studios":                "Walt Disney",
        "Searchlight Pictures":                "Walt Disney",

        "Warner Bros. Pictures":               "Warner Bros.",
        "Warner Bros":                         "Warner Bros.",
        "Warner Brothers":                     "Warner Bros.",

        "Universal Pictures":                  "Universal",
        "Universal Pictures International (UPI)": "Universal",
        "Focus Features":                      "Universal",

        "Sony Pictures Releasing":             "Sony Pictures",
        "Sony Pictures Entertainment (SPE)":   "Sony Pictures",
        "Sony":                                "Sony Pictures",
        "TriStar Pictures":                    "Sony Pictures",
        "Columbia Pictures":                   "Sony Pictures",

        "Paramount":                           "Paramount Pictures",
        "Lions Gate Films":                    "Lionsgate",
        "A24 Films":                           "A24",
        "A24 Distribution":                    "A24",
    }
    return mapping.get(n, n)

# ─────────────────────────────────────────────
# Headers
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

BOM_YEAR_URL = "https://www.boxofficemojo.com/year/2026/"

# ─────────────────────────────────────────────
# CSV loader
# ─────────────────────────────────────────────
def load_summer_list(csv_path="summer_movies.csv"):
    titles = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        rdr = csv.reader(f)
        first = next(rdr, None)
        if first and first[0].strip():
            raw   = first[0].strip()
            clean = re.sub(r"\s*\(.*\)$", "", raw).strip()
            if clean.lower() not in ("movie", "title"):
                titles.append(clean)
        for row in rdr:
            if not row or not row[0].strip():
                continue
            raw   = row[0].strip()
            clean = re.sub(r"\s*\(.*\)$", "", raw).strip()
            titles.append(clean)
    return titles

# ─────────────────────────────────────────────
# Core BOM scraper — returns list of dicts
# {title, gross (Decimal), distributor, release_date (datetime|None)}
# ─────────────────────────────────────────────
def _fetch_bom_yearly(debug: bool = False):
    """
    Scrape Box Office Mojo yearly chart.
    BOM table columns (2025/2026 layout):
      Rank | Release | Gross | Total Theaters | Release Date | Distributor
    Returns [] on failure.
    """
    time.sleep(random.uniform(1.0, 2.0))
    try:
        resp = requests.get(BOM_YEAR_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        if debug:
            print(f"[DEBUG] BOM yearly fetch failed: {e}")
        return []

    soup  = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        if debug:
            print("[DEBUG] BOM: no table found on yearly page")
        return []

    # Identify column positions from headers
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    if debug:
        print(f"[DEBUG] BOM table headers: {headers}")

    def col_idx(keys):
        for k in keys:
            for i, h in enumerate(headers):
                if k in h:
                    return i
        return None

    title_col = col_idx(["release", "movie", "title"])
    gross_col = col_idx(["gross"])
    dist_col  = col_idx(["distributor"])
    date_col  = col_idx(["date"])

    if debug:
        print(f"[DEBUG] BOM col map — title:{title_col} gross:{gross_col} dist:{dist_col} date:{date_col}")

    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        def cell(idx):
            if idx is None or idx >= len(tds):
                return ""
            return tds[idx].get_text(strip=True)

        # Title: prefer <a> link text inside the cell; also capture href for daily data
        title    = ""
        bom_href = None
        if title_col is not None and title_col < len(tds):
            a = tds[title_col].find("a")
            if a:
                title    = a.get_text(strip=True)
                bom_href = a.get("href")
            else:
                title = cell(title_col)
        if not title:
            continue

        # Gross
        gross_raw = re.sub(r"[^\d]", "", cell(gross_col)) if gross_col is not None else ""
        if not gross_raw or len(gross_raw) < 4:
            continue
        try:
            gross = Decimal(gross_raw)
        except Exception:
            continue

        # Distributor
        dist = cell(dist_col) if dist_col is not None else ""

        # Release date
        date_str   = cell(date_col) if date_col is not None else ""
        release_dt = None
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                release_dt = datetime.strptime(date_str.strip(), fmt)
                break
            except Exception:
                pass

        rows.append({
            "title":    title,
            "gross":    gross,
            "dist":     dist,
            "release":  release_dt,
            "bom_href": bom_href,   # e.g. "/title/tt1234567/"
        })

    if debug:
        print(f"[DEBUG] BOM: parsed {len(rows)} rows")
        for r in rows[:10]:
            print(f"   '{r['title']}' ${r['gross']:,} | {r['dist']} | {r['release']}")

    return rows

# ─────────────────────────────────────────────
# Fallback snapshot (update periodically)
# ─────────────────────────────────────────────
def _fallback_data():
    print("[INFO] Using hardcoded snapshot — update figures manually if needed.")
    return [
        {"title": "The Devil Wears Prada 2",   "gross": Decimal("200025726"), "dist": "Walt Disney Studios Motion Pictures", "release": datetime(2026, 5,  1)},
        {"title": "Michael",                    "gross": Decimal("128000000"), "dist": "Lionsgate",                          "release": datetime(2026, 5,  1)},
        {"title": "The Mandalorian and Grogu",  "gross": Decimal("102000000"), "dist": "Walt Disney Studios Motion Pictures", "release": datetime(2026, 5, 22)},
        {"title": "Mortal Kombat II",           "gross": Decimal("85000000"),  "dist": "Warner Bros. Pictures",              "release": datetime(2026, 5, 15)},
        {"title": "Obsession",                  "gross": Decimal("45000000"),  "dist": "Universal Pictures",                 "release": datetime(2026, 5, 15)},
    ]

# ─────────────────────────────────────────────
# Public API used by summer_pool.py
# ─────────────────────────────────────────────
def get_top_10_summer_movies(csv_path="summer_movies.csv", debug=False):
    raw_titles = load_summer_list(csv_path)
    norm_map   = {normalize_title(t): t for t in raw_titles}

    if debug:
        print(f"[DEBUG] Loaded {len(raw_titles)} raw titles from '{csv_path}':")
        for t in raw_titles[:10]:
            print("   ", t)
        print(f"[DEBUG] Norm-map keys (first 10): {list(norm_map.keys())[:10]}\n")

    data = _fetch_bom_yearly(debug=debug) or _fallback_data()

    if debug:
        print(f"[DEBUG] Fetched {len(data)} total box-office entries")
        for r in data[:20]:
            t = r["title"]
            print(f"   '{t}' → in list? {normalize_title(t) in norm_map}")
        print()

    matched = []
    for r in data:
        key = normalize_title(r["title"])
        if key in norm_map:
            matched.append({"title": norm_map[key], "gross": r["gross"]})

    matched.sort(key=lambda x: x["gross"], reverse=True)
    top10 = matched[:10]

    if debug:
        print(f"[DEBUG] Matched {len(matched)} of your titles; Top 10 is:")
        for i, m in enumerate(top10, 1):
            print(f"  {i}. {m['title']} — ${m['gross']:,}")
        print()

    return top10


def get_top_distributors_for_summer(limit: int = 5, debug: bool = False):
    """
    Sum gross by distributor, restricted to titles that appear in the
    summer CSV — avoids broken date parsing entirely.
    """
    csv_titles = load_summer_list()
    summer_norm = set(normalize_title(t) for t in csv_titles)

    data = _fetch_bom_yearly(debug=debug) or _fallback_data()

    totals    = {}
    breakdown = defaultdict(list)   # dist -> [(title, gross), ...]
    splitter  = re.compile(r"\s*/\s*")

    matched_norms = set()

    for r in data:
        norm = normalize_title(r["title"])
        if norm not in summer_norm:
            continue
        matched_norms.add(norm)
        gross = int(r["gross"])
        if gross <= 0:
            continue
        for part in splitter.split(r.get("dist") or ""):
            part = part.strip()
            if not part:
                continue
            dist = normalize_distributor(part)
            totals[dist] = totals.get(dist, 0) + gross
            breakdown[dist].append((r["title"], gross))

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]

    if debug:
        print("\n[DEBUG] ── Distributor breakdown ──")
        for dist, _ in ranked:
            movies = sorted(breakdown[dist], key=lambda x: x[1], reverse=True)
            total  = sum(g for _, g in movies)
            print(f"\n  {dist} — ${total:,}")
            for title, gross in movies:
                print(f"      ${gross:>15,}  {title}")

        print("\n[DEBUG] ── CSV titles with NO BOM match (possible mismatches) ──")
        unmatched = [t for t in csv_titles if normalize_title(t) not in matched_norms]
        for t in unmatched:
            print(f"  ✗ {t}")

        print(f"\n[DEBUG] Top {limit} distributors:")
        for i, (d, g) in enumerate(ranked, 1):
            print(f"  {i}. {d} — ${g:,}")

    return ranked


# ─────────────────────────────────────────────
# Daily gross history via Box Office Mojo
# Strategy:
#   1. The yearly BOM scrape captured each movie's /title/ttXXX/ href
#   2. We fetch that title page to find the domestic release link (/release/rlXXX/)
#   3. We scrape the daily table from the release page
# A per-title BOM href lookup dict is built once and reused.
# ─────────────────────────────────────────────

# Module-level cache: populated by fetch_daily_grosses_for_top10
_BOM_HREFS: dict = {}   # normalize_title(title) -> "/title/ttXXX/"


def _scrape_bom_daily_from_release_url(release_url: str, title: str, debug: bool) -> list:
    """Given a BOM /release/rlXXX/ URL, scrape the daily gross table."""
    if debug:
        print(f"[DEBUG]   Fetching release page: {release_url}")
    time.sleep(random.uniform(0.8, 1.5))
    try:
        resp = requests.get(release_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        if debug:
            print(f"[DEBUG]   Release page fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # BOM daily table headers contain "Date", "Day", "Daily", "Cumulative"
    table = None
    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
        if any("daily" in h for h in hdrs) and any("cumulative" in h for h in hdrs):
            table = tbl
            break

    if not table:
        if debug:
            print(f"[DEBUG]   Daily table not found on release page for '{title}'")
        return []

    hdrs = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    if debug:
        print(f"[DEBUG]   Release page table headers: {hdrs}")

    def col(keys):
        for k in keys:
            for i, h in enumerate(hdrs):
                if k in h:
                    return i
        return None

    date_col  = col(["date"])
    daily_col = col(["daily"])
    cum_col   = col(["cumulative", "total"])

    if any(c is None for c in [date_col, daily_col, cum_col]):
        if debug:
            print(f"[DEBUG]   Could not map daily columns. Headers: {hdrs}")
        return []

    rows = []
    for day_num, tr in enumerate(table.find_all("tr")[1:], start=1):
        tds = tr.find_all("td")
        if len(tds) <= max(date_col, daily_col, cum_col):
            continue
        try:
            date_str = tds[date_col].get_text(strip=True)
            daily    = int(re.sub(r"[^\d]", "", tds[daily_col].get_text(strip=True)) or 0)
            cumul    = int(re.sub(r"[^\d]", "", tds[cum_col].get_text(strip=True)) or 0)
            parsed_date = None
            for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
                try:
                    parsed_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                    break
                except Exception:
                    pass
            rows.append({"day": day_num, "date": parsed_date or date_str,
                         "daily": daily, "cumulative": cumul})
        except Exception:
            continue

    if debug:
        print(f"[DEBUG]   Got {len(rows)} daily rows for '{title}'")
    return rows


def fetch_daily_grosses_for_title(title: str, debug: bool = False,
                                  bom_href: str = None) -> list:
    """
    Fetch daily gross history for one title via BOM.
    bom_href: the /title/ttXXX/ path captured from the yearly BOM page.
    """
    href = bom_href or _BOM_HREFS.get(normalize_title(title))
    if not href:
        if debug:
            print(f"[DEBUG] No BOM href for '{title}' — skipping daily data")
        return []

    # Ensure absolute URL
    if href.startswith("/"):
        title_url = "https://www.boxofficemojo.com" + href
    else:
        title_url = href

    if debug:
        print(f"[DEBUG] Fetching BOM title page for '{title}' → {title_url}")

    time.sleep(random.uniform(0.8, 1.5))
    try:
        resp = requests.get(title_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        if debug:
            print(f"[DEBUG]   Title page fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find domestic theatrical release link — looks for /release/rl... hrefs
    release_href = None
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/release/rl" in h and "domestic" not in h.lower():
            release_href = h
            break
        if "/release/rl" in h:
            release_href = h
            break

    if not release_href:
        if debug:
            print(f"[DEBUG]   No release link found on title page for '{title}'")
        return []

    release_url = ("https://www.boxofficemojo.com" + release_href
                   if release_href.startswith("/") else release_href)

    return _scrape_bom_daily_from_release_url(release_url, title, debug)


def fetch_daily_grosses_for_top10(top10: list, debug: bool = False) -> dict:
    """
    Fetch daily data for all top-10 movies.
    Re-uses the BOM yearly data (already scraped) to get hrefs efficiently.
    """
    global _BOM_HREFS

    # Rebuild href cache from a fresh yearly scrape
    yearly = _fetch_bom_yearly(debug=False)
    _BOM_HREFS = {
        normalize_title(r["title"]): r.get("bom_href")
        for r in yearly
        if r.get("bom_href")
    }

    result = {}
    for movie in top10:
        title      = movie["title"]
        bom_href   = _BOM_HREFS.get(normalize_title(title))
        result[title] = fetch_daily_grosses_for_title(title, debug=debug,
                                                      bom_href=bom_href)
    return result