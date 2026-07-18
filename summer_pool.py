#!/usr/bin/env python3
import argparse
import csv
import sys

from summer_box_office_fetcher import (
    get_top_10_summer_movies,
    get_top_distributors_for_summer,
    fetch_daily_grosses_for_top10,
    normalize as normalize_title,
)

# === MANUAL monthly opening-weekend winners ===
# (You can update as each month concludes.)
MONTHLY_WINNERS = {
    "May":    "The Mandalorian and Grogu",   # Update when May opening-weekend winner is confirmed
    "June":   "Toy Story 5",   # Update when June opening-weekend winner is confirmed
    "July":   None,   # Update when July opening-weekend winner is confirmed
    "August": None,   # Update when August opening-weekend winner is confirmed
}

# Points for distributor rank guesses
DIST_RANK_POINTS = {1: 5, 2: 3, 3: 1}

# ---------------------------------------------
# Entries loader (backwards compatible)
# ---------------------------------------------
DIST_HEADER_VARIANTS = [
    ("Dist 1", "Dist 2", "Dist 3"),
    ("Dist1", "Dist2", "Dist3"),
    ("Distributor 1", "Distributor 2", "Distributor 3"),
    ("Top Distributor 1", "Top Distributor 2", "Top Distributor 3"),
]
def normalize_dist(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    # common unifications to keep buckets consistent
    mapping = {
        "Walt Disney Studios Motion Pictures": "Walt Disney",
        "Disney": "Walt Disney",
        "The Walt Disney Studios": "Walt Disney",

        "Warner Bros. Pictures": "Warner Bros.",
        "Warner Bros": "Warner Bros.",
        "Warner Bros. Discovery": "Warner Bros.",

        "Universal Pictures": "Universal",

        "Sony Pictures Releasing": "Sony Pictures",
        "Columbia Pictures": "Sony Pictures",

        "Paramount": "Paramount Pictures",
    }
    return mapping.get(n, n)


def rank_distributors_from_bo(bo_rows, summer_norm_set, debug=False):
    """
    Sum grosses per distributor using the SAME box-office rows used for Top 10,
    restricted to titles that are in the summer CSV list.
    bo_rows: list of dicts with keys: 'title', 'gross', 'distributor'
    summer_norm_set: set of normalized titles from summer_movies.csv
    """
    from collections import defaultdict
    totals = defaultdict(int)
    kept_examples = []  # for debug

    for row in bo_rows:
        t = row.get('title', '')
        if not t:
            continue
        if normalize_title(t) not in summer_norm_set:
            continue

        g = row.get('gross', 0) or 0
        d = normalize_dist(row.get('distributor', ''))

        if not d:
            continue

        totals[d] += int(g)
        if debug and len(kept_examples) < 8:
            kept_examples.append((t, d, g))

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)

    if debug:
        print("\n[DEBUG] Distributor examples included (title, dist, gross):")
        for t,d,g in kept_examples:
            print(f"   - {t} | {d} | ${g:,}")

        print("\n[DEBUG] Top distributors (computed from box-office table + summer CSV):")
        for i,(d,amt) in enumerate(ranked[:10], 1):
            print(f"  {i}. {d} — ${amt:,}")

    return ranked
def _pick_existing_headers(header_row, variants):
    """Return the first variant tuple fully present in the header row."""
    lower = [h.lower() for h in header_row]
    for trio in variants:
        if all(h.lower() in lower for h in trio):
            return trio
    return None

def load_entries(path, debug=False):
    """
    Expected columns:
      Name, Pick 1 .. Pick 10, May, June, July, August,
      plus distributor guesses (Dist 1/2/3 or Distributor 1/2/3, etc.)
    """
    entries = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            dist_headers = _pick_existing_headers(header, DIST_HEADER_VARIANTS)

            for row in reader:
                name  = (row.get("Name") or "").strip()
                picks = [(row.get(f"Pick {i}") or "").strip() for i in range(1, 11)]
                monthly = {m: (row.get(m) or "").strip() for m in MONTHLY_WINNERS}

                # Distributor guesses (try to be flexible)
                dists = ["", "", ""]
                if dist_headers:
                    dists = [(row.get(dist_headers[0]) or "").strip(),
                             (row.get(dist_headers[1]) or "").strip(),
                             (row.get(dist_headers[2]) or "").strip()]

                entries.append({
                    "name": name,
                    "picks": picks,
                    "monthly": monthly,
                    "dists": dists,  # [first, second, third]
                })
    except FileNotFoundError:
        sys.exit(f"Entries file not found: {path}")

    if debug:
        print(f"[DEBUG] Loaded {len(entries)} entries from '{path}'")
    return entries

