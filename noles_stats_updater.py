"""
Noles in the Show — Stats Updater
==================================
Pulls current season stats from the MLB Stats API for all players
on the Noles in the Show roster, then:
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
LEVEL_SPORT_ID = {
    "MLB":         1,
    "AAA":         11,
    "AA":          12,
    "High-A":      13,
    "Low-A":       14,
    "Rookie":      15,
    "Independent": None,
}

HEADERS = {"User-Agent": "NolesInTheShow/1.0"}

# ── Manual level/team overrides (persists across Action runs) ─────────────────
# Add players here when their Excel entry lags behind reality.
# Format: "Player Name": {"level": "MLB", "team": "Team Name", "org": "Org Name"}
LEVEL_OVERRIDES = {
    "Shane Drohan": {"level": "MLB", "team": "Milwaukee Brewers", "org": "Milwaukee Brewers"},
}

# ── MiLB Profile URLs (used for direct player ID extraction and clickable links) ──
MILB_URLS = {
    "Shane Drohan":       "https://www.milb.com/player/shane-drohan-675660",
    "James Tibbs III":    "https://www.milb.com/player/james-tibbs-iii-696486",
    "CJ Van Eyk":         "https://www.milb.com/player/cj-van-eyk-669310",
    "Brandon Leibrandt":  "https://www.milb.com/player/brandon-leibrandt-605335",
    "Drew Parrish":       "https://www.milb.com/player/drew-parrish-669283",
    "DJ Stewart":         "https://www.milb.com/player/dj-stewart-621466",
    "Jamie Arnold":       "https://www.milb.com/player/jamie-arnold-701364",
    "Jackson Baumeister": "https://www.milb.com/player/jackson-baumeister-691765",
    "Jack Anderson":      "https://www.milb.com/player/jack-anderson-681252",
    "J.C. Flowers":       "https://www.milb.com/player/j-c-flowers-669001",
    "Alex Lodise":        "https://www.milb.com/player/alex-lodise-822676",
    "Marco Dinges":       "https://www.milb.com/player/marco-dinges-701392",
    "Jaime Ferrer":       "https://www.milb.com/player/jaime-ferrer-695634",
    "Carson Dorsey":      "https://www.milb.com/player/carson-dorsey-805955",
    "Wyatt Crowell":      "https://www.milb.com/player/wyatt-crowell-694573",
    "Gavin Adams":        "https://www.milb.com/player/gavin-adams-701400",
    "Yoel Tejeda Jr.":    "https://www.milb.com/player/yoel-tejeda-jr-701347",
    "Conner Whittaker":   "https://www.milb.com/player/conner-whittaker-701422",
    "Bryce Hubbart":      "https://www.milb.com/player/bryce-hubbart-687114",
    "Carson Montgomery":  "https://www.milb.com/player/carson-montgomery-691000",
    "Colton Vincent":     "https://www.milb.com/player/colton-vincent-694294",
    "Max Williams":       "https://www.milb.com/player/max-williams-703637",
    "Drew Faurot":        "https://www.milb.com/player/drew-faurot-702656",
    "Joey Volini":        "https://www.milb.com/player/joey-volini-804123",
    "Cam Leiter":         "https://www.milb.com/player/cam-leiter-804942",
    "Peyton Prescott":    "https://www.milb.com/player/peyton-prescott-807290",
    "Evan Chrest":        "https://www.milb.com/player/evan-chrest-805971",
    "Gage Harrelson":     "https://www.milb.com/player/gage-harrelson-702629",
    "Jaxson West":        "https://www.milb.com/player/jaxson-west-702486",
    "Maison Martinez":    "https://www.milb.com/player/maison-martinez-824506",
}

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
def get_player_stats(person_id: int, season: int, level: str = "") -> dict:
    """Fetch hitting and pitching stats for the current season only."""
    stats = {"hitting": {}, "pitching": {}, "season_used": season}
    sport_id = LEVEL_SPORT_ID.get(level)
    if sport_id is None:
        return stats  # Independent league or unknown — no API data available
    for group in ("hitting", "pitching"):
        try:
            r = requests.get(
                f"{MLB_API}/people/{person_id}/stats",
                params={"stats": "season", "season": season,
                        "group": group, "sportId": sport_id},
                headers=HEADERS, timeout=10
            )
            r.raise_for_status()
            splits = r.json().get("stats", [])
            if splits and splits[0].get("splits"):
                stats[group] = splits[0]["splits"][0].get("stat", {})
        except Exception as e:
            print(f"  ! Stats error for {person_id} ({group}): {e}")
    return stats
# ── Read Roster from Excel ────────────────────────────────────────────────────
def read_roster() -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb["Roster"]
    players = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        name, pos, org, level, team = row[0], row[1], row[2], row[3], row[4]
        draft_year  = row[5] if len(row) > 5 else None
        draft_round = row[6] if len(row) > 6 else None
        draft_pick  = row[7] if len(row) > 7 else None
        notes       = row[9] if len(row) > 9 else None
        milb_url    = MILB_URLS.get(name, "")
        if draft_round and str(draft_round).upper() in ("UDFA", "CB-A"):
            draft_str = f"{draft_year} · {draft_round}" if draft_year else str(draft_round)
        elif draft_year and draft_round:
            pick_str  = f" (Pick #{draft_pick})" if draft_pick and str(draft_pick) != "—" else ""
            draft_str = f"{draft_year} · Rd {draft_round}{pick_str}"
        else:
            draft_str = str(draft_year) if draft_year else ""
        if name:
            override = LEVEL_OVERRIDES.get(name, {})
            players.append({
                "name": name, "position": pos or "",
                "org":   override.get("org",   org   or ""),
                "level": override.get("level", level or ""),
                "team":  override.get("team",  team  or ""),
                "milb_url": milb_url,
                "draft_info": draft_str, "notes": notes or ""
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
        "H":   fmt(s.get("hits")),
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
        "MLB":         "#782F40",  # FSU Garnet
        "AAA":         "#B5451B",  # Burnt orange
        "AA":          "#CEB888",  # FSU Gold
        "High-A":      "#4A7C59",  # Forest green
        "Low-A":       "#2C5F8A",  # Steel blue
        "Rookie":      "#6B4C93",  # Purple
        "Independent": "#5a5a5a",  # Neutral gray
    }
    light_levels = {"AA"}  # light backgrounds need dark text

    # ── Dynamic hero stats ────────────────────────────────────────────────────
    total_players = len(player_data)
    mlb_count     = sum(1 for p in player_data if p.get("level") == "MLB")
    org_count     = len(set(p["org"] for p in player_data if p.get("org")))

    # ── Photo URL helper ──────────────────────────────────────────────────────
    def photo_url(p):
        mid = p.get("mlb_id")
        base = "https://img.mlbstatic.com/mlb-photos/image/upload"
        pid = mid if mid else "000000"
        # MLB players use the "67" style; MiLB players use "milb" style
        style = "67" if p.get("level") == "MLB" else "milb"
        return f"{base}/w_120,q_auto:best/v1/people/{pid}/headshot/{style}/current"

    # ── Card stat strip ──────────────────────────────────────────────────────
    def card_stat_strip(stats, pitcher):
        if not stats:
            return '<div class="no-stats-strip">No 2026 stats yet</div>'
        if pitcher:
            keys = [("W","W"),("L","L"),("SV","SV"),("IP","IP"),("ERA","ERA"),("WHIP","WHIP"),("K","K"),("G","G")]
            extra = ' cs-p'
            cols = 8
        else:
            keys = [("G","G"),("AB","AB"),("AVG","AVG"),("H","H"),("HR","HR"),("RBI","RBI")]
            extra = ' cs-p'
            cols = 6
        cells = "".join(
            f'<div class="cs{extra}"><div class="cs-val">{stats.get(k,"—")}</div>'
            f'<div class="cs-lbl">{lbl}</div></div>'
            for k, lbl in keys
        )
        return f'<div class="card-stats-strip" style="grid-template-columns:repeat({cols},1fr)">{cells}</div>'

    # ── Modal stats table ─────────────────────────────────────────────────────
    def modal_stats_html(stats, pitcher):
        if not stats:
            return '<div class="no-stats-modal">No stats yet — season in progress</div>'
        hdrs = ["G","GS","W","L","SV","IP","ERA","WHIP","K","BB"] if pitcher else \
               ["G","AB","AVG","HR","RBI","R","SB","OBP","SLG","OPS"]
        th = "".join(f"<th>{h}</th>" for h in hdrs)
        td = "".join(f"<td>{stats.get(h,'—')}</td>" for h in hdrs)
        return (f'<table class="modal-stats-table"><thead><tr>{th}</tr></thead>'
                f'<tbody><tr>{td}</tr></tbody></table>')

    # ── Card view ─────────────────────────────────────────────────────────────
    def player_card(p, idx):
        stats   = p.get("stats_fmt", {})
        lvl     = p["level"]
        color   = level_colors.get(lvl, "#555")
        txt     = "#333" if lvl in light_levels else "white"
        pitcher = is_pitcher(p["position"])
        strip   = card_stat_strip(stats, pitcher)
        draft   = p.get("draft_info", "")
        initials = "".join(w[0].upper() for w in p["name"].split()[:2])
        return (
            f'<div class="card" data-level="{lvl}" data-name="{p["name"].lower()}" '
            f'onclick="openModal({idx})" style="cursor:pointer">'
            f'<div class="card-top" style="background:{color}"></div>'
            f'<div class="card-main">'
            f'<div class="card-photo-wrap">'
            f'<div class="card-photo-init">{initials}</div>'
            f'<img class="card-photo" src="{photo_url(p)}" alt="{p["name"]}" '
            f'onload="if(this.naturalWidth<10)this.style.display=\'none\'" '
            f'onerror="var t=this,s=t.src;if(!t.dataset.tried){{t.dataset.tried=1;'
            f't.src=s.includes(\'/67/\')?s.replace(\'/67/\',\'/milb/\'):s.replace(\'/milb/\',\'/67/\');}}'
            f'else{{t.style.display=\'none\';}}">'
            f'</div>'
            f'<div class="card-info">'
            f'<div class="card-name">{p["name"]}</div>'
            f'<div class="card-pos-team">{p["position"]} · {p["team"]}</div>'
            f'<span class="lvl-badge" style="background:{color};color:{txt}">{lvl}</span>'
            f'<div class="card-draft">{draft}</div>'
            f'</div>'
            f'</div>'
            f'{strip}'
            f'</div>'
        )

    cards_html = "\n".join(player_card(p, i) for i, p in enumerate(sorted_players))

    # ── Build modal JSON (done outside f-string to avoid brace conflicts) ─────
    import json as _json
    modal_players = []
    for p in sorted_players:
        stats   = p.get("stats_fmt", {})
        pitcher = is_pitcher(p["position"])
        lvl     = p["level"]
        team_line = p["team"] if p["team"] == p["org"] else f'{p["team"]} ({p["org"]})'
        meta_html = (
            f'<strong>{p["position"]}</strong> · {team_line}<br>'
            f'<span style="color:#888">{lvl}</span>'
        )
        modal_players.append({
            "name":      p["name"],
            "posTeam":   f'{p["position"]} · {team_line}',
            "pos":       p["position"],
            "org":       p["org"],
            "team":      p["team"],
            "lvl":       lvl,
            "color":     level_colors.get(lvl, "#555"),
            "txt":       "#333" if lvl in light_levels else "white",
            "draft":     p.get("draft_info", ""),
            "notes":     p.get("notes", "") or "",
            "milb_url":  p.get("milb_url", ""),
            "photo":     photo_url(p),
            "initials":  "".join(w[0].upper() for w in p["name"].split()[:2]),
            "metaHtml":  meta_html,
            "statsHtml": modal_stats_html(stats, pitcher),
        })
    modal_json_str = _json.dumps(modal_players, ensure_ascii=False)

    # ── List/table view ───────────────────────────────────────────────────────
    def table_row(p):
        lvl   = p["level"]
        color = level_colors.get(lvl, "#555")
        txt   = "#333" if lvl in light_levels else "white"
        stats = p.get("stats_fmt", {})
        pitcher = is_pitcher(p["position"])
        if pitcher:
            s1_lbl, s1_val = "ERA",  stats.get("ERA", "—")
            s2_lbl, s2_val = "IP",   stats.get("IP",  "—")
            s3_lbl, s3_val = "WHIP", stats.get("WHIP","—")
        else:
            s1_lbl, s1_val = "AVG",  stats.get("AVG", "—")
            s2_lbl, s2_val = "HR",   stats.get("HR",  "—")
            s3_lbl, s3_val = "OPS",  stats.get("OPS", "—")
        org_cell = p.get("org", "") if lvl != "MLB" else "—"
        return f'''
        <tr data-level="{lvl}" data-name="{p["name"].lower()}">
          <td class="td-name">
            <img class="row-photo" src="{photo_url(p)}" alt="{p["name"]}" onerror="this.style.display='none'">
            <span class="name-text">{f'<a href="{p["milb_url"]}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;">{p["name"]}</a>' if p.get("milb_url") else p["name"]} <span class="pos-tag">{p["position"]}</span></span>
          </td>
          <td><span class="lvl-badge" style="background:{color};color:{txt}">{lvl}</span></td>
          <td class="td-team">{p["team"]}</td>
          <td class="td-org">{org_cell}</td>
          <td class="td-stat"><span class="stat-lbl-sm">{s1_lbl}</span> {s1_val}</td>
          <td class="td-stat"><span class="stat-lbl-sm">{s2_lbl}</span> {s2_val}</td>
          <td class="td-stat"><span class="stat-lbl-sm">{s3_lbl}</span> {s3_val}</td>
        </tr>'''

    rows_html = "\n".join(table_row(p) for p in sorted_players)

    # ── Level filter buttons (color-coordinated) ──────────────────────────────
    level_btns = ('<button class="filter-btn active" data-color="#782F40" '
                  'onclick="filterLevel(\'all\',this)">All</button>\n')
    for lvl in levels_order:
        if any(p["level"] == lvl for p in player_data):
            c = level_colors.get(lvl, "#555")
            level_btns += (f'<button class="filter-btn" data-color="{c}" '
                           f'onclick="filterLevel(\'{lvl}\',this)">{lvl}</button>\n')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Noles in the Show — {SEASON} Stats</title>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-4259331118703482"
     crossorigin="anonymous"></script>
<style>
:root {{
  --garnet: #782F40; --garnet-dark: #5a1f2d; --garnet-light: #9e4055;
  --gold: #CEB888;   --gold-light: #e8d9b0;  --gold-dark: #a8955e;
  --cream: #FAF7F2;  --border: #e0d8d0;
  --shadow: 0 2px 12px rgba(120,47,64,0.10);
}}
*,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ scroll-behavior: smooth; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--cream); color: #1a1a1a; line-height: 1.5; }}

/* ── Nav ── */
nav {{ background: var(--garnet); padding: 0 32px; display: flex; align-items: center;
       justify-content: space-between; position: sticky; top: 0; z-index: 100;
       box-shadow: 0 2px 8px rgba(0,0,0,0.25); }}
.nav-brand {{ display: flex; align-items: center; gap: 10px; padding: 14px 0; text-decoration: none; }}
.nav-logo {{ width: 48px; height: 48px; border-radius: 6px; flex-shrink: 0;
              object-fit: cover; display: block; }}
.nav-title {{ color: white; font-weight: 700; font-size: 1.1rem; }}
.nav-sub   {{ color: var(--gold); font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; }}
.nav-links {{ display: flex; gap: 2px; margin: 0 24px; }}
.nav-links a {{ color: rgba(255,255,255,0.8); text-decoration: none; padding: 8px 14px;
                border-radius: 6px; font-size: 0.88rem; transition: all 0.15s; white-space: nowrap; }}
.nav-links a:hover {{ background: rgba(255,255,255,0.12); color: white; }}
.nav-links a.active {{ background: rgba(206,184,136,0.2); color: var(--gold); font-weight: 600; }}
.nav-updated {{ color: rgba(255,255,255,0.70); font-size: 0.72rem; margin-left: auto; white-space: nowrap; }}

/* ── Hero ── */
.hero {{ background: linear-gradient(135deg, var(--garnet-dark) 0%, var(--garnet) 55%, var(--garnet-light) 100%);
         padding: 28px 32px 24px; text-align: center; position: relative; overflow: hidden; }}
.hero::before {{ content:''; position: absolute; inset: 0;
  background: repeating-linear-gradient(45deg, transparent, transparent 40px,
    rgba(206,184,136,0.04) 40px, rgba(206,184,136,0.04) 80px); }}
.hero-content {{ position: relative; z-index: 1; max-width: 700px; margin: 0 auto; }}
.hero-eyebrow {{ display: inline-block; background: rgba(206,184,136,0.2);
  border: 1px solid rgba(206,184,136,0.4); color: var(--gold);
  font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase;
  padding: 4px 14px; border-radius: 20px; margin-bottom: 16px; }}
.hero h1 {{ color: white; font-size: 1.9rem; font-weight: 800; line-height: 1.15; margin-bottom: 18px; }}
.hero h1 span {{ color: var(--gold); }}
.hero-stats {{ display: flex; justify-content: center; gap: 40px;
               border-top: 1px solid rgba(206,184,136,0.2); padding-top: 18px; }}
.hero-stat .num {{ color: var(--gold); font-size: 2rem; font-weight: 800; line-height: 1; }}
.hero-stat .lbl {{ color: rgba(255,255,255,0.6); font-size: 0.72rem; text-transform: uppercase;
                    letter-spacing: 0.08em; margin-top: 4px; }}
.hero-updated {{ margin-top: 16px; padding-top: 14px; border-top: 1px solid rgba(206,184,136,0.2);
                  color: rgba(255,255,255,0.65); font-size: 0.8rem; letter-spacing: 0.03em; }}
.hero-updated strong {{ color: var(--gold); font-weight: 600; }}

/* ── Legacy accolades strip ── */
.legacy {{ background: var(--garnet-dark); border-bottom: 3px solid var(--gold); }}
.legacy-inner {{ max-width: 1100px; margin: 0 auto; padding: 20px 32px;
                 display: flex; gap: 0; flex-wrap: wrap; justify-content: space-around; }}
.legacy-item {{ text-align: center; padding: 10px 24px; border-right: 1px solid rgba(206,184,136,0.2); }}
.legacy-item:last-child {{ border-right: none; }}
.legacy-title {{ color: rgba(255,255,255,0.65); font-size: 0.68rem; font-weight: 600;
                 text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 5px; }}
.legacy-val {{ color: var(--gold); font-size: 1.25rem; font-weight: 800; line-height: 1.2; }}

/* ── Sections ── */
.section-wrap {{ max-width: 1200px; margin: 48px auto 0; padding: 0 32px; }}
.section-title {{ font-size: 1.3rem; font-weight: 700; color: var(--garnet);
                  border-left: 4px solid var(--gold); padding-left: 12px; margin-bottom: 20px; }}
.news-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }}
.news-card {{ background: white; border: 1px solid var(--border); border-radius: 10px;
              padding: 18px 20px; box-shadow: var(--shadow); }}
.news-date {{ font-size: 0.72rem; color: #999; text-transform: uppercase;
              letter-spacing: 0.06em; margin-bottom: 7px; }}
.news-card h4 {{ font-size: 0.95rem; font-weight: 700; color: var(--garnet);
                  margin-bottom: 7px; line-height: 1.35; }}
.news-card p {{ font-size: 0.83rem; color: #666; line-height: 1.55; }}
.news-tag {{ display: inline-block; background: var(--gold-light); color: var(--gold-dark);
             font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
             letter-spacing: 0.06em; padding: 2px 9px; border-radius: 10px; margin-top: 10px; }}
.about-box {{ background: white; border: 1px solid var(--border); border-radius: 10px;
              padding: 28px 32px; box-shadow: var(--shadow); line-height: 1.7; color: #444; }}
.about-box p {{ margin-bottom: 14px; font-size: 0.92rem; }}
.about-box p:last-child {{ margin-bottom: 0; }}
.about-box strong {{ color: var(--garnet); }}
.news-grid-wrap {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px,1fr)); gap: 16px; }}

/* ── Controls bar ── */
.controls {{ background: white; padding: 14px 32px; border-bottom: 1px solid var(--border);
             display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
             box-shadow: 0 1px 4px rgba(0,0,0,0.05); position: sticky; top: 64px; z-index: 90; }}
.controls-label {{ font-size: 0.78rem; font-weight: 700; color: #888;
                    text-transform: uppercase; letter-spacing: 0.05em; margin-right: 4px; }}
.filter-btn {{ padding: 5px 13px; border: 1.5px solid #ccc; background: white;
               border-radius: 20px; cursor: pointer; font-size: 0.78rem;
               color: #555; transition: all .15s; font-weight: 500; }}
.filter-btn:hover {{ border-color: var(--garnet); color: var(--garnet); }}
.search-box {{ padding: 6px 12px; border: 1.5px solid #ccc; border-radius: 20px;
               font-size: 0.82rem; width: 190px; outline: none; transition: border-color .15s; }}
.search-box:focus {{ border-color: var(--garnet); }}
.view-toggle {{ margin-left: auto; display: flex; gap: 4px; }}
.view-btn {{ padding: 5px 14px; border: 1.5px solid #ccc; background: white;
             border-radius: 20px; cursor: pointer; font-size: 0.78rem;
             color: #555; transition: all .15s; font-weight: 600; }}
.view-btn.active {{ background: var(--garnet); color: white; border-color: var(--garnet); }}
.controls-updated {{ margin-left: 12px; font-size: 0.72rem; color: #999; white-space: nowrap;
                      padding: 4px 0; display: flex; align-items: center; gap: 4px; }}
.controls-updated::before {{ content: '↻'; font-size: 0.78rem; color: var(--garnet); }}

/* ── Two-column roster layout ── */
.roster-layout {{ display: flex; gap: 24px; align-items: flex-start;
                  max-width: 1400px; margin: 0 auto; padding: 24px 32px; }}
.roster-main {{ flex: 1; min-width: 0; }}
.roster-sidebar {{ width: 300px; flex-shrink: 0; position: sticky; top: 118px;
                   display: flex; flex-direction: column; gap: 20px; }}

/* ── Sidebar widgets ── */
.sidebar-widget {{ background: white; border: 1px solid var(--border); border-radius: 10px;
                   overflow: hidden; box-shadow: var(--shadow); }}
.sidebar-widget-hdr {{ background: var(--garnet); color: white; padding: 11px 16px;
                        display: flex; align-items: center; gap: 8px;
                        font-weight: 700; font-size: 0.85rem; }}
.sidebar-widget-hdr a {{ color: var(--gold); text-decoration: none; font-size: 0.75rem;
                          margin-left: auto; border: 1px solid var(--gold);
                          padding: 2px 9px; border-radius: 12px; white-space: nowrap; }}
.sidebar-widget-hdr a:hover {{ background: var(--gold); color: var(--garnet); }}
.links-list {{ list-style: none; padding: 8px 0; }}
.links-list li {{ border-bottom: 1px solid var(--border); }}
.links-list li:last-child {{ border-bottom: none; }}
.links-list a {{ display: flex; align-items: center; gap: 10px; padding: 10px 16px;
                 color: #444; text-decoration: none; font-size: 0.85rem;
                 transition: background .12s; }}
.links-list a:hover {{ background: #fff5f6; color: var(--garnet); }}
.links-list .link-icon {{ font-size: 1rem; width: 20px; text-align: center; flex-shrink: 0; }}
.links-list .link-lbl {{ font-weight: 600; }}
.links-list .link-sub {{ font-size: 0.72rem; color: #aaa; display: block; margin-top: 1px; }}

/* ── Card grid ── */
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }}
.card {{ background: white; border-radius: 10px; overflow: hidden;
         box-shadow: var(--shadow); transition: transform .15s, box-shadow .15s;
         cursor: pointer; }}
.card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(120,47,64,0.18); }}
.card.hidden {{ display: none; }}
/* horizontal card */
.card-top {{ height: 5px; }}
.card-main {{ display: flex; align-items: center; gap: 12px; padding: 10px 14px 8px; }}
.card-photo-wrap {{ position:relative; width:54px; height:54px; flex-shrink:0; border-radius:50%; }}
.card-photo-init {{ position:absolute; inset:0; border-radius:50%; background:#782F40;
                    color:#CEB888; font-size:1.1rem; font-weight:700;
                    display:flex; align-items:center; justify-content:center; }}
.card-photo {{ position:absolute; inset:0; width:54px; height:54px; border-radius:50%;
               object-fit:cover; border:2px solid #eee; background:transparent; }}
.card-info {{ flex: 1; min-width: 0; }}
.card-name {{ font-size: 0.92rem; font-weight: 700; color: #1a1a1a; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }}
.card-pos-team {{ font-size: 0.7rem; color: #888; margin: 1px 0 4px;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.card-draft {{ font-size: 0.65rem; color: #aaa; margin-top: 3px; }}
/* stat strip on card */
.card-stats-strip {{ display: grid; grid-template-columns: repeat(4, 1fr);
                     border-top: 1px solid #f0f0f0; padding: 6px 14px 8px; gap: 4px; }}
.cs {{ text-align: center; }}
.cs-val {{ font-size: 0.88rem; font-weight: 700; color: var(--garnet); }}
.cs-lbl {{ font-size: 0.56rem; color: #bbb; text-transform: uppercase; margin-top: 1px; }}
.cs-p .cs-val {{ font-size: 0.72rem; }}
.cs-p .cs-lbl {{ font-size: 0.5rem; }}
.no-stats-strip {{ grid-column: 1/-1; text-align: center; color: #ccc; font-size: 0.68rem;
                   font-style: italic; padding: 4px 0; }}
/* ── Modal ── */
.modal-overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.55);
                  z-index:1000; justify-content:center; align-items:flex-start;
                  overflow-y:auto; padding: 40px 16px; }}
.modal-overlay.open {{ display:flex; }}
.modal-box {{ background:white; border-radius:14px; width:100%; max-width:520px;
              box-shadow:0 20px 60px rgba(0,0,0,.3); overflow:hidden;
              animation: modalIn .2s ease; }}
@keyframes modalIn {{ from {{opacity:0;transform:translateY(-20px)}} to {{opacity:1;transform:translateY(0)}} }}
.modal-header {{ background: var(--garnet); color:white;
                 padding: 20px 20px 16px; position:relative; }}
.modal-header h2 {{ margin:0; font-size:1.2rem; font-weight:700; }}
.modal-header .mh-sub {{ font-size:0.78rem; opacity:.8; margin-top:4px; }}
.modal-close {{ position:absolute; top:14px; right:16px; background:rgba(255,255,255,.2);
                border:none; color:white; font-size:1.2rem; cursor:pointer;
                border-radius:50%; width:32px; height:32px; display:flex;
                align-items:center; justify-content:center; line-height:1; }}
.modal-close:hover {{ background:rgba(255,255,255,.35); }}
.modal-body {{ padding: 20px; }}
.modal-photo-wrap {{ position:relative; width:80px; height:80px; float:right; margin:0 0 12px 16px;
                     border-radius:50%; border:3px solid var(--border); flex-shrink:0; }}
.modal-photo-init {{ position:absolute; inset:0; border-radius:50%; background:#782F40;
                     color:#CEB888; font-size:1.6rem; font-weight:700;
                     display:flex; align-items:center; justify-content:center; }}
.modal-photo {{ position:absolute; inset:0; width:100%; height:100%; border-radius:50%; object-fit:cover; }}
.modal-meta {{ font-size:0.82rem; color:#555; line-height:1.7; }}
.modal-meta strong {{ color:#222; }}
.modal-stats-table {{ width:100%; border-collapse:collapse; margin-top:14px;
                      font-size:0.83rem; clear:both; }}
.modal-stats-table th {{ background:var(--garnet); color:white; padding:6px 10px;
                          font-size:0.72rem; font-weight:600; text-transform:uppercase;
                          letter-spacing:.04em; text-align:center; }}
.modal-stats-table td {{ padding:7px 10px; border-bottom:1px solid #eee;
                          text-align:center; color:#333; font-weight:600; }}
.modal-stats-table tr:last-child td {{ border-bottom:none; }}
.modal-stats-table .lbl-col {{ text-align:left; color:#888; font-weight:400; }}
.modal-draft {{ margin-top:12px; font-size:0.78rem; color:#777; }}
.modal-notes {{ margin-top:8px; font-size:0.8rem; color:#555;
                background:#fafafa; border-radius:6px; padding:8px 12px;
                border-left:3px solid var(--garnet); }}
.no-stats-modal {{ color:#bbb; font-style:italic; font-size:0.82rem; text-align:center; padding:16px 0; }}

/* ── List/Table view ── */
.list-wrap {{ display: none; }}
.roster-card {{ background: white; border-radius: 10px; border: 1px solid var(--border);
                overflow: hidden; box-shadow: var(--shadow); }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
thead tr {{ background: var(--garnet); }}
thead th {{ color: white; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em;
            text-transform: uppercase; padding: 12px 14px; text-align: left; white-space: nowrap; }}
tbody tr {{ border-bottom: 1px solid var(--border); transition: background .1s; }}
tbody tr:last-child {{ border-bottom: none; }}
tbody tr:hover {{ background: #fff5f6; }}
tbody tr.hidden {{ display: none; }}
td {{ padding: 10px 14px; vertical-align: middle; }}
.td-name {{ display: flex; align-items: center; gap: 10px; font-weight: 600; white-space: nowrap; }}
.row-photo {{ width: 38px; height: 38px; border-radius: 50%; object-fit: cover;
              border: 2px solid var(--border); flex-shrink: 0; background: var(--gold-light); }}
.pos-tag {{ font-size: 0.72rem; font-weight: 400; color: #888; margin-left: 4px; }}
.lvl-badge {{ display: inline-block; padding: 3px 8px; border-radius: 12px;
              font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
              text-transform: uppercase; white-space: nowrap; }}
.td-team {{ color: #444; font-size: 0.85rem; }}
.td-org  {{ color: #777; font-size: 0.82rem; }}
.td-stat {{ font-size: 0.85rem; font-weight: 600; color: var(--garnet); white-space: nowrap; }}
.stat-lbl-sm {{ font-size: 0.68rem; font-weight: 400; color: #aaa; text-transform: uppercase; margin-right: 2px; }}

/* ── Footer ── */
footer {{ background: var(--garnet-dark); color: rgba(255,255,255,0.55);
          text-align: center; padding: 22px 32px; font-size: 0.8rem; margin-top: 48px; }}
footer a {{ color: var(--gold); text-decoration: none; }}

/* ── Responsive: Tablet (≤900px) ── */
@media (max-width: 900px) {{
  .roster-layout {{ flex-direction: column; padding: 16px 20px; }}
  .roster-sidebar {{ width: 100%; position: static; flex-direction: row; flex-wrap: wrap; gap: 16px; }}
  .sidebar-widget {{ flex: 1; min-width: 260px; }}
  .legacy-inner {{ gap: 0; }}
  .legacy-item {{ padding: 10px 14px; }}
  .section-wrap {{ padding: 0 20px; }}
  .controls {{ padding: 12px 20px; top: 58px; }}
}}

/* ── Responsive: Mobile (≤600px) ── */
@media (max-width: 600px) {{
  nav {{ padding: 0 16px; }}
  .nav-links {{ display: none; }}
  .nav-updated {{ display: none; }}
  .hero {{ padding: 20px 16px 18px; }}
  .hero h1 {{ font-size: 1.4rem; }}
  .hero-eyebrow {{ font-size: 0.65rem; }}
  .hero-stats {{ gap: 16px; flex-wrap: wrap; }}
  .hero-stat .num {{ font-size: 1.5rem; }}
  .legacy-inner {{ padding: 12px 16px; justify-content: flex-start; }}
  .legacy-item {{ padding: 8px 12px; width: 50%; border-right: none;
                  border-bottom: 1px solid rgba(206,184,136,0.2); }}
  .legacy-item:nth-child(odd) {{ border-right: 1px solid rgba(206,184,136,0.2); }}
  .legacy-val {{ font-size: 1rem; }}
  .controls {{ padding: 10px 16px; gap: 6px; top: 54px; }}
  .controls-label {{ display: none; }}
  .search-box {{ width: 130px; }}
  .view-toggle {{ margin-left: 0; }}
  .roster-layout {{ padding: 12px 16px; }}
  .grid {{ grid-template-columns: 1fr; gap: 8px; }}
  .card-photo {{ width: 46px; height: 46px; }}
  .card-name {{ font-size: 0.85rem; }}
  .cs-val {{ font-size: 0.8rem; }}
  .cs-lbl {{ font-size: 0.5rem; }}
  .modal-box {{ max-width: 100%; margin: 0; border-radius: 10px; }}
  .roster-sidebar {{ flex-direction: column; }}
  .sidebar-widget {{ min-width: unset; }}
  .section-wrap {{ padding: 0 16px; }}
  .news-grid-wrap {{ grid-template-columns: 1fr; }}
  .about-box {{ padding: 18px 16px; }}
  table {{ font-size: 0.78rem; }}
  td, thead th {{ padding: 8px 10px; }}
  .row-photo {{ width: 28px; height: 28px; }}
  footer {{ padding: 18px 16px; }}
}}
</style>
</head>
<body>

<!-- Nav -->
<nav>
  <a href="#home" class="nav-brand">
    <img class="nav-logo" src="logo.png" alt="Noles in the Show">
    <div>
      <div class="nav-title">Noles in the Show</div>
      <div class="nav-sub">FSU Baseball Alumni Tracker</div>
    </div>
  </a>
  <div class="nav-links">
    <a href="#home"   class="nav-link active">Home</a>
    <a href="#roster" class="nav-link">Roster</a>
    <a href="#news"   class="nav-link">News</a>
    <a href="#about"  class="nav-link">About</a>
  </div>
  <span class="nav-updated">Updated {updated}</span>
</nav>

<!-- Hero -->
<section class="hero" id="home">
  <div class="hero-content">
    <div class="hero-eyebrow">Florida State University</div>
    <h1>Tracking Every <span>Seminole</span> in Pro Baseball</h1>
    <div class="hero-stats">
      <div class="hero-stat"><div class="num">{total_players}</div><div class="lbl">Total Players</div></div>
      <div class="hero-stat"><div class="num">{mlb_count}</div><div class="lbl">On MLB Rosters</div></div>
      <div class="hero-stat"><div class="num">{org_count}</div><div class="lbl">Organizations</div></div>
      <div class="hero-stat"><div class="num">{SEASON}</div><div class="lbl">Season</div></div>
    </div>
    <div class="hero-updated">📅 Stats last updated: <strong>{updated} ET</strong></div>
  </div>
</section>

<!-- FSU Legacy Accolades -->
<div class="legacy">
  <div class="legacy-inner">
    <div class="legacy-item">
      <div class="legacy-title">Location</div>
      <div class="legacy-val">Tallahassee, FL</div>
    </div>
    <div class="legacy-item">
      <div class="legacy-title">Home Stadium</div>
      <div class="legacy-val">Dick Howser Stadium</div>
    </div>
    <div class="legacy-item">
      <div class="legacy-title">Conference</div>
      <div class="legacy-val">Atlantic Coast Conference</div>
    </div>
    <div class="legacy-item">
      <div class="legacy-title">CWS Appearances</div>
      <div class="legacy-val">24</div>
    </div>
  </div>
</div>

<!-- News Section (above roster) -->
<div class="section-wrap" id="news">
  <div class="section-title">Latest News &amp; Updates</div>
  <div class="news-grid-wrap">
    <div class="news-card">
      <div class="news-date">March 27, 2026</div>
      <h4>2026 Season Underway — Noles Spread Across Pro Ball</h4>
      <p>FSU alumni are active across all levels of professional baseball as the 2026 season gets rolling. Stats updated daily.</p>
      <span class="news-tag">Season Update</span>
    </div>
    <div class="news-card">
      <div class="news-date">March 2026</div>
      <h4>Cal Raleigh Opens Season with Seattle Mariners</h4>
      <p>Former Seminole backstop Cal Raleigh is back behind the plate for Seattle as one of the top catchers in the American League.</p>
      <span class="news-tag">MLB</span>
    </div>
    <div class="news-card">
      <div class="news-date">March 2026</div>
      <h4>Luke Weaver on the Mound for the New York Mets</h4>
      <p>Right-hander Luke Weaver looks to build on a strong 2025 campaign as part of the Mets rotation heading into the new season.</p>
      <span class="news-tag">MLB</span>
    </div>
    <div class="news-card">
      <div class="news-date">Ongoing</div>
      <h4>Minor League Pipeline Loaded with FSU Talent</h4>
      <p>From High-A to Triple-A, FSU alumni are climbing through organizations across the league.</p>
      <span class="news-tag">Minor Leagues</span>
    </div>
  </div>
</div>

<!-- Controls (sticky) -->
<div class="controls" id="roster">
  <span class="controls-label">Level:</span>
  {level_btns}
  <input class="search-box" type="text" placeholder="Search player…" oninput="applySearch(this.value)">
  <span class="controls-updated">Updated {updated} ET</span>
  <div class="view-toggle">
    <button class="view-btn active" id="btnCards" onclick="setView('cards')">⊞ Cards</button>
    <button class="view-btn" id="btnList"  onclick="setView('list')">≡ List</button>
  </div>
</div>

<!-- Two-column roster layout -->
<div class="roster-layout">

  <!-- Main: cards + list -->
  <div class="roster-main">
    <div class="grid" id="grid">
{cards_html}
    </div>
    <div class="list-wrap" id="listWrap">
      <div class="roster-card">
        <table>
          <thead>
            <tr>
              <th>Player</th><th>Level</th><th>Team</th><th>Organization</th><th colspan="3">Key Stats</th>
            </tr>
          </thead>
          <tbody id="listBody">
{rows_html}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="roster-sidebar">

    <!-- Twitter feed -->
    <div class="sidebar-widget">
      <div class="sidebar-widget-hdr">
        <span>𝕏 @NolesInTheShow</span>
        <a href="https://twitter.com/NolesInTheShow" target="_blank">Follow</a>
      </div>
      <a class="twitter-timeline"
         data-height="380"
         data-theme="light"
         data-chrome="noheader nofooter noborders"
         href="https://twitter.com/NolesInTheShow">
        Tweets by @NolesInTheShow
      </a>
    </div>

    <!-- Links -->
    <div class="sidebar-widget">
      <div class="sidebar-widget-hdr">🔗 FSU Baseball Links</div>
      <ul class="links-list">
        <li>
          <a href="https://fsuseminoles.com/sports/baseball" target="_blank">
            <span class="link-icon">⚾</span>
            <span><span class="link-lbl">FSU Baseball</span><span class="link-sub">Official Seminoles site</span></span>
          </a>
        </li>
        <li>
          <a href="https://twitter.com/FSUBaseball" target="_blank">
            <span class="link-icon">𝕏</span>
            <span><span class="link-lbl">@FSUBaseball</span><span class="link-sub">Official FSU Baseball Twitter</span></span>
          </a>
        </li>
        <li>
          <a href="https://www.baseball-reference.com/friv/colleges.fcgi?college=fsu" target="_blank">
            <span class="link-icon">📊</span>
            <span><span class="link-lbl">Baseball Reference</span><span class="link-sub">FSU alumni career stats</span></span>
          </a>
        </li>
        <li>
          <a href="https://www.mlb.com" target="_blank">
            <span class="link-icon">🏟</span>
            <span><span class="link-lbl">MLB.com</span><span class="link-sub">Major League Baseball</span></span>
          </a>
        </li>
        <li>
          <a href="https://garnetandgold.com" target="_blank">
            <span class="link-icon">🛒</span>
            <span><span class="link-lbl">Garnet &amp; Gold</span><span class="link-sub">Official FSU merchandise</span></span>
          </a>
        </li>
      </ul>
    </div>

  </div>
</div>

<!-- About Section -->
<div class="section-wrap" id="about" style="margin-bottom: 48px;">
  <div class="section-title">About Noles in the Show</div>
  <div class="about-box">
    <p><strong>Noles in the Show</strong> is a fan-run tracker dedicated to following Florida State University baseball alumni throughout their professional careers — from rookie ball all the way to the Major Leagues.</p>
    <p>Florida State has one of the most storied baseball programs in the country. Playing out of <strong>Dick Howser Stadium</strong> in Tallahassee, the Seminoles have made <strong>24 College World Series appearances</strong> and captured more than <strong>20 ACC Championships</strong>. The program has consistently produced professional talent, sending over <strong>350 players</strong> to the draft since the modern era began.</p>
    <p>This site pulls live stats directly from the MLB Stats API and refreshes regularly throughout the season. Stats reflect current-season performance across all levels of affiliated and independent professional baseball.</p>
    <p style="font-size:0.82rem; color:#aaa;">Noles in the Show is a fan site and is not affiliated with Florida State University or Major League Baseball. Data sourced from the MLB Stats API.</p>
  </div>
</div>

<!-- Modal overlay -->
<div class="modal-overlay" id="modalOverlay" onclick="maybeClose(event)">
  <div class="modal-box" id="modalBox">
    <div class="modal-header">
      <h2 id="mName">Player</h2>
      <div class="mh-sub" id="mSub"></div>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="modal-body">
      <div class="modal-photo-wrap">
        <div class="modal-photo-init" id="mPhotoInit"></div>
        <img class="modal-photo" id="mPhoto" src="" alt=""
          onload="if(this.naturalWidth<10)this.style.display='none'"
          onerror="var t=this,s=t.src;if(!t.dataset.tried){{t.dataset.tried=1;t.src=s.includes('/67/')?s.replace('/67/','/milb/'):s.replace('/milb/','/67/');}}else{{t.style.display='none';}}">
      </div>
      <div class="modal-meta" id="mMeta"></div>
      <div id="mStatsSection"></div>
      <div class="modal-draft" id="mDraft"></div>
      <div class="modal-notes" id="mNotes" style="display:none"></div>
    </div>
  </div>
</div>

<!-- Twitter widget script -->
<script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>

<!-- Footer -->
<footer>
  <p><strong style="color:white">Noles in the Show</strong> · Fan site, not affiliated with FSU or MLB.</p>
  <p style="margin-top:6px;">Data sourced from <a href="https://statsapi.mlb.com">MLB Stats API</a> · {SEASON} Season</p>
</footer>

<script>
MODAL_DATA_PLACEHOLDER

let currentLevel = 'all';
let currentSearch = '';
let currentView = 'cards';

function openModal(idx) {{
  const p = MODAL_DATA[idx];
  if (!p) return;
  document.getElementById('mName').textContent  = p.name;
  document.getElementById('mSub').textContent   = p.posTeam;
  const mPhoto = document.getElementById('mPhoto');
  const mPhotoInit = document.getElementById('mPhotoInit');
  mPhotoInit.textContent = p.initials || '?';
  mPhoto.dataset.tried = '';
  mPhoto.style.display = '';
  mPhoto.src = p.photo;
  mPhoto.alt = p.name;
  document.getElementById('mMeta').innerHTML    = p.metaHtml;
  document.getElementById('mStatsSection').innerHTML = p.statsHtml;
  const draftEl = document.getElementById('mDraft');
  draftEl.textContent = p.draft ? '🎓 Draft: ' + p.draft : '';
  const notesEl = document.getElementById('mNotes');
  if (p.notes) {{
    notesEl.textContent = p.notes;
    notesEl.style.display = 'block';
  }} else {{
    notesEl.style.display = 'none';
  }}
  document.getElementById('modalOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  document.getElementById('modalOverlay').classList.remove('open');
  document.body.style.overflow = '';
}}

function maybeClose(e) {{
  if (e.target === document.getElementById('modalOverlay')) closeModal();
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closeModal();
}});

function setView(v) {{
  currentView = v;
  document.getElementById('grid').style.display     = v === 'cards' ? 'grid'  : 'none';
  document.getElementById('listWrap').style.display = v === 'list'  ? 'block' : 'none';
  document.getElementById('btnCards').classList.toggle('active', v === 'cards');
  document.getElementById('btnList').classList.toggle('active',  v === 'list');
}}

function filterLevel(level, btn) {{
  currentLevel = level;
  document.querySelectorAll('.filter-btn').forEach(b => {{
    b.classList.remove('active');
    b.style.background = '';
    b.style.borderColor = '';
    b.style.color = '';
  }});
  btn.classList.add('active');
  const c = btn.dataset.color || '#782F40';
  btn.style.background  = c;
  btn.style.borderColor = c;
  const lightLevels = ['AA'];
  btn.style.color = lightLevels.includes(level) ? '#333' : 'white';
  applyFilters();
}}

function applySearch(q) {{
  currentSearch = q.toLowerCase();
  applyFilters();
}}

const navSections = ['home','roster','news','about'];
function updateActiveNav() {{
  const scrollY = window.scrollY + 80;
  let active = 'home';
  navSections.forEach(id => {{
    const el = document.getElementById(id);
    if (el && el.offsetTop <= scrollY) active = id;
  }});
  document.querySelectorAll('.nav-link').forEach(a => {{
    a.classList.toggle('active', a.getAttribute('href') === '#' + active);
  }});
}}
window.addEventListener('scroll', updateActiveNav, {{ passive: true }});

function applyFilters() {{
  document.querySelectorAll('.card').forEach(card => {{
    const levelMatch = currentLevel === 'all' || card.dataset.level === currentLevel;
    const nameMatch  = !currentSearch || card.dataset.name.includes(currentSearch);
    card.classList.toggle('hidden', !levelMatch || !nameMatch);
  }});
  document.querySelectorAll('#listBody tr').forEach(row => {{
    const levelMatch = currentLevel === 'all' || row.dataset.level === currentLevel;
    const nameMatch  = !currentSearch || row.dataset.name.includes(currentSearch);
    row.classList.toggle('hidden', !levelMatch || !nameMatch);
  }});
}}
</script>
</body>
</html>"""

    # Inject modal data (done outside f-string to avoid brace conflicts)
    html = html.replace("MODAL_DATA_PLACEHOLDER",
                        f"const MODAL_DATA = {modal_json_str};")

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ HTML dashboard generated → {HTML_PATH.name}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  Noles in the Show — Stats Updater  ({SEASON} Season)")
    print(f"{'='*55}\n")

    roster = read_roster()
    print(f"→ Loaded {len(roster)} players from roster\n")

    cache = load_cache()
    player_data = []

    print("→ Looking up player IDs and fetching stats...\n")
    for p in roster:
        name = p["name"]
        milb_url = p.get("milb_url", "")

        # Extract player ID directly from MiLB URL if available (more reliable than name search)
        if milb_url and milb_url.startswith("http"):
            try:
                pid = int(milb_url.rstrip("/").split("-")[-1])
                cache[name] = pid
                print(f"  \u2713 {name} \u2192 ID {pid} (from URL)")
            except (ValueError, IndexError):
                pid = find_player_id(name, cache)
        else:
            pid = find_player_id(name, cache)

        p["mlb_id"] = pid
        if pid:
            raw_stats = get_player_stats(pid, SEASON, p.get("level", ""))
            pitcher = is_pitcher(p["position"])
            raw = raw_stats["pitching"] if pitcher else raw_stats["hitting"]
            p["stats_fmt"]    = format_pitching(raw) if pitcher else format_hitting(raw)
            p["stats_raw"]    = raw_stats
            p["season_shown"] = raw_stats.get("season_used", SEASON)
        else:
            p["stats_fmt"]    = {}
            p["season_shown"] = SEASON

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
