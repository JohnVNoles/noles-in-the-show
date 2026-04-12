"""
Noles in the Show — Social Post Drafter
========================================
Runs after noles_stats_updater.py each day. Uses the same player_data
pipeline to draft ready-to-post content for X (@NolesInTheShow) and
Instagram (@nolesintheshow), then writes a styled HTML email body to
email_body.html for delivery via GitHub Actions (dawidd6/action-send-mail).

Sources checked (in order):
  1. MLB Stats API data (already fetched by the updater — reused here)
  2. news_cache.json — detects promotions and MLB debuts across days
  3. MLB/MiLB news feeds — scanned for player name mentions
  4. Team affiliate RSS feeds — checked for coverage of our players

Usage (standalone):
  python social_post_drafter.py

Optional env vars:
  SKIP_NEWS_SCRAPE=1 — skip RSS scraping (faster, stats-only mode)
"""

import json
import os
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
CACHE_PATH      = BASE_DIR / "player_id_cache.json"
NEWS_CACHE_PATH = BASE_DIR / "news_cache.json"
EXCEL_PATH      = BASE_DIR / "noles_in_the_pros.xlsx"
DRAFTS_PATH     = BASE_DIR / "social_drafts.json"   # rolling log of all drafts
SEASON          = datetime.now().year
MLB_API         = "https://statsapi.mlb.com/api/v1"
HEADERS         = {"User-Agent": "NolesInTheShow/1.0"}

X_HANDLE        = "@NolesInTheShow"
IG_HANDLE       = "@nolesintheshow"
FSU_HASHTAGS    = "#FSU #Seminoles #NolesInTheShow #FSUBaseball"
IG_HASHTAGS     = "#FSU #Seminoles #NolesInTheShow #FSUBaseball #MiLB #MLB #ProBall #BaseballTwitter #GoNoles"

TEAMS_WEBHOOK   = os.environ.get("TEAMS_WEBHOOK_URL", "")
EMAIL_BODY_PATH = BASE_DIR / "email_body.html"

# RSS/news feeds that cover minor and major league baseball
NEWS_FEEDS = [
    # MLB official news
    "https://www.mlb.com/feeds/news/rss.xml",
    # MiLB news
    "https://www.milb.com/news/rss",
    # Baseball America prospect news
    "https://www.baseballamerica.com/feed/",
    # ESPN MLB
    "https://www.espn.com/espn/rss/mlb/news",
]

# ── Shared helpers (duplicated from updater to keep this script standalone) ───
LEVEL_RANK = {"MLB": 0, "AAA": 1, "AA": 2, "High-A": 3, "Low-A": 4, "Rookie": 5, "Independent": 6}

def is_pitcher(pos: str) -> bool:
    return pos in ("SP", "RP", "CP", "LHP", "RHP", "P")

def load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}

def load_news_cache() -> dict:
    if NEWS_CACHE_PATH.exists():
        with open(NEWS_CACHE_PATH) as f:
            return json.load(f)
    return {}

def load_drafts() -> list:
    if DRAFTS_PATH.exists():
        with open(DRAFTS_PATH) as f:
            return json.load(f)
    return []

def save_drafts(drafts: list):
    with open(DRAFTS_PATH, "w") as f:
        json.dump(drafts, f, indent=2)

# ── Re-import player data from updater ───────────────────────────────────────
def get_player_data() -> list[dict]:
    """Import and run the same data pipeline as the main updater."""
    sys.path.insert(0, str(BASE_DIR))
    try:
        import noles_stats_updater as u
        roster     = u.read_roster()
        cache      = u.load_cache()
        player_data = []
        for p in roster:
            name    = p["name"]
            milb_url = p.get("milb_url", "")
            if milb_url and milb_url.startswith("http"):
                try:
                    pid = int(milb_url.rstrip("/").split("-")[-1])
                    cache[name] = pid
                except (ValueError, IndexError):
                    pid = u.find_player_id(name, cache)
            else:
                pid = u.find_player_id(name, cache)
            p["mlb_id"] = pid
            if pid:
                raw_stats = u.get_player_stats(pid, SEASON, p.get("level", ""))
                pitcher   = is_pitcher(p["position"])
                raw       = raw_stats["pitching"] if pitcher else raw_stats["hitting"]
                p["stats_fmt"]    = u.format_pitching(raw) if pitcher else u.format_hitting(raw)
                p["stats_raw"]    = raw_stats
                p["season_shown"] = raw_stats.get("season_used", SEASON)
            else:
                p["stats_fmt"]    = {}
                p["season_shown"] = SEASON
            player_data.append(p)
        return player_data
    except Exception as e:
        print(f"  ! Could not load player data: {e}")
        return []