# ---------------------------------------------
# Scoring
# ---------------------------------------------
def score_entry(picks, actual_titles, monthly_guess, dist_guesses, dist_rankings):
    """
    Picks:
      +1 if anywhere in top 10
      +N bonus if in exact position N
    Monthly opening winners:
      +3 for each correct month
    Distributors:
      if dist_guesses[0] == 1st place → +5
      if dist_guesses[1] == 2nd place → +3
      if dist_guesses[2] == 3rd place → +1
    """
    total = 0

    # Top 10 placement (+1) and exact rank bonus (+N)
    for idx, pick in enumerate(picks, start=1):
        npick = normalize_title(pick)
        for pos, real in enumerate(actual_titles, start=1):
            if npick == normalize_title(real):
                total += 1
                if pos == idx:
                    total += pos
                break

    # Monthly +3
    for month, winner in MONTHLY_WINNERS.items():
        guess = (monthly_guess.get(month) or "")
        if winner and normalize_title(guess) == normalize_title(winner):
            total += 3

    # Distributor rank bonuses
    # dist_rankings is a mapping: distributor → rank (1..N)
    for wanted_rank, guess in enumerate(dist_guesses, start=1):
        if not guess:
            continue
        gnorm = normalize_title(guess)
        for dist, rank in dist_rankings.items():
            if normalize_title(dist) == gnorm and rank == wanted_rank:
                total += DIST_RANK_POINTS.get(wanted_rank, 0)
                break

    return total

