"""
Noles in the Pros — Stats Updater
==================================
Pulls current season stats from the MLB Stats API for all players
on the Noles in the Pros roster, then:
  1. Updates a "2026 Stats" sheet in noles_in_the_pros.xlsx
  2. Generates/refreshes noles_dashboard.html

Run manually or via scheduled task. Requires: openpyxl, requests
  pip install openpyxl requests
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
EXCEL_PATH = BASE_DIR / "noles_in_the_pros.xlsx"
CACHE_PATH = BASE_DIR / "player_id_cache.json"
HTML_PATH  = BASE_DIR / "noles_dashboard.html"
SEASON     = datetime.now().year
MLB_API    = "https://statsapi.mlb.com/api/v1"

# sport IDs: 1=MLB, 11=AAA, 12=AA, 13=High-A, 14=Low-A, 15=Rookie/Short, 16=Complex
SPORT_IDS  = "1,11,12,13,14,15,16"

HEADERS = {"User-Agent": "NolesInThePros/1.0"}

# ── Player ID Cache ───────────────────────────────────────────────────────────
def load_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def find_player_id(name: str, cache: dict) -> int | None:
    """Search MLB Stats API for a player by name. Returns personId or None."""
    if name in cache:
        return cache[name]
    try:
        r = requests.get(
            f"{MLB_API}/people/search",
            params={"names": name, "sportIds": SPORT_IDS},
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        results = r.json().get("people", [])
        if results:
            pid = results[0]["id"]
            cache[name] = pid
            save_cache(cache)
            print(f"  ✓ Found {name} → ID {pid}")
            return pid
        else:
            print(f"  ✗ Not found in MLB API: {name} (likely independent league)")
            cache[name] = None
            save_cache(cache)
            return None
    except Exception as e:
        print(f"  ! Error looking up {name}: {e}")
        return None

# ── Stats Fetching ────────────────────────────────────────────────────────────
def get_player_stats(person_id: int, season: int) -> dict:
    """Fetch hitting and pitching stats for a player for the given season."""
    stats = {"hitting": {}, "pitching": {}}
    for group in ("hitting", "pitching"):
        try:
            r = requests.get(
                f"{MLB_API}/people/{person_id}/stats",
                params={"stats": "season", "season": season,
                        "group": group, "sportIds": SPORT_IDS},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            splits = r.json().get("stats", [])
            if splits and splits[0].get("splits"):
                stats[group] = splits[0]["splits"][0].get("stat", {})
        except Exception as e:
            print(f"    ! Stats error for {person_id} ({group}): {e}")
    return stats

# ── Read Roster from Excel ────────────────────────────────────────────────────
def read_roster() -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb["Roster"]
    players = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        name, pos, org, level, team = row[0], row[1], row[2], row[3], row[4]
        if name:
            players.append({
                "name": name, "position": pos or "",
                "org": org or "", "level": level or "",
                "team": team or ""
            })
    return players

# ── Format Stats for Display ─────────────────────────────────────────────────
def format_hitting(s: dict) -> dict:
    def fmt(val, decimals=0):
        if val is None: return "—"
        try:
            v = float(val)
            if decimals: return f"{v:.{decimals}f}".lstrip("0") or ".000"
            return str(int(v))
        except: return str(val)

    return {
        "G":   fmt(s.get("gamesPlayed")),
        "AB":  fmt(s.get("atBats")),
        "AVG": fmt(s.get("avg"), 3),
        "HR":  fmt(s.get("homeRuns")),
        "RBI": fmt(s.get("rbi")),
        "R":   fmt(s.get("runs")),
        "SB":  fmt(s.get("stolenBases")),
        "OBP": fmt(s.get("obp"), 3),
        "SLG": fmt(s.get("slg"), 3),
        "OPS": fmt(s.get("ops"), 3),
    }

def format_pitching(s: dict) -> dict:
    def fmt(val, decimals=0):
        if val is None: return "—"
        try:
            v = float(val)
            if decimals: return f"{v:.{decimals}f}"
            return str(int(v))
        except: return str(val)

    return {
        "G":    fmt(s.get("gamesPlayed")),
        "GS":   fmt(s.get("gamesStarted")),
        "W":    fmt(s.get("wins")),
        "L":    fmt(s.get("losses")),
        "SV":   fmt(s.get("saves")),
        "IP":   fmt(s.get("inningsPitched"), 1),
        "ERA":  fmt(s.get("era"), 2),
        "WHIP": fmt(s.get("whip"), 2),
        "K":    fmt(s.get("strikeOuts")),
        "BB":   fmt(s.get("baseOnBalls")),
    }

# Pitchers by position tag
PITCHERS = {"RHP", "LHP", "SP", "RP", "P"}

def is_pitcher(position: str) -> bool:
    return any(p in position.upper() for p in PITCHERS)

# ── Update Excel Stats Sheet ──────────────────────────────────────────────────
def update_excel(player_data: list[dict]):
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment

    wb = openpyxl.load_workbook(EXCEL_PATH)

    # Remove old stats sheet if present
    if "2026 Stats" in wb.sheetnames:
        del wb["2026 Stats"]

    ws = wb.create_sheet("2026 Stats")

    header_fill  = PatternFill("solid", fgColor="FF1A3A5C")
    header_font  = Font(bold=True, color="FFFFFFFF", size=10)
    section_fill = PatternFill("solid", fgColor="FFD6E4F0")
    section_font = Font(bold=True, size=10)
    center       = Alignment(horizontal="center")
    left         = Alignment(horizontal="left")

    batter_headers  = ["Name","Pos","Team","Level","G","AB","AVG","HR","RBI","R","SB","OBP","SLG","OPS","Last Updated"]
    pitcher_headers = ["Name","Pos","Team","Level","G","GS","W","L","SV","IP","ERA","WHIP","K","BB","Last Updated"]

    updated = datetime.now().strftime("%m/%d/%Y %H:%M")
    row_num = 1

    def write_section(title, headers, players_subset):
        nonlocal row_num
        # Section header
        ws.cell(row_num, 1, title).font = section_font
        ws.cell(row_num, 1).fill = section_fill
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=len(headers))
        row_num += 1

        # Column headers
        for col, h in enumerate(headers, 1):
            c = ws.cell(row_num, col, h)
            c.fill = header_fill
            c.font = header_font
            c.alignment = center
        row_num += 1

        # Data rows
        for p in players_subset:
            stats = p.get("stats_fmt", {})
            row_vals = [p["name"], p["position"], p["team"], p["level"]]
            for h in headers[4:-1]:
                row_vals.append(stats.get(h, "—"))
            row_vals.append(updated)
            for col, val in enumerate(row_vals, 1):
                c = ws.cell(row_num, col, val)
                c.alignment = center if col > 2 else left
            row_num += 1
        row_num += 1  # blank row between sections

    # Split into pitchers and hitters
    levels_order = ["MLB", "AAA", "AA", "High-A", "Low-A", "Rookie", "Independent"]
    hitters  = [p for p in player_data if not is_pitcher(p["position"]) and p.get("stats_fmt")]
    pitchers = [p for p in player_data if is_pitcher(p["position"]) and p.get("stats_fmt")]

    # Sort each group by level
    level_rank = {l: i for i, l in enumerate(levels_order)}
    hitters.sort(key=lambda p: level_rank.get(p["level"], 99))
    pitchers.sort(key=lambda p: level_rank.get(p["level"], 99))

    write_section(f"⚾  HITTERS — {SEASON} Stats", batter_headers, hitters)
    write_section(f"🎯  PITCHERS — {SEASON} Stats", pitcher_headers, pitchers)

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 28
    ws.column_dimensions["D"].width = 12
    for col in ["E","F","G","H","I","J","K","L","M","N"]:
        ws.column_dimensions[col].width = 8
    ws.column_dimensions["O"].width = 18

    wb.save(EXCEL_PATH)
    print(f"  ✓ Excel 'Stats' sheet updated ({len(player_data)} players)")

# ── Generate HTML Dashboard ───────────────────────────────────────────────────
def generate_html(player_data: list[dict]):
    updated = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    levels_order = ["MLB", "AAA", "AA", "High-A", "Low-A", "Rookie", "Independent"]
    level_rank = {l: i for i, l in enumerate(levels_order)}
    sorted_players = sorted(player_data, key=lambda p: level_rank.get(p["level"], 99))

    level_colors = {
        "MLB":         "#1a3a5c",
        "AAA":         "#2c5f8a",
        "AA":          "#3a7ab5",
        "High-A":      "#4a8f6b",
        "Low-A":       "#7a6f2e",
        "Rookie":      "#8a4a2e",
        "Independent": "#5a5a5a",
    }

    def player_card(p):
        stats = p.get("stats_fmt", {})
        lvl   = p["level"]
        color = level_colors.get(lvl, "#555")
        pid   = is_pitcher(p["position"])

        if pid:
            stat_keys = [("ERA","ERA"),("IP","IP"),("W","W"),("L","L"),
                         ("SV","SV"),("K","K"),("BB","BB"),("WHIP","WHIP")]
        else:
            stat_keys = [("AVG","AVG"),("HR","HR"),("RBI","RBI"),("OPS","OPS"),
                         ("G","G"),("R","R"),("SB","SB"),("OBP","OBP")]

        stats_html = ""
        for key, label in stat_keys:
            val = stats.get(key, "—")
            stats_html += f'<div class="stat"><div class="stat-val">{val}</div><div class="stat-lbl">{label}</div></div>'

        no_data = not stats
        no_data_msg = '<div class="no-data">Season not started or not in MLB system</div>' if no_data else ""

        return f'''
        <div class="card" data-level="{lvl}">
          <div class="card-header" style="background:{color}">
            <div class="card-name">{p["name"]}</div>
            <div class="card-meta">{p["position"]} · {p["team"]}</div>
          </div>
          <div class="card-level" style="color:{color}">{lvl}</div>
          <div class="card-stats">{stats_html}{no_data_msg}</div>
        </div>'''

    cards_html = "\n".join(player_card(p) for p in sorted_players)

    level_btns = '<button class="filter-btn active" onclick="filterLevel(\'all\', this)">All</button>\n'
    for lvl in levels_order:
        if any(p["level"] == lvl for p in player_data):
            level_btns += f'<button class="filter-btn" onclick="filterLevel(\'{lvl}\', this)">{lvl}</button>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Noles in the Pros — {SEASON} Stats</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f4f8; color: #333; }}
  header {{ background: #1a3a5c; color: white; padding: 24px 32px; }}
  header h1 {{ font-size: 1.8rem; font-weight: 700; letter-spacing: -0.5px; }}
  header p  {{ font-size: 0.85rem; opacity: 0.75; margin-top: 4px; }}
  .controls {{ background: white; padding: 16px 32px; border-bottom: 1px solid #dde3ea;
               display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  .controls label {{ font-size: 0.8rem; color: #666; margin-right: 4px; font-weight: 600; }}
  .filter-btn {{ padding: 6px 14px; border: 1.5px solid #c0cad6; background: white;
                 border-radius: 20px; cursor: pointer; font-size: 0.78rem;
                 color: #444; transition: all .15s; font-weight: 500; }}
  .filter-btn:hover  {{ border-color: #1a3a5c; color: #1a3a5c; }}
  .filter-btn.active {{ background: #1a3a5c; color: white; border-color: #1a3a5c; }}
  .search-box {{ margin-left: auto; padding: 6px 12px; border: 1.5px solid #c0cad6;
                 border-radius: 20px; font-size: 0.82rem; width: 200px; outline: none; }}
  .search-box:focus {{ border-color: #1a3a5c; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
           gap: 16px; padding: 24px 32px; }}
  .card {{ background: white; border-radius: 10px; overflow: hidden;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); transition: transform .15s, box-shadow .15s; }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,.12); }}
  .card.hidden {{ display: none; }}
  .card-header {{ padding: 14px 16px; color: white; }}
  .card-name {{ font-size: 1rem; font-weight: 700; }}
  .card-meta {{ font-size: 0.75rem; opacity: 0.85; margin-top: 2px; }}
  .card-level {{ font-size: 0.7rem; font-weight: 700; letter-spacing: .5px;
                 text-transform: uppercase; padding: 6px 16px 0; }}
  .card-stats {{ display: grid; grid-template-columns: repeat(4, 1fr);
                 padding: 10px 12px 14px; gap: 8px; }}
  .stat {{ text-align: center; }}
  .stat-val {{ font-size: 1rem; font-weight: 700; color: #1a3a5c; }}
  .stat-lbl {{ font-size: 0.62rem; color: #999; text-transform: uppercase; margin-top: 1px; }}
  .no-data {{ grid-column: 1/-1; text-align: center; color: #aaa;
              font-size: 0.78rem; padding: 8px 0; font-style: italic; }}
  footer {{ text-align: center; padding: 24px; font-size: 0.78rem; color: #999; }}
</style>
</head>
<body>
<header>
  <h1>⚾ Noles in the Pros</h1>
  <p>{SEASON} Season Stats · Last updated: {updated}</p>
</header>
<div class="controls">
  <label>Level:</label>
  {level_btns}
  <input class="search-box" type="text" placeholder="Search player…" oninput="searchPlayers(this.value)">
</div>
<div class="grid" id="grid">
{cards_html}
</div>
<footer>Data sourced from MLB Stats API · Noles in the Pros · {SEASON}</footer>
<script>
function filterLevel(level, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(card => {{
    card.classList.toggle('hidden', level !== 'all' && card.dataset.level !== level);
  }});
}}
function searchPlayers(query) {{
  const q = query.toLowerCase();
  document.querySelectorAll('.card').forEach(card => {{
    const name = card.querySelector('.card-name').textContent.toLowerCase();
    card.classList.toggle('hidden', q.length > 0 && !name.includes(q));
  }});
  // Reset level filters when searching
  if (q.length > 0) {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  }}
}}
</script>
</body>
</html>"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ HTML dashboard generated → {HTML_PATH.name}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  Noles in the Pros — Stats Updater  ({SEASON} Season)")
    print(f"{'='*55}\n")

    roster = read_roster()
    print(f"→ Loaded {len(roster)} players from roster\n")

    cache = load_cache()
    player_data = []

    print("→ Looking up player IDs and fetching stats...\n")
    for p in roster:
        name = p["name"]
        pid  = find_player_id(name, cache)

        if pid:
            raw_stats = get_player_stats(pid, SEASON)
            pitcher = is_pitcher(p["position"])
            raw = raw_stats["pitching"] if pitcher else raw_stats["hitting"]
            p["stats_fmt"] = format_pitching(raw) if pitcher else format_hitting(raw)
            p["stats_raw"] = raw_stats
        else:
            p["stats_fmt"] = {}

        player_data.append(p)

    print(f"\n→ Updating Excel spreadsheet...")
    update_excel(player_data)

    print(f"\n→ Generating HTML dashboard...")
    generate_html(player_data)

    found = sum(1 for p in player_data if p.get("stats_fmt"))
    print(f"\n{'='*55}")
    print(f"  ✅ Done! {found}/{len(player_data)} players have stats data.")
    print(f"  📊 Excel: {EXCEL_PATH.name}")
    print(f"  🌐 Dashboard: {HTML_PATH.name}")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()