# ── News scraping ─────────────────────────────────────────────────────────────
def scrape_news_mentions(player_names: list[str]) -> list[dict]:
    """Scan RSS feeds for articles mentioning any of our players."""
    mentions = []
    if os.environ.get("SKIP_NEWS_SCRAPE"):
        return mentions

    name_set = {n.lower() for n in player_names}
    # Also check last-name-only for common coverage style
    last_names = {n.split()[-1].lower() for n in player_names if len(n.split()) > 1}

    for feed_url in NEWS_FEEDS:
        try:
            r = requests.get(feed_url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            # Handle both RSS and Atom
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items[:30]:  # only check recent items
                title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
                link_el  = item.find("link")  or item.find("{http://www.w3.org/2005/Atom}link")
                desc_el  = item.find("description") or item.find("{http://www.w3.org/2005/Atom}summary")
                pub_el   = item.find("pubDate") or item.find("{http://www.w3.org/2005/Atom}published")

                title = title_el.text if title_el is not None else ""
                link  = link_el.get("href", link_el.text) if link_el is not None else ""
                desc  = desc_el.text if desc_el is not None else ""
                pub   = pub_el.text  if pub_el  is not None else ""

                combined = (title + " " + desc).lower()

                # Check for full name match first, then last-name match with FSU context
                matched_player = None
                for name in player_names:
                    if name.lower() in combined:
                        matched_player = name
                        break
                if not matched_player:
                    last = None
                    for ln in last_names:
                        if ln in combined and ("seminole" in combined or "fsu" in combined or "florida state" in combined):
                            # Find original full name
                            for name in player_names:
                                if name.split()[-1].lower() == ln:
                                    last = name
                                    break
                    matched_player = last

                if matched_player and title:
                    # Strip HTML tags from description
                    clean_desc = re.sub(r'<[^>]+>', '', desc or "").strip()[:300]
                    mentions.append({
                        "player": matched_player,
                        "title": title.strip(),
                        "link": link.strip() if isinstance(link, str) else "",
                        "summary": clean_desc,
                        "source": feed_url.split("/")[2],
                        "pub": pub,
                    })
        except Exception as e:
            print(f"  ! Feed error ({feed_url.split('/')[2]}): {e}")

    # Deduplicate by title
    seen = set()
    unique = []
    for m in mentions:
        if m["title"] not in seen:
            seen.add(m["title"])
            unique.append(m)
    return unique

# ── Draft generation ──────────────────────────────────────────────────────────
def build_drafts(player_data: list[dict], news_mentions: list[dict]) -> list[dict]:
    """
    Build a prioritized list of post drafts. Each draft has:
      - type: 'debut' | 'promotion' | 'pitching' | 'hitting' | 'news_mention' | 'weekly_summary'
      - priority: int (lower = more important)
      - x_post: str (≤280 chars, clean professional tone)
      - ig_caption: str (longer, more conversational, with hashtags)
      - player: str
      - source: str (what triggered this draft)
    """
    drafts  = []
    today   = datetime.now().strftime("%Y-%m-%d")
    news_cache = load_news_cache()
    date_str   = datetime.now().strftime("%B %d, %Y")
    month_str  = datetime.now().strftime("%B %Y")

    used_players = set()  # one post per player per day

    # ── Priority 1: MLB Debuts ────────────────────────────────────────────────
    for p in player_data:
        name  = p["name"]
        level = p.get("level", "")
        prev  = news_cache.get(name, {}).get("level", "")
        if level != "MLB" or name in used_players:
            continue
        if prev and prev != "MLB":
            pos  = p.get("position", "")
            team = p.get("team", "")
            org  = p.get("org", team)
            hand = "LHP" if "LHP" in pos else "RHP" if "RHP" in pos else ""
            role_str = f" ({hand})" if hand else ""

            x = (
                f"🎉 {name}{role_str} has reached the Major Leagues with the {org}.\n\n"
                f"Another Seminole makes it to The Show. {X_HANDLE}\n\n"
                f"#FSU #Seminoles #MLB"
            )
            ig = (
                f"🎉 {name} is in the Big Leagues!\n\n"
                f"The former Florida State Seminole{role_str} has been called up by the {org}, "
                f"earning a spot on an MLB roster. "
                f"From Tallahassee to The Show — this is what it's all about.\n\n"
                f"Follow along for daily stats and updates all season long.\n\n"
                f"{IG_HASHTAGS} #MLBDebut"
            )
            drafts.append({
                "priority": 1, "type": "debut", "player": name,
                "source": "Stats API — level change detected",
                "x_post": x, "ig_caption": ig,
            })
            used_players.add(name)

    # ── Priority 2: Promotions ────────────────────────────────────────────────
    for p in player_data:
        name  = p["name"]
        level = p.get("level", "")
        prev  = news_cache.get(name, {}).get("level", "")
        if name in used_players or level == "MLB" or not prev:
            continue
        if level in LEVEL_RANK and prev in LEVEL_RANK and LEVEL_RANK[level] < LEVEL_RANK[prev]:
            team = p.get("team", "")
            org  = p.get("org", "")
            pos  = p.get("position", "")

            x = (
                f"📈 {name} has been promoted to {level} with the {org}.\n\n"
                f"The former Seminole is now suiting up for the {team}. "
                f"Stats: nolesintheshow.com\n\n"
                f"#FSU #Seminoles #MiLB"
            )
            ig = (
                f"📈 Promotion alert — {name} is moving up!\n\n"
                f"The former Florida State Seminole has been promoted to {level} "
                f"in the {org} system, now playing for the {team}. "
                f"The Nole pipeline keeps producing talent at every level.\n\n"
                f"Check nolesintheshow.com for his full stats.\n\n"
                f"{IG_HASHTAGS} #Promotion #MiLB"
            )
            drafts.append({
                "priority": 2, "type": "promotion", "player": name,
                "source": f"Stats API — moved from {prev} to {level}",
                "x_post": x, "ig_caption": ig,
            })
            used_players.add(name)

    # ── Priority 3: News mentions from RSS ────────────────────────────────────
    for mention in news_mentions[:3]:  # cap at 3 external news items
        name = mention["player"]
        if name in used_players:
            continue
        p = next((pl for pl in player_data if pl["name"] == name), {})
        level = p.get("level", "")
        team  = p.get("team", "")
        title = mention["title"]
        link  = mention["link"]
        source = mention["source"]

        x = (
            f"📰 {name} ({level} - {team}) is making news.\n\n"
            f'"{title}"\n\n'
            f"via {source} | Full stats: nolesintheshow.com\n\n"
            f"#FSU #Seminoles"
        )
        # Trim X post if over 280
        if len(x) > 280:
            x = (
                f"📰 {name} ({level} — {team}) is making news.\n\n"
                f"Full stats: nolesintheshow.com\n\n"
                f"#FSU #Seminoles"
            )

        ig = (
            f"📰 {name} is getting some coverage.\n\n"
            f'"{title}"\n\n'
            f"The former Seminole is currently at {level} with the {team}. "
            f"Check nolesintheshow.com for his daily stats all season.\n\n"
            f"{IG_HASHTAGS}"
        )
        drafts.append({
            "priority": 3, "type": "news_mention", "player": name,
            "source": f"RSS — {source}: {title}",
            "x_post": x, "ig_caption": ig,
            "article_link": link,
        })
        used_players.add(name)

    # ── Priority 4: Pitching stat spotlight ──────────────────────────────────
    pitching_candidates = []
    for p in player_data:
        if p["name"] in used_players or not is_pitcher(p.get("position", "")):
            continue
        stats = p.get("stats_fmt", {})
        try:
            era  = float(stats.get("ERA", "99").replace("—", "99") or "99")
            ip   = float(stats.get("IP",  "0").replace("—", "0")  or "0")
            whip = float(stats.get("WHIP","99").replace("—", "99") or "99")
            ks   = int(stats.get("K",   "0").replace("—", "0")   or "0")
            w    = int(stats.get("W",   "0").replace("—", "0")   or "0")
            gs   = int(stats.get("GS",  "0").replace("—", "0")   or "0")
        except ValueError:
            continue
        if ip >= 5 and era <= 3.00:
            pitching_candidates.append((era, ip, whip, ks, w, gs, p))
    pitching_candidates.sort(key=lambda x: x[0])

    for era, ip, whip, ks, w, gs, p in pitching_candidates[:2]:
        name  = p["name"]
        if name in used_players:
            continue
        level = p.get("level", "")
        team  = p.get("team", "")
        org   = p.get("org", "")
        pos   = p.get("position", "")
        hand  = "LHP" if "LHP" in pos else "RHP"
        role  = "starter" if gs >= 2 else "reliever"

        # Build stat line
        era_str  = f"{era:.2f}"
        ip_str   = f"{ip:.1f}"
        stat_line = f"{era_str} ERA | {ip_str} IP | {ks} K | {whip:.2f} WHIP"

        if era == 0.00:
            headline_x  = f"{name} has yet to allow an earned run this season."
            headline_ig = f"scoreless innings to open {SEASON}"
        elif era < 1.50:
            headline_x  = f"{name} is one of the best {role}s at {level} right now."
            headline_ig = f"one of the better starts to a season you'll find at {level}"
        else:
            headline_x  = f"{name} is off to a strong start at {level} this season."
            headline_ig = f"a strong start to the {SEASON} campaign"

        x = (
            f"⚾ {name} | {hand} | {level} — {org}\n\n"
            f"{stat_line}\n\n"
            f"{headline_x}\n\n"
            f"Full stats: nolesintheshow.com | #FSU #Seminoles"
        )
        ig = (
            f"⚾ {name} is putting together {headline_ig}.\n\n"
            f"The former Florida State {hand} is pitching for the {team} at {level}.\n\n"
            f"📊 {stat_line}\n\n"
            f"Track all your former Noles at nolesintheshow.com — stats update every morning.\n\n"
            f"{IG_HASHTAGS}"
        )
        # Trim X if needed
        if len(x) > 280:
            x = f"⚾ {name} | {hand} | {level}\n\n{stat_line}\n\n{headline_x}\n\nnolesintheshow.com | #FSU #Seminoles"

        drafts.append({
            "priority": 4, "type": "pitching", "player": name,
            "source": f"Stats API — {era_str} ERA, {ip_str} IP",
            "x_post": x, "ig_caption": ig,
        })
        used_players.add(name)

    # ── Priority 5: Hitting stat spotlight ────────────────────────────────────
    hitting_candidates = []
    for p in player_data:
        if p["name"] in used_players or is_pitcher(p.get("position", "")):
            continue
        stats = p.get("stats_fmt", {})
        try:
            avg = float(stats.get("AVG", "0").replace("—", "0") or "0")
            ab  = int(stats.get("AB",  "0").replace("—", "0") or "0")
            hr  = int(stats.get("HR",  "0").replace("—", "0") or "0")
            rbi = int(stats.get("RBI", "0").replace("—", "0") or "0")
            ops = float(stats.get("OPS","0").replace("—", "0") or "0")
            sb  = int(stats.get("SB",  "0").replace("—", "0") or "0")
            h   = int(stats.get("H",   "0").replace("—", "0") or "0")
        except ValueError:
            continue
        if ab >= 10 and (avg >= 0.300 or hr >= 3 or (sb >= 4 and avg >= 0.270)):
            score = avg * 10 + hr * 0.5 + ops
            hitting_candidates.append((score, avg, ab, hr, rbi, ops, sb, h, p))
    hitting_candidates.sort(key=lambda x: -x[0])

    used_angles = set()
    for score, avg, ab, hr, rbi, ops, sb, h, p in hitting_candidates[:3]:
        name  = p["name"]
        if name in used_players:
            continue
        level = p.get("level", "")
        team  = p.get("team", "")
        org   = p.get("org", "")
        pos   = p.get("position", "")

        avg_str  = f".{int(avg * 1000):03d}"
        stat_line = f"{avg_str} AVG | {hr} HR | {rbi} RBI | {ops:.3f} OPS"
        if sb >= 4:
            stat_line += f" | {sb} SB"

        # Pick angle
        if hr >= 5 and avg >= 0.300 and "five_tool" not in used_angles:
            angle    = "five_tool"
            note_x   = f"Hitting {avg_str} with {hr} HR for the {team.split()[-1]}. Doing damage at {level}."
            note_ig  = f"hitting for average and power at {level} — the complete package"
        elif hr >= 5 and "power" not in used_angles:
            angle    = "power"
            note_x   = f"{hr} home runs already on the season. The power is real at {level}."
            note_ig  = f"making his power felt at {level} early in {SEASON}"
        elif sb >= 4 and avg >= 0.270 and "speed" not in used_angles:
            angle    = "speed"
            note_x   = f"Batting {avg_str} with {sb} steals. Hard to keep off the bases at {level}."
            note_ig  = f"one of the faster, more dynamic bats at {level} this season"
        elif "contact" not in used_angles:
            angle    = "contact"
            note_x   = f"One of the more consistent bats at {level} this year."
            note_ig  = f"quietly putting together one of the better batting lines at {level}"
        else:
            continue

        used_angles.add(angle)

        x = (
            f"🔥 {name} | {pos} | {level} — {org}\n\n"
            f"{stat_line}\n\n"
            f"{note_x}\n\n"
            f"nolesintheshow.com | #FSU #Seminoles"
        )
        ig = (
            f"🔥 {name} is {note_ig}.\n\n"
            f"The former Florida State Seminole is currently playing for the {team}.\n\n"
            f"📊 {stat_line}\n\n"
            f"Stats update daily at nolesintheshow.com — bookmark it to follow all your former Noles.\n\n"
            f"{IG_HASHTAGS}"
        )
        if len(x) > 280:
            x = f"🔥 {name} | {pos} | {level}\n\n{stat_line}\n\n{note_x}\n\nnolesintheshow.com | #FSU #Seminoles"

        drafts.append({
            "priority": 5, "type": "hitting", "player": name,
            "source": f"Stats API — {avg_str} AVG, {hr} HR",
            "x_post": x, "ig_caption": ig,
        })
        used_players.add(name)

    # ── Priority 6: Weekly roster summary (Sundays) ───────────────────────────
    if datetime.now().weekday() == 6 or not drafts:  # Sunday or no other content
        total  = len(player_data)
        mlb_ct = sum(1 for p in player_data if p.get("level") == "MLB")
        orgs   = len(set(p["org"] for p in player_data if p.get("org")))
        levels = {}
        for p in player_data:
            lvl = p.get("level", "Unknown")
            levels[lvl] = levels.get(lvl, 0) + 1

        level_lines = ", ".join(
            f"{ct} at {lvl}"
            for lvl, ct in sorted(levels.items(), key=lambda x: LEVEL_RANK.get(x[0], 99))
            if lvl != "Unknown"
        )

        x = (
            f"📊 {SEASON} Noles in the Show — Weekly Update\n\n"
            f"{total} former FSU Seminoles in pro ball across {orgs} organizations.\n"
            f"{mlb_ct} on active MLB rosters.\n\n"
            f"Full stats at nolesintheshow.com\n\n"
            f"#FSU #Seminoles #NolesInTheShow"
        )
        ig = (
            f"📊 {SEASON} Season Update — {month_str}\n\n"
            f"{total} former Florida State Seminoles are currently in professional baseball "
            f"across {orgs} different organizations.\n\n"
            f"Breakdown: {level_lines}.\n\n"
            f"{'Including ' + str(mlb_ct) + ' on active MLB rosters. ' if mlb_ct else ''}"
            f"The Seminole pipeline is producing at every level.\n\n"
            f"Daily stats for every player at nolesintheshow.com.\n\n"
            f"{IG_HASHTAGS}"
        )
        drafts.append({
            "priority": 6, "type": "weekly_summary", "player": "All",
            "source": "Weekly summary",
            "x_post": x, "ig_caption": ig,
        })

    # Sort by priority, take top 3 for the day
    drafts.sort(key=lambda d: d["priority"])
    return drafts[:3]

# ── Email body writer ────────────────────────────────────────────────────────
def write_email_body(drafts: list[dict]) -> bool:
    """Write a styled HTML email body to email_body.html for GitHub Actions to send."""
    date_str = datetime.now().strftime("%A, %B %d, %Y")

    draft_blocks = []
    for i, d in enumerate(drafts, 1):
        ptype   = d.get("type", "").replace("_", " ").title()
        player  = d.get("player", "")
        source  = d.get("source", "")
        x_post  = d.get("x_post", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ig_cap  = d.get("ig_caption", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        article = d.get("article_link", "")

        article_html = f'<p style="margin-top:10px"><a href="{article}" style="color:#782F40">Read article</a></p>' if article else ""

        draft_blocks.append(f"""
        <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:20px">
          <div style="background:#782F40;color:white;padding:8px 12px;border-radius:4px;margin-bottom:12px">
            <strong>#{i} &mdash; {ptype}</strong> &middot; {player}
          </div>
          <p style="font-size:0.78rem;color:#888;margin:0 0 12px">Source: {source}</p>

          <p style="font-weight:bold;color:#1da1f2;margin:0 0 4px">X Post ({len(d.get("x_post",""))} chars)</p>
          <div style="background:#f8f9fa;border-left:3px solid #1da1f2;padding:12px;border-radius:4px;white-space:pre-wrap;font-size:0.88rem;margin-bottom:16px">{x_post}</div>

          <p style="font-weight:bold;color:#e1306c;margin:0 0 4px">Instagram Caption</p>
          <div style="background:#f8f9fa;border-left:3px solid #e1306c;padding:12px;border-radius:4px;white-space:pre-wrap;font-size:0.88rem">{ig_cap}</div>
          {article_html}
        </div>
        """)

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;background:#f5f5f5">
  <div style="background:#782F40;padding:20px;border-radius:8px 8px 0 0;text-align:center">
    <h1 style="color:#CEB888;margin:0;font-size:1.4rem">&#9918; Noles in the Show</h1>
    <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:0.9rem">Daily Post Drafts &mdash; {date_str}</p>
  </div>
  <div style="background:white;padding:24px;border-radius:0 0 8px 8px;border:1px solid #ddd">
    <p style="color:#555;font-size:0.9rem;margin-top:0">
      {len(drafts)} draft(s) ready for review. Copy each into X and Instagram &mdash; edit as needed before posting.
    </p>
    {"".join(draft_blocks)}
    <div style="text-align:center;padding-top:12px;border-top:1px solid #eee;margin-top:8px">
      <a href="https://nolesintheshow.com" style="color:#782F40;font-weight:bold">nolesintheshow.com</a>
      &nbsp;&middot;&nbsp;
      <a href="https://x.com/NolesInTheShow" style="color:#1da1f2">@NolesInTheShow</a>
      &nbsp;&middot;&nbsp;
      <a href="https://www.instagram.com/nolesintheshow/" style="color:#e1306c">@nolesintheshow</a>
    </div>
  </div>
</body>
</html>"""

    try:
        with open(EMAIL_BODY_PATH, "w") as f:
            f.write(html)
        print(f"  ✓ Email body written to {EMAIL_BODY_PATH.name}")
        return True
    except Exception as e:
        print(f"  ! Failed to write email body: {e}")
        return False


# ── Teams message ─────────────────────────────────────────────────────────────
def send_to_teams(drafts: list[dict]) -> bool:
    """Send a rich adaptive card to Teams with all today's drafts."""
    if not TEAMS_WEBHOOK:
        print("  ! TEAMS_WEBHOOK_URL not set — skipping Teams notification")
        return False

    today_str = datetime.now().strftime("%A, %B %d, %Y")

    # Build sections for each draft
    sections = []
    for i, d in enumerate(drafts, 1):
        ptype = d["type"].replace("_", " ").title()
        player = d["player"]
        source = d.get("source", "")
        x_post = d["x_post"]
        ig_cap = d["ig_caption"]
        article = d.get("article_link", "")

        # Truncate long IG caption for card display
        ig_preview = ig_cap if len(ig_cap) <= 500 else ig_cap[:497] + "..."

        section = {
            "activityTitle": f"**#{i} — {ptype}** · {player}",
            "activitySubtitle": f"Source: {source}",
            "facts": [
                {
                    "name": "🐦 X Post (copy → post at x.com/NolesInTheShow):",
                    "value": x_post,
                },
                {
                    "name": "📸 Instagram Caption (copy → post at @nolesintheshow):",
                    "value": ig_preview,
                },
            ],
        }
        if article:
            section["facts"].append({"name": "🔗 Article:", "value": article})

        sections.append(section)

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "782F40",
        "summary": f"Noles in the Show — Daily Post Drafts ({today_str})",
        "sections": [
            {
                "activityTitle": f"📣 Daily Post Drafts — {today_str}",
                "activitySubtitle": (
                    f"{len(drafts)} draft(s) ready. Copy each into X and Instagram. "
                    f"Review before posting — stats are pulled live each morning."
                ),
                "activityImage": "https://nolesintheshow.com/logo.png",
                "markdown": True,
            },
            *sections,
            {
                "activityTitle": "📊 Live Stats",
                "text": "[View full roster at nolesintheshow.com](https://nolesintheshow.com)",
            },
        ],
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "Open nolesintheshow.com",
                "targets": [{"os": "default", "uri": "https://nolesintheshow.com"}],
            },
            {
                "@type": "OpenUri",
                "name": "Post to X",
                "targets": [{"os": "default", "uri": "https://x.com/NolesInTheShow"}],
            },
            {
                "@type": "OpenUri",
                "name": "Post to Instagram",
                "targets": [{"os": "default", "uri": "https://www.instagram.com/nolesintheshow/"}],
            },
        ],
    }

    try:
        r = requests.post(TEAMS_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
        print(f"  ✓ Teams message sent ({len(drafts)} drafts)")
        return True
    except Exception as e:
        print(f"  ! Teams webhook error: {e}")
        return False

# ── Save drafts to JSON log ───────────────────────────────────────────────────
def log_drafts(drafts: list[dict]):
    """Append today's drafts to the rolling JSON log in the repo."""
    history = load_drafts()
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "drafts": drafts,
    }
    # Keep last 30 days
    history = [h for h in history if h.get("date", "") >= (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")]
    history.append(entry)
    save_drafts(history)
    print(f"  ✓ Drafts logged to {DRAFTS_PATH.name}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  Noles in the Show — Social Post Drafter ({SEASON})")
    print(f"{'='*55}\n")

    print("→ Loading player data from stats pipeline...")
    player_data = get_player_data()
    if not player_data:
        print("  ! No player data available. Exiting.")
        sys.exit(1)
    print(f"  ✓ {len(player_data)} players loaded\n")

    player_names = [p["name"] for p in player_data]

    print("→ Scanning news feeds for player mentions...")
    mentions = scrape_news_mentions(player_names)
    print(f"  ✓ {len(mentions)} relevant articles found\n")

    print("→ Building post drafts...")
    drafts = build_drafts(player_data, mentions)
    print(f"  ✓ {len(drafts)} draft(s) generated\n")

    # Print drafts to console for CI logs
    for i, d in enumerate(drafts, 1):
        print(f"  ── Draft #{i}: {d['type'].upper()} — {d['player']} ──")
        print(f"  Source: {d['source']}")
        print(f"\n  X POST:\n{textwrap.indent(d['x_post'], '    ')}")
        print(f"\n  IG CAPTION:\n{textwrap.indent(d['ig_caption'][:400], '    ')}{'...' if len(d['ig_caption']) > 400 else ''}")
        print()

    print("→ Logging drafts to social_drafts.json...")
    log_drafts(drafts)

    print("→ Writing HTML email body...")
    write_email_body(drafts)

    print("→ Sending to Teams (if webhook configured)...")
    send_to_teams(drafts)

    print(f"\n{'='*55}")
    print(f"  ✅ Done! {len(drafts)} post draft(s) ready for review.")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()