# ---------------------------------------------
# Output
# ---------------------------------------------
def write_csv(path, scored, top10, top_dists):
    """
    CSV with three blocks:
      1) Top 10 Summer Movies
      2) Top 5 Distributors
      3) Leaderboard
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)

        # Top 10
        w.writerow(["# Top 10 Summer Movies", "Domestic Gross"])
        for i, m in enumerate(top10, start=1):
            w.writerow([f"{i}. {m['title']}", f"${m['gross']:,}"])
        w.writerow([])

        # Distributors
        w.writerow(["# Top 5 Distributors (May–Aug releases)", "Sum Domestic Gross"])
        for i, (d, g) in enumerate(top_dists, start=1):
            w.writerow([f"{i}. {d}", f"${g:,}"])
        w.writerow([])

        # Leaderboard
        w.writerow(["Rank", "Name", "Score"])
        for i, e in enumerate(scored, start=1):
            w.writerow([i, e["name"], e["score"]])

def write_html(results, top10, monthly_winners, top_dists, daily_data=None, path="leaderboard.html"):
    """
    Writes the leaderboard HTML page.
    daily_data: dict of { title -> list of {day, date, daily, cumulative} }
                If None or empty, the chart section is omitted.
    """
    import json

    # ── Palette: 10 distinct colors for the chart lines ──────────────────────
    CHART_COLORS = [
        "#f4c542", "#e05252", "#5b9cf4", "#52c97a", "#c47af5",
        "#f5a142", "#42c5c5", "#e07db8", "#a0c842", "#7090f5",
    ]

    # ── Serialize daily data for JS ───────────────────────────────────────────
    chart_block = ""
    if daily_data:
        datasets = []
        for idx, movie in enumerate(top10):
            title  = movie["title"]
            rows   = daily_data.get(title, [])
            color  = CHART_COLORS[idx % len(CHART_COLORS)]
            cum_pts  = [{"x": r["day"], "y": r["cumulative"]} for r in rows if r.get("cumulative")]
            daily_pts = [{"x": r["day"], "y": r["daily"]}     for r in rows if r.get("daily")]
            datasets.append({
                "title":      title,
                "color":      color,
                "cumulative": cum_pts,
                "daily":      daily_pts,
            })
        datasets_json = json.dumps(datasets)

        chart_block = f"""
    <!-- ═══════════════════════════════════════════════════
         BOX OFFICE TRAJECTORY CHART
    ════════════════════════════════════════════════════ -->
    <section class="chart-section">
      <h2 class="section-title">📈 Box Office Trajectory</h2>
      <p class="chart-desc">Days since each film's opening day &mdash; toggle between cumulative and daily gross.</p>

      <!-- Mode toggle -->
      <div class="toggle-bar">
        <button class="toggle-btn active" id="btn-cumulative" onclick="setMode('cumulative')">Cumulative Gross</button>
        <button class="toggle-btn" id="btn-daily" onclick="setMode('daily')">Daily Gross</button>
      </div>

      <!-- Movie filter chips -->
      <div class="chip-bar" id="chip-bar"></div>

      <div class="chart-wrap">
        <canvas id="trajectoryChart"></canvas>
      </div>
    </section>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <script>
    const RAW = {datasets_json};

    // State
    let mode    = 'cumulative';
    let visible = new Set(RAW.map(d => d.title));
    let chart   = null;

    function fmt(n) {{
      if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
      if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
      if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'K';
      return '$' + n;
    }}

    function buildDatasets() {{
      return RAW.filter(d => visible.has(d.title)).map(d => ({{
        label:           d.title,
        data:            mode === 'cumulative' ? d.cumulative : d.daily,
        borderColor:     d.color,
        backgroundColor: d.color + '28',
        pointRadius:     2,
        pointHoverRadius: 5,
        borderWidth:     2.5,
        tension:         0.35,
        fill:            mode === 'daily',
      }}));
    }}

    function render() {{
      const isMobile = window.innerWidth < 600;
      const ctx = document.getElementById('trajectoryChart').getContext('2d');
      if (chart) chart.destroy();
      chart = new Chart(ctx, {{
        type: 'line',
        data: {{ datasets: buildDatasets() }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: 'nearest', intersect: false, axis: 'x' }},
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              backgroundColor: '#1a1a2e',
              titleColor: '#f4c542',
              bodyColor: '#e0e0e0',
              padding: isMobile ? 8 : 12,
              bodyFont: {{ size: isMobile ? 11 : 13 }},
              callbacks: {{
                title: items => `Day ${{items[0].parsed.x}}`,
                label: ctx => ` ${{ctx.dataset.label.length > 20 && isMobile
                  ? ctx.dataset.label.substring(0, 18) + '…'
                  : ctx.dataset.label}}: ${{fmt(ctx.parsed.y)}}`
              }}
            }}
          }},
          scales: {{
            x: {{
              type: 'linear',
              title: {{ display: !isMobile, text: 'Day Since Opening', color: '#aaa', font: {{ size: 13 }} }},
              ticks: {{ color: '#aaa', font: {{ size: isMobile ? 10 : 12 }}, maxTicksLimit: isMobile ? 7 : 15 }},
              grid:  {{ color: 'rgba(255,255,255,0.06)' }},
            }},
            y: {{
              title: {{ display: !isMobile,
                text: mode === 'cumulative' ? 'Cumulative Domestic Gross' : 'Daily Domestic Gross',
                color: '#aaa', font: {{ size: 13 }} }},
              ticks: {{ color: '#aaa', font: {{ size: isMobile ? 10 : 12 }}, callback: v => fmt(v), maxTicksLimit: isMobile ? 5 : 8 }},
              grid:  {{ color: 'rgba(255,255,255,0.06)' }},
            }}
          }}
        }}
      }});
    }}
    window.addEventListener('resize', render);

    function setMode(m) {{
      mode = m;
      document.getElementById('btn-cumulative').classList.toggle('active', m === 'cumulative');
      document.getElementById('btn-daily').classList.toggle('active', m === 'daily');
      render();
    }}

    function toggleMovie(title, btn) {{
      if (visible.has(title)) {{
        if (visible.size === 1) return; // keep at least one
        visible.delete(title);
        btn.classList.remove('active');
      }} else {{
        visible.add(title);
        btn.classList.add('active');
      }}
      render();
    }}

    // Build chips
    const chipBar = document.getElementById('chip-bar');
    RAW.forEach(d => {{
      const btn = document.createElement('button');
      btn.className = 'chip active';
      btn.textContent = d.title;
      btn.style.setProperty('--chip-color', d.color);
      btn.onclick = () => toggleMovie(d.title, btn);
      chipBar.appendChild(btn);
    }});

    render();
    </script>
"""

    # ── Build full page ───────────────────────────────────────────────────────
    monthly_rows = ""
    for month in ("May", "June", "July", "August"):
        winner = monthly_winners.get(month)
        if winner:
            monthly_rows += f"<tr><td>{month}</td><td>{winner}</td><td class='score-cell'>+3</td></tr>\n"
        else:
            monthly_rows += f"<tr><td>{month}</td><td class='tbd'>TBD</td><td class='score-cell'>—</td></tr>\n"

    top10_rows = ""
    for i, m in enumerate(top10, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        top10_rows += f"<tr><td class='rank-col'>{medal}</td><td>{m['title']}</td><td class='money'>${m['gross']:,}</td></tr>\n"

    dist_rows = ""
    for i, (d, g) in enumerate(top_dists, 1):
        dist_rows += f"<tr><td class='rank-col'>{i}</td><td>{d}</td><td class='money'>${g:,}</td></tr>\n"

    leader_rows = ""
    for i, r in enumerate(results, 1):
        name  = r.get("name") or r.get("Name", "—")
        score = r.get("score") or r.get("Score", 0)
        cls   = " gold" if i == 1 else (" silver" if i == 2 else (" bronze" if i == 3 else ""))
        leader_rows += f"<tr class='leader-row{cls}'><td class='rank-col'>{i}</td><td>{name}</td><td class='score-cell'>{score}</td></tr>\n"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🎬 Summer Movie Pool 2026</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    /* ── Reset & base ───────────────────────────────── */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:         #0d0d1a;
      --surface:    #15152a;
      --surface2:   #1e1e38;
      --border:     rgba(255,255,255,0.08);
      --gold:       #f4c542;
      --silver:     #b0bec5;
      --bronze:     #cd7f32;
      --accent:     #5b9cf4;
      --text:       #e8e8f0;
      --muted:      #888;
      --font-head:  'Bebas Neue', Impact, sans-serif;
      --font-body:  'DM Sans', system-ui, sans-serif;
    }}
    body {{
      font-family: var(--font-body);
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 0 0 60px;
    }}

    /* ── Hero banner ────────────────────────────────── */
    .hero {{
      background: linear-gradient(135deg, #0d0d1a 0%, #1a0a2e 50%, #0d1a2e 100%);
      border-bottom: 1px solid var(--border);
      text-align: center;
      padding: 52px 20px 40px;
      position: relative;
      overflow: hidden;
    }}
    .hero::before {{
      content: '';
      position: absolute; inset: 0;
      background: radial-gradient(ellipse at 60% 0%, rgba(91,156,244,0.12) 0%, transparent 70%),
                  radial-gradient(ellipse at 20% 100%, rgba(244,197,66,0.08) 0%, transparent 60%);
      pointer-events: none;
    }}
    .hero-eyebrow {{
      font-family: var(--font-body);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 4px;
      text-transform: uppercase;
      color: var(--gold);
      margin-bottom: 10px;
    }}
    .hero h1 {{
      font-family: var(--font-head);
      font-size: clamp(3rem, 8vw, 6rem);
      letter-spacing: 3px;
      line-height: 1;
      color: #fff;
      text-shadow: 0 0 60px rgba(91,156,244,0.4);
      margin-bottom: 6px;
    }}
    .hero-sub {{
      color: var(--muted);
      font-size: 14px;
      letter-spacing: 1px;
    }}

    /* ── Layout ─────────────────────────────────────── */
    .container {{ max-width: 980px; margin: 0 auto; padding: 0 20px; }}

    /* ── Section titles ─────────────────────────────── */
    .section-title {{
      font-family: var(--font-head);
      font-size: 2rem;
      letter-spacing: 2px;
      color: var(--gold);
      margin: 48px 0 16px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }}

    /* ── Tables ─────────────────────────────────────── */
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      background: var(--surface2);
      color: var(--muted);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 2px;
      text-transform: uppercase;
      padding: 10px 14px;
      text-align: left;
    }}
    tbody tr {{
      border-bottom: 1px solid var(--border);
      transition: background 0.15s;
    }}
    tbody tr:hover {{ background: var(--surface2); }}
    td {{ padding: 11px 14px; font-size: 15px; }}
    .rank-col {{ width: 52px; color: var(--muted); font-size: 13px; }}
    .money {{ font-variant-numeric: tabular-nums; color: #8ecf8e; }}
    .score-cell {{ font-weight: 600; color: var(--accent); font-size: 16px; }}
    .tbd {{ color: var(--muted); font-style: italic; }}

    /* ── Leaderboard rows ───────────────────────────── */
    .leader-row.gold   td {{ color: var(--gold);   }}
    .leader-row.silver td {{ color: var(--silver); }}
    .leader-row.bronze td {{ color: var(--bronze); }}
    .leader-row.gold   .rank-col::before {{ content: '🥇 '; }}
    .leader-row.silver .rank-col::before {{ content: '🥈 '; }}
    .leader-row.bronze .rank-col::before {{ content: '🥉 '; }}
    .leader-row.gold, .leader-row.silver, .leader-row.bronze {{ font-weight: 600; }}

    /* ── Chart section ──────────────────────────────── */
    .chart-section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 28px 24px 24px;
      margin-top: 48px;
    }}
    .chart-section .section-title {{ margin-top: 0; }}
    .chart-desc {{ color: var(--muted); font-size: 14px; margin-bottom: 18px; }}
    .chart-wrap {{ position: relative; height: clamp(280px, 50vw, 420px); margin-top: 20px; }}
    .chart-wrap canvas {{ width: 100% !important; height: 100% !important; }}

    /* ── Toggle buttons ─────────────────────────────── */
    .toggle-bar {{ display: flex; gap: 8px; margin-bottom: 16px; }}
    .toggle-btn {{
      background: var(--surface2);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 6px;
      padding: 7px 18px;
      font-family: var(--font-body);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s;
    }}
    .toggle-btn.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .toggle-btn:hover:not(.active) {{ border-color: var(--accent); color: var(--accent); }}

    /* ── Chip filter ────────────────────────────────── */
    .chip-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-bottom: 12px;
    }}
    .chip {{
      background: transparent;
      border: 1.5px solid var(--chip-color, #888);
      color: var(--chip-color, #888);
      border-radius: 999px;
      padding: 6px 14px;
      font-family: var(--font-body);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s;
      opacity: 0.45;
      white-space: nowrap;
      min-height: 36px;
      display: flex;
      align-items: center;
    }}
    .chip.active {{ opacity: 1; background: color-mix(in srgb, var(--chip-color) 18%, transparent); }}
    .chip:hover {{ opacity: 0.85; }}

    /* ── Mobile overrides ───────────────────────────── */
    @media (max-width: 600px) {{
      .chart-section {{ padding: 16px 12px 16px; }}
      .toggle-bar {{ gap: 6px; }}
      .toggle-btn {{ padding: 8px 14px; font-size: 13px; flex: 1; text-align: center; }}
      td {{ padding: 9px 10px; font-size: 14px; }}
      .hero h1 {{ letter-spacing: 1px; }}
      .section-title {{ font-size: 1.6rem; margin: 32px 0 12px; }}
      .chip {{ font-size: 11px; padding: 5px 10px; min-height: 32px; }}
    }}
  </style>
</head>
<body>

  <div class="hero">
    <p class="hero-eyebrow">Summer 2026</p>
    <h1>Movie Pool</h1>
    <p class="hero-sub">Live standings &mdash; updated daily</p>
  </div>

  <div class="container">

    <!-- Top 10 -->
    <h2 class="section-title">🎬 Top 10 Summer Movies</h2>
    <table>
      <thead><tr><th class="rank-col">#</th><th>Movie</th><th>Domestic Gross</th></tr></thead>
      <tbody>{top10_rows}</tbody>
    </table>

    <!-- Chart -->
    {chart_block}

    <!-- Distributors -->
    <h2 class="section-title">🏢 Top 5 Distributors</h2>
    <table>
      <thead><tr><th class="rank-col">#</th><th>Distributor</th><th>Total Domestic (May–Aug)</th></tr></thead>
      <tbody>{dist_rows}</tbody>
    </table>

    <!-- Monthly -->
    <h2 class="section-title">📅 Monthly Opening-Weekend Winners</h2>
    <table>
      <thead><tr><th>Month</th><th>Winner</th><th>Bonus</th></tr></thead>
      <tbody>{monthly_rows}</tbody>
    </table>

    <!-- Leaderboard -->
    <h2 class="section-title">🏆 Pool Leaderboard</h2>
    <table>
      <thead><tr><th class="rank-col">#</th><th>Name</th><th>Score</th></tr></thead>
      <tbody>{leader_rows}</tbody>
    </table>

  </div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------
# Main
# ---------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="Summer Movie Pool Leaderboard")
    p.add_argument("--entries", required=True,
                   help="CSV of Name, Pick1..Pick10, May..Aug, plus distributor columns")
    p.add_argument("--csv", dest="csv_path", required=True,
                   help="CSV of all summer releases (column Title)")
    p.add_argument("--debug", action="store_true", help="Verbose logging")
    p.add_argument("--no-chart", action="store_true",
                   help="Skip fetching daily gross data (faster, omits chart)")
    args = p.parse_args()

    # 1) Top 10 (from your curated list)
    top10 = get_top_10_summer_movies(csv_path=args.csv_path, debug=args.debug)
    if not top10:
        sys.exit("No summer top 10 found—check your fetcher.")
    actual_titles = [m["title"] for m in top10]

    # 2) Top distributors
    top_dists = get_top_distributors_for_summer(limit=5, debug=args.debug)
    # map distributor → rank
    dist_rankings = {dist: i for i, (dist, _sum) in enumerate(top_dists, start=1)}

    # 3) Score everyone
    entries = load_entries(args.entries, debug=args.debug)
    scored = []
    for e in entries:
        pts = score_entry(
            picks=e["picks"],
            actual_titles=actual_titles,
            monthly_guess=e["monthly"],
            dist_guesses=e["dists"],
            dist_rankings=dist_rankings,
        )
        scored.append({"name": e["name"], "score": pts})
    scored.sort(key=lambda x: (-x["score"], x["name"]))

    # 4) Daily gross data for chart
    daily_data = {}
    if not args.no_chart:
        print("\nFetching daily gross histories for chart...")
        daily_data = fetch_daily_grosses_for_top10(top10, debug=args.debug)
        movies_with_data = sum(1 for v in daily_data.values() if v)
        print(f"  Got data for {movies_with_data}/{len(top10)} movies.")

    # 5) Console output
    print("\nCurrent Top 10 Summer Movies:")
    for i, m in enumerate(top10, 1):
        print(f"  {i}. {m['title']} — ${m['gross']:,}")

    print("\nTop 5 Distributors (May–Aug releases):")
    for i, (d, g) in enumerate(top_dists, 1):
        print(f"  {i}. {d} — ${g:,}")

    print("\nMonthly Opening-Weekend Winners (+3 pts each):")
    for month, winner in MONTHLY_WINNERS.items():
        if winner:
            print(f"  {month}: {winner}")

    print("\nPool Leaderboard:")
    for e in scored:
        print(f"  {e['name']}: {e['score']} pts")

    # 6) Export
    write_csv("leaderboard.csv", scored, top10, top_dists)
    write_html(scored, top10, MONTHLY_WINNERS, top_dists,
               daily_data=daily_data if not args.no_chart else None,
               path="leaderboard.html")
    print("\nSaved leaderboard.csv and leaderboard.html")

if __name__ == "__main__":
    main()
