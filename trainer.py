"""
trainer.py  -  Dota 2 pro-match predictor (run on your PC)

Data: PandaScore free plan (past matches, game winners and lengths, tournament rosters, tiers).
      Sign up at pandascore.co, copy your token, paste it below or set PANDASCORE_TOKEN.

Setup:   pip install requests numpy
Run:     python trainer.py                 # ~12 months
         python trainer.py --months 18
         python trainer.py --demo          # synthetic data, offline pipeline check

Raw data is cached in cache_dota/ so re-runs only download what is new.
"""

import argparse, json, math, os, random, sys, time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta

import numpy as np
import requests

# ------------------------------------------------------------------ config
TOKEN ="di0FFBr1_1GjEpPMJNxCkjdM2cHqPMkUQuVLBZUOITRfjsUK8pc"
BASE = 1500.0
K_TEAM = 32.0
K_PLAYER = 20.0
TIER_BASE = {1: 1500.0, 2: 1480.0, 3: 1400.0, 4: 1330.0, 5: 1280.0}   # entry rating by tier of a team's first event: regional pools start lower
TIER_K = {1: 1.3, 2: 1.15, 3: 1.0, 4: 0.85, 5: 0.7}                    # tier-1 results move ratings more than tier-C/D ones
SOS_N = 12                                                             # opponents remembered for strength-of-schedule
FORM_N = 10
H2H_N = 10
TEST_FRACTION = 0.2
OUT = "model.json"
CACHE_DIR = "cache_dota"
API = "https://api.pandascore.co"
PNAMES = {}                     # player id -> nickname (filled by fetch_rosters)
TIER_NUM = {"s": 1, "a": 2, "b": 3, "c": 4, "d": 5}


# ------------------------------------------------------------------ data
def ps_get(path, params=None, tries=5):
    if not TOKEN:
        sys.exit("No PandaScore token. Paste it into TOKEN at the top of trainer.py.")
    for i in range(tries):
        r = requests.get(f"{API}{path}", params=params,
                         headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}, timeout=60)
        if r.status_code == 429:
            wait = 60 * (i + 1); print(f"  rate limited, waiting {wait}s"); time.sleep(wait); continue
        if r.status_code in (401, 403):
            sys.exit(f"PandaScore said {r.status_code}: check your token (or this endpoint needs a paid plan).")
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up on {path}")


def fetch_past_matches(months):
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, "matches.json")
    have = {}
    if os.path.exists(f):
        for m in json.load(open(f, encoding="utf-8")):
            have[m["id"]] = m
        print(f"cache: {len(have)} matches")
    since = datetime.now(timezone.utc) - timedelta(days=30 * months)
    newest = max((m["begin_at"] for m in have.values()), default="")
    lo = max(since.isoformat(), (newest[:10] + "T00:00:00Z") if newest else "")
    hi = datetime.now(timezone.utc).isoformat()

    page, added = 1, 0
    while True:
        rows = ps_get("/dota2/matches/past", {
            "sort": "-begin_at", "per_page": 100, "page": page,
            "range[begin_at]": f"{lo},{hi}", "filter[status]": "finished",
        })
        if not rows:
            break
        for m in rows:
            if m.get("forfeit") or len(m.get("opponents") or []) != 2:
                continue
            if m["id"] not in have:
                added += 1
            have[m["id"]] = slim(m)
        print(f"  page {page}: {len(rows)} matches (total {len(have)})")
        if len(rows) < 100:
            break
        page += 1
        time.sleep(0.7)   # free plan: 1000 req/hour
    matches = [m for m in have.values() if m["begin_at"] >= since.isoformat()]
    matches.sort(key=lambda m: (m["begin_at"], m["id"]))
    json.dump(matches, open(f, "w", encoding="utf-8"))
    print(f"downloaded {added} new, using {len(matches)} matches since {since:%Y-%m-%d}")
    return matches


def slim(m):
    """Keep only what we need from a PandaScore match."""
    opps = [o["opponent"] for o in m["opponents"]]
    maps = []
    for g in m.get("games") or []:
        w = g.get("winner") or {}
        if g.get("finished") and w.get("id") and not g.get("forfeit"):
            maps.append({"pos": g.get("position", 0), "winner": w["id"], "length": int(g.get("length") or 0)})
    maps.sort(key=lambda g: g["pos"])
    t = m.get("tournament") or {}
    return {
        "id": m["id"], "begin_at": m["begin_at"], "ts": int(datetime.fromisoformat(m["begin_at"].replace("Z", "+00:00")).timestamp()),
        "team_a": opps[0]["id"], "team_b": opps[1]["id"],
        "name_a": opps[0].get("name") or "", "name_b": opps[1].get("name") or "",
        "acr_a": opps[0].get("acronym") or "", "acr_b": opps[1].get("acronym") or "",
        "tournament_id": m.get("tournament_id") or t.get("id"),
        "tier": TIER_NUM.get((t.get("tier") or "").lower(), 4),
        "league": ((m.get("league") or {}).get("name") or ""),
        "bo": m.get("number_of_games") or len(maps) or 1,
        "games": maps, "winner": m.get("winner_id"),
    }


OPENDOTA = "https://api.opendota.com/api"

def norm_team(x):
    x = (x or "").lower()
    for w in ("team", "esports", "esport", "gaming", "club", "the", "e-sports", "academy"):
        x = x.replace(w, "")
    return "".join(c for c in x if c.isalnum())


def fetch_opendota(months, name2id, known_ids):
    """Backup source. OpenDota is keyless and carries games PandaScore sometimes misses
    (and the reverse is also true), so we merge both. Teams are matched by name; matches
    whose teams we cannot identify are skipped rather than guessed."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    f = os.path.join(CACHE_DIR, "opendota.json")
    have = {str(m["id"]): m for m in json.load(open(f, encoding="utf-8"))} if os.path.exists(f) else {}
    since = time.time() - months * 30 * 86400
    newest = max((m["ts"] for m in have.values()), default=0)
    rows, less = [], None
    try:
        import urllib.request
        for _ in range(40):                                        # 100 matches a page, walk back until we reach what we have
            url = f"{OPENDOTA}/proMatches" + (f"?less_than_match_id={less}" if less else "")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "predictor/1.0"}), timeout=45) as r:
                page = json.loads(r.read().decode())
            if not page: break
            rows += page; less = min(p["match_id"] for p in page)
            oldest = min(p.get("start_time", 0) for p in page)
            if oldest < max(since, newest - 86400): break
            time.sleep(1.2)
    except Exception as e:
        print(f"  OpenDota backup unavailable ({e}); continuing with PandaScore only")
    added = 0
    for r in rows:
        if not r.get("radiant_name") or not r.get("dire_name") or r.get("radiant_win") is None: continue
        if (r.get("start_time") or 0) < since: continue
        have[str(r["match_id"])] = {"id": r["match_id"], "ts": int(r["start_time"]), "a": r["radiant_name"], "b": r["dire_name"],
                                    "a_won": bool(r["radiant_win"]), "length": int(r.get("duration") or 0), "league": r.get("league_name") or ""}
        added += 1
    json.dump(list(have.values()), open(f, "w", encoding="utf-8"))
    out, unmatched = [], 0
    for m in have.values():
        if m["ts"] < since: continue
        a, b = name2id.get(norm_team(m["a"])), name2id.get(norm_team(m["b"]))
        if not a or not b: unmatched += 1; continue
        out.append({"id": "od" + str(m["id"]), "ts": m["ts"], "team_a": a, "team_b": b,
                    "name_a": m["a"], "name_b": m["b"], "acr_a": "", "acr_b": "", "tournament_id": None,
                    "tier": 3, "league": m["league"], "bo": 1, "source": "opendota",
                    "games": [{"pos": 1, "winner": a if m["a_won"] else b, "length": m["length"]}],
                    "winner": a if m["a_won"] else b,
                    "begin_at": datetime.fromtimestamp(m["ts"], timezone.utc).isoformat()})
    print(f"OpenDota backup: {len(have)} cached ({added} new), {len(out)} usable, {unmatched} skipped (team not in PandaScore)")
    return out


def merge_sources(primary, extra):
    """Add games the primary source does not have. Same two teams within three hours = same match."""
    seen = {(min(m["team_a"], m["team_b"], key=str), max(m["team_a"], m["team_b"], key=str), m["ts"] // 10800) for m in primary}
    added = [m for m in extra
             if (min(m["team_a"], m["team_b"], key=str), max(m["team_a"], m["team_b"], key=str), m["ts"] // 10800) not in seen]
    out = primary + added
    out.sort(key=lambda m: (m["ts"], str(m["id"])))
    print(f"merged: {len(primary)} from PandaScore + {len(added)} only on OpenDota = {len(out)} matches")
    return out


def fetch_upcoming(days=7, back=1):
    """Matches from `back` days ago to `days` days ahead (PandaScore /dota2/matches), slimmed.
    Yesterday's are included so the app can show results and settle picks."""
    lo = (datetime.now(timezone.utc) - timedelta(days=back)).isoformat(timespec="seconds")
    hi = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    out, page = [], 1
    while True:
        rows = ps_get("/dota2/matches", {"sort": "begin_at", "per_page": 100, "page": page, "range[begin_at]": f"{lo},{hi}"}) or []
        for m in rows:
            opps = [o.get("opponent") or {} for o in (m.get("opponents") or [])]
            if len(opps) != 2 or not opps[0].get("id") or not opps[1].get("id") or not m.get("begin_at"): continue
            t = m.get("tournament") or {}; sr = m.get("serie") or {}; lg = m.get("league") or {}
            out.append({"id": m["id"], "ts": int(datetime.fromisoformat(m["begin_at"].replace("Z", "+00:00")).timestamp()), "team_a": opps[0]["id"], "team_b": opps[1]["id"],
                        "name_a": opps[0].get("name") or "", "name_b": opps[1].get("name") or "", "acr_a": opps[0].get("acronym") or "", "acr_b": opps[1].get("acronym") or "",
                        "tournament_id": m.get("tournament_id") or t.get("id"), "tier": TIER_NUM.get((t.get("tier") or "").lower(), 4), "bo": m.get("number_of_games") or 3,
                        "status": m.get("status") or "", "winner": (m.get("winner") or {}).get("id"),
                        "score": [ (r.get("score") if isinstance(r, dict) else None) for r in (m.get("results") or []) ][:2],
                        "event": " ".join(x for x in [lg.get("name") or "", sr.get("full_name") or sr.get("name") or "", t.get("name") or ""] if x).strip(), "name": m.get("name") or ""})
        if len(rows) < 100: break
        page += 1; time.sleep(0.7)
    print(f"schedule: {len(out)} matches from {back}d ago to {days}d ahead")
    return out


def fetch_rosters(tournament_ids):
    """tournament_id -> {team_id: [player_ids]} via GET /tournaments/{id}/rosters (free plan)."""
    f = os.path.join(CACHE_DIR, "rosters.json"); pf = os.path.join(CACHE_DIR, "players.json")
    have = json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}
    if os.path.exists(pf): PNAMES.update({int(k): v for k, v in json.load(open(pf, encoding="utf-8")).items()})
    if have and not PNAMES: print("  roster cache has no player names yet - refetching once so the app can show names"); have = {}
    todo = [t for t in tournament_ids if t and str(t) not in have]
    print(f"rosters: {len(have)} cached, {len(todo)} to fetch")
    for i, tid in enumerate(todo):
        try:
            res = ps_get(f"/tournaments/{tid}/rosters")
            rosters = res.get("rosters") if isinstance(res, dict) else res
            have[str(tid)] = {str(r["id"]): [p["id"] for p in (r.get("players") or [])]
                              for r in (rosters or []) if r.get("id")}
            for r in (rosters or []):
                for p in (r.get("players") or []):
                    if p.get("id"): PNAMES[p["id"]] = p.get("name") or p.get("slug") or str(p["id"])
        except SystemExit:
            raise
        except Exception as e:
            print(f"  roster {tid} failed: {e}")
            have[str(tid)] = {}
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(todo)}")
            json.dump(have, open(f, "w", encoding="utf-8")); json.dump({str(k): v for k, v in PNAMES.items()}, open(pf, "w", encoding="utf-8"))
        time.sleep(0.7)
    json.dump(have, open(f, "w", encoding="utf-8")); json.dump({str(k): v for k, v in PNAMES.items()}, open(pf, "w", encoding="utf-8"))
    return have


def demo_matches(n=2500, teams=40):
    random.seed(2)
    strength = {t: random.gauss(0, 120) for t in range(1, teams + 1)}
    rosters = {t: [t * 100 + i for i in range(5)] for t in strength}
    now = int(time.time()); out = []
    for i in range(n):
        a, b = random.sample(list(strength), 2)
        p = 1 / (1 + 10 ** (-(strength[a] - strength[b]) / 400))
        bo = random.choice([1, 3, 3, 3, 5]); wins = [0, 0]; maps = []
        while max(wins) < (bo + 1) // 2:
            w = a if random.random() < p else b; lose = random.randint(3, 11) if random.random() < 0.85 else random.randint(12, 13)
            wins[0 if w == a else 1] += 1; maps.append({"pos": len(maps) + 1, "winner": w, "length": random.randint(1400, 3000)})
        ts = now - (n - i) * 3600 * 3
        out.append({"id": i, "begin_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(), "ts": ts,
                    "team_a": a, "team_b": b, "name_a": f"Team {a}", "name_b": f"Team {b}", "acr_a": f"T{a}", "acr_b": f"T{b}",
                    "tournament_id": 1 + i // 200, "tier": random.choice([1, 2, 2, 3, 4]), "league": "Demo", "bo": bo,
                    "games": maps, "winner": a if wins[0] > wins[1] else b})
    ros = {str(t): {str(k): v for k, v in rosters.items()} for t in range(1, 1 + n // 200 + 1)}
    for t, pl in rosters.items():
        for k, p in enumerate(pl): PNAMES[p] = f"p{t}_{k+1}"
    PNAMES[99999] = "standin"
    up = []
    for i in range(12):
        a, b = random.sample(list(strength), 2); ts = now + 3600 * (6 + i * 5) - (20 * 3600 if i < 3 else 0)
        up.append({"status": "not_started", "winner": None, "score": [], "id": 900000 + i, "ts": ts, "team_a": a, "team_b": b, "name_a": f"Team {a}", "name_b": f"Team {b}", "acr_a": f"T{a}", "acr_b": f"T{b}", "tournament_id": 1 + n // 200,
                   "tier": random.choice([1, 2, 3]), "bo": random.choice([3, 3, 5]), "event": "Demo Masters - Playoffs", "name": f"Team {a} vs Team {b}"})
    if random.random() < 2: ros[str(1 + n // 200)][str(up[0]["team_a"])] = rosters[up[0]["team_a"]][:4] + [99999]     # a stand-in, to show the roster-change warning
    return out, ros, up


# ------------------------------------------------------------------ ratings & features
def expected(ra, rb): return 1 / (1 + 10 ** (-(ra - rb) / 400))


class State:
    def __init__(self):
        self.team_elo = defaultdict(lambda: BASE); self.player_elo = defaultdict(lambda: BASE)
        self.form = defaultdict(lambda: deque(maxlen=FORM_N)); self.last_ts = {}
        self.roster = {}; self.h2h = defaultdict(lambda: deque(maxlen=H2H_N))
        self.names = {}; self.acr = {}; self.games = defaultdict(int)
        self.lineups = defaultdict(dict); self.pgames = defaultdict(int); self.plast = {}; self.pteam = {}
        self.entry = {}; self.opp_elo = defaultdict(lambda: deque(maxlen=SOS_N)); self.tiers = defaultdict(lambda: deque(maxlen=SOS_N))

    def enter(self, t, tier, ts):
        """first sighting: start at the tier's base rating. Long idle: regress a fifth of the way back to it."""
        if t not in self.entry:
            self.entry[t] = TIER_BASE.get(tier, 1400.0); self.team_elo[t] = self.entry[t]
        elif ts - self.last_ts.get(t, ts) > 60 * 86400:
            self.team_elo[t] += 0.2 * (self.entry[t] - self.team_elo[t])
    def enter_players(self, players, tier):
        for p in players or []:
            if p not in self.player_elo: self.player_elo[p] = TIER_BASE.get(tier, 1400.0)
    def sos(self, t): o = self.opp_elo[t]; return float(np.mean(o)) if o else self.team_elo[t]
    def tier_avg(self, t): o = self.tiers[t]; return float(np.mean(o)) if o else 3.0

    def team_feats(self, t, players, now):
        r = self.roster.get(t)
        stab = (len(set(players) & set(r)) / max(len(r), 1)) if (r and players) else 0.5
        f = self.form[t]; form = (sum(f) / len(f)) if f else 0.5
        pe = [self.player_elo[p] for p in players] if players else [self.team_elo[t]]
        rest = min((now - self.last_ts.get(t, now - 14 * 86400)) / 86400, 14)
        return self.team_elo[t], float(np.mean(pe)), form, stab, rest, self.games[t]

    def features(self, a, b, pa_, pb_, ts, tier):
        self.enter(a, tier, ts); self.enter(b, tier, ts); self.enter_players(pa_, tier); self.enter_players(pb_, tier)
        ea, pa, fa, sa, ra, ga = self.team_feats(a, pa_, ts)
        eb, pb, fb, sb, rb, gb = self.team_feats(b, pb_, ts)
        key = (min(a, b), max(a, b)); h = self.h2h[key]
        if h:
            wa = sum(h) if a == key[0] else len(h) - sum(h); h2h = (wa - (len(h) - wa)) / len(h)
        else:
            h2h = 0.0
        return [(ea - eb) / 100, (pa - pb) / 100, fa - fb, sa - sb, h2h, (ra - rb) / 7,
                math.log1p(ga) - math.log1p(gb), 1.0 if tier == 1 else 0.0,
                (self.sos(a) - self.sos(b)) / 100, self.tier_avg(b) - self.tier_avg(a), (self.sos(a) - self.sos(b)) / 100 * ((ea - eb) / 100)]

    def update_map(self, a, b, pa_, pb_, a_won, ts, tier=3, length=0):
        win = 1.0 if a_won else 0.0
        e = expected(self.team_elo[a], self.team_elo[b])
        mov = 1.0
        if length:                                                                  # a 24-minute stomp says more than a 45-minute grind
            mov = 1.35 if length < 1500 else (1.15 if length < 1980 else (1.0 if length < 2700 else (0.85 if length < 3300 else 0.75)))
        kt = TIER_K.get(tier, 1.0) * mov
        ka = K_TEAM * kt * (1.5 if self.games[a] < 10 else 1.0); kb = K_TEAM * kt * (1.5 if self.games[b] < 10 else 1.0)
        ea0, eb0 = self.team_elo[a], self.team_elo[b]
        self.team_elo[a] += ka * (win - e); self.team_elo[b] += kb * ((1 - win) - (1 - e))
        self.opp_elo[a].append(eb0); self.opp_elo[b].append(ea0); self.tiers[a].append(tier); self.tiers[b].append(tier)
        if pa_ and pb_:
            ep = expected(np.mean([self.player_elo[p] for p in pa_]), np.mean([self.player_elo[p] for p in pb_]))
            for p in pa_: self.player_elo[p] += K_PLAYER * kt * (win - ep)
            for p in pb_: self.player_elo[p] += K_PLAYER * kt * ((1 - win) - (1 - ep))
        for t, pl in ((a, pa_), (b, pb_)):
            if pl:
                key = ",".join(str(x) for x in sorted(pl)); L = self.lineups[t].get(key) or {"games": 0, "first": ts, "last": ts}
                L["games"] += 1; L["last"] = ts; self.lineups[t][key] = L
                for x in pl: self.pgames[x] += 1; self.plast[x] = ts; self.pteam[x] = t
        self.form[a].append(win); self.form[b].append(1 - win)
        self.last_ts[a] = self.last_ts[b] = ts
        key = (min(a, b), max(a, b)); self.h2h[key].append(win if a == key[0] else 1 - win)
        self.games[a] += 1; self.games[b] += 1

    def after_match(self, m, pa_, pb_):
        if pa_: self.roster[m["team_a"]] = list(pa_)
        if pb_: self.roster[m["team_b"]] = list(pb_)
        for k, nm, ac in (("team_a", "name_a", "acr_a"), ("team_b", "name_b", "acr_b")):
            if m[nm]: self.names[m[k]] = m[nm]
            if m[ac]: self.acr[m[k]] = m[ac]


FEATURE_NAMES = ["team_elo_diff", "player_elo_diff", "form_diff", "roster_stability_diff",
                 "head_to_head", "rest_diff", "experience_diff", "tier1", "schedule_strength_diff", "tier_played_diff", "elo_x_schedule"]
NM = len(FEATURE_NAMES)          # the base model never sees the market; it is blended in afterwards (stack)
# ------------------------------------------------------------------ market feature (from odds_logger.py)
def _mnorm(s):
    import re as _re
    s = _re.sub(r"\b(team|esports?|e-sports|gaming|club|the)\b", " ", str(s or "").lower())
    return _re.sub(r"[^a-z0-9]", "", s)

def _same(a, b):
    a, b = _mnorm(a), _mnorm(b)
    if not a or not b: return False
    return a == b or a in b or b in a or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4])

def load_market(path, game):
    """Returns list of (ts, home, away, p_home_vigfree_opening) for one game from odds_log.json."""
    import json as _json, os as _os
    from datetime import datetime as _dt, timezone as _tz
    if not path or not _os.path.exists(path): return []
    out = []
    for rec in _json.load(open(path, encoding="utf-8")).values():
        if rec.get("game") != game or not rec.get("first", {}).get("ml"): continue
        books = list(rec["first"]["ml"].values())
        h, a = books[0]
        if h <= 1 or a <= 1: continue
        qh, qa = 1 / h, 1 / a; p = qh / (qh + qa)
        try: ts = int(_dt.fromisoformat(rec["date"].replace("Z", "+00:00")).timestamp())
        except Exception: continue
        out.append((ts, rec.get("home", ""), rec.get("away", ""), p))
    print(f"market: {len(out)} logged {game} events with opening moneylines")
    return out

def market_feats(market, ts, name_a, name_b, window_h=8):
    """[logit of opening P(team A), has_market]. Picks the CLOSEST logged game in time (teams often meet
    on consecutive days, so 'first match within 36h' picked the wrong game). Window default 8 hours."""
    import math as _m
    best, best_dt = None, None
    for (mts, h, a, p) in market:
        dt = abs(mts - ts)
        if dt > window_h * 3600: continue
        if _same(h, name_a) and _same(a, name_b): cand = p
        elif _same(h, name_b) and _same(a, name_a): cand = 1 - p
        else: continue
        if best_dt is None or dt < best_dt: best, best_dt = cand, dt
    if best is None: return [0.0, 0.0]
    best = min(max(best, 0.02), 0.98)
    return [_m.log(best / (1 - best)), 1.0]



# ------------------------------------------------------------------ model
def train_logistic(X, y, l2=1.0, iters=50, lr=None):
    """L2-regularised logistic regression solved by Newton's method (converges exactly, unlike gradient descent)."""
    mu, sd = X.mean(0), X.std(0) + 1e-9; Xs = (X - mu) / sd; n, d = Xs.shape
    Xb = np.hstack([Xs, np.ones((n, 1))]); w = np.zeros(d + 1); R = np.eye(d + 1) * l2; R[d, d] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Xb @ w))); g = Xb.T @ (p - y) + R @ w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + R
        step = np.linalg.solve(H, g); w -= step
        if np.abs(step).max() < 1e-8: break
    return {"w": w[:d], "b": float(w[d]), "mu": mu, "sd": sd}

def predict(model, X): return 1 / (1 + np.exp(-(((X - model["mu"]) / model["sd"]) @ model["w"] + model["b"])))

def evaluate(name, p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6); acc = ((p > 0.5) == (y == 1)).mean()
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean(); brier = ((p - y) ** 2).mean()
    print(f"  {name:<22} accuracy {acc*100:5.1f}%   log-loss {ll:.4f}   brier {brier:.4f}"); return acc, ll, brier

def calibration(p, y, bins=8):
    edges = np.linspace(0, 1, bins + 1); rows = []
    for i in range(bins):
        mask = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        if mask.sum() >= 10:
            rows.append({"bin": f"{edges[i]:.2f}-{edges[i+1]:.2f}", "n": int(mask.sum()),
                         "predicted": round(float(p[mask].mean()), 3), "actual": round(float(y[mask].mean()), 3)})
    return rows


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12); ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-backup", action="store_true", help="skip the OpenDota backup source");
    ap.add_argument("--active-days", type=int, default=400, help="a team stays searchable in the app if it played within this many days");
    ap.add_argument("--out", default=OUT); ap.add_argument("--odds", default="odds_log.json"); args = ap.parse_args()
    market = [] if args.demo else load_market(args.odds, "dota2")
    moves = []                                                   # live price movement from odds_log.json, for the app
    if not args.demo and os.path.exists(args.odds):
        try:
            raw = json.load(open(args.odds, encoding="utf-8"))
            for k, r in raw.items():
                if r.get("game") != "dota2" or r.get("closing") or not r.get("series"): continue
                ser = r["series"]
                moves.append({"a": r.get("home", ""), "b": r.get("away", ""), "date": r.get("date", ""),
                              "open": r.get("open_p", ser[0][1]), "now": r.get("last_p", ser[-1][1]),
                              "hi": r.get("hi_p"), "lo": r.get("lo_p"), "n": len(ser),
                              "spark": [x[1] for x in ser[-40:]], "vol": r.get("volume")})
        except Exception as e:
            print(f"  could not read price history from {args.odds}: {e}")
        print(f"price history: {len(moves)} open markets with a movement series")

    if args.demo:
        matches, rosters, upcoming = demo_matches()
    else:
        matches = fetch_past_matches(args.months)
        upcoming = fetch_upcoming(7, 1)
        rosters = fetch_rosters(sorted({m["tournament_id"] for m in matches + upcoming if m["tournament_id"]}))
        if not args.no_backup:
            name2id = {}
            for m in matches + upcoming:
                name2id.setdefault(norm_team(m["name_a"]), m["team_a"]); name2id.setdefault(norm_team(m["name_b"]), m["team_b"])
                if m.get("acr_a"): name2id.setdefault(norm_team(m["acr_a"]), m["team_a"])
                if m.get("acr_b"): name2id.setdefault(norm_team(m["acr_b"]), m["team_b"])
            matches = merge_sources(matches, fetch_opendota(args.months, name2id, set()))
    if len(matches) < 300:
        sys.exit(f"only {len(matches)} matches - not enough. Try --months 18 or check the token.")

    st = State(); X, y, ts, MK = [], [], [], []
    no_roster = 0
    for m in matches:
        ros = rosters.get(str(m["tournament_id"]), {})
        pa = ros.get(str(m["team_a"])) or []; pb = ros.get(str(m["team_b"])) or []
        if not pa or not pb: no_roster += 1
        # random orientation so the intercept doesn't learn "first listed team"
        flip = random.Random(m["id"]).random() < 0.5
        a, b, PA, PB = (m["team_b"], m["team_a"], pb, pa) if flip else (m["team_a"], m["team_b"], pa, pb)
        mk = market_feats(market, m["ts"], m["name_b"] if flip else m["name_a"], m["name_a"] if flip else m["name_b"], 24)
        for g in m["games"]:
            X.append(st.features(a, b, PA, PB, m["ts"], m["tier"])); MK.append(mk); y.append(1.0 if g["winner"] == a else 0.0); ts.append(m["ts"])
            st.update_map(a, b, PA, PB, g["winner"] == a, m["ts"], m["tier"], g.get("length"))
        st.after_match(m, pa, pb)
    X, y, MK = np.array(X), np.array(y), np.array(MK)
    nr = sum(1 for m in matches for g in m["games"] if g.get("length"))
    print(f"\n{len(matches)} matches, {len(X)} games ({nr} with a length for margin-of-victory Elo), {no_roster} matches without roster data")

    cut = int(len(X) * (1 - TEST_FRACTION)); Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    print(f"train {len(Xtr)} games, test {len(Xte)} (from {datetime.fromtimestamp(ts[cut]):%Y-%m-%d})\n\nholdout results:")
    evaluate("coin flip", np.full(len(yte), ytr.mean()), yte)
    evaluate("team elo only", 1 / (1 + 10 ** (-(Xte[:, 0] * 100) / 400)), yte)
    evaluate("player elo only", 1 / (1 + 10 ** (-(Xte[:, 1] * 100) / 400)), yte)
    model = train_logistic(Xtr, ytr); pte = predict(model, Xte)
    acc, ll, brier = evaluate("logistic (all feats)", pte, yte)
    MKtr, MKte = MK[:cut], MK[cut:]; hm = MKte[:, 1] > 0; stack = None
    if hm.sum() >= 30:
        pm = 1 / (1 + np.exp(-MKte[hm, 0]))
        evaluate("market line alone", pm, yte[hm]); evaluate("base model, same games", pte[hm], yte[hm])
        ok = MKtr[:, 1] > 0
        if ok.sum() >= 100:
            oo = np.zeros(len(ytr)); edges = np.linspace(0, len(ytr), 6).astype(int)     # out-of-fold base predictions, chronological blocks
            for i in range(5):
                te_ = np.zeros(len(ytr), bool); te_[edges[i]:edges[i + 1]] = True
                oo[te_] = predict(train_logistic(Xtr[~te_], ytr[~te_]), Xtr[te_])
            lg = lambda p: np.log(np.clip(p, 0.02, 0.98) / (1 - np.clip(p, 0.02, 0.98)))
            bl = train_logistic(np.c_[lg(oo[ok]), MKtr[ok, 0]], ytr[ok], l2=0.1)
            ps = predict(bl, np.c_[lg(pte[hm]), MKte[hm, 0]]); evaluate("STACKED model+market", ps, yte[hm])
            wz = bl["w"] / bl["sd"]; stack = {"a": round(float(wz[0]), 5), "b": round(float(wz[1]), 5), "c": round(float(bl["b"] - (bl["mu"] / bl["sd"] * bl["w"]).sum()), 5)}
            print(f"  stacked blend: logit(p) = {wz[0]:+.2f}*model + {wz[1]:+.2f}*market (+const), fitted on {int(ok.sum())} train games with a logged line")
        print(f"  ({int(hm.sum())} holdout games had a logged opening line)")
    else:
        print(f"  only {int(hm.sum())} holdout games had a logged line - no stack yet, keep odds_logger running")
    print("\nfeature weights:")
    for n, w in sorted(zip(FEATURE_NAMES, model["w"]), key=lambda t: -abs(t[1])): print(f"  {n:<24} {w:+.3f}")
    cal = calibration(pte, yte); print("\ncalibration:")
    for r in cal: print(f"  {r['bin']}  n={r['n']:<4} predicted {r['predicted']:.2f}  actual {r['actual']:.2f}")

    final = train_logistic(X, y)
    active_since = time.time() - args.active_days * 86400
    teams = {str(t): {"name": st.names.get(t, f"team {t}"), "acronym": st.acr.get(t, ""), "elo": round(st.team_elo[t], 1), "sos": round(st.sos(t), 1), "tier_avg": round(st.tier_avg(t), 2), "entry": st.entry.get(t, BASE),
                      "form": [int(v) for v in st.form[t]], "last_ts": st.last_ts.get(t, 0),
                      "roster": st.roster.get(t, []), "games": st.games[t]}
             for t in st.team_elo if st.last_ts.get(t, 0) >= active_since}
    used = {p for t in teams.values() for p in t["roster"]}
    players = {str(p): round(st.player_elo[p], 1) for p in st.player_elo if p in used}
    h2h = {f"{a}|{b}": [int(v) for v in d] for (a, b), d in st.h2h.items() if str(a) in teams and str(b) in teams}
    # player ranks among everyone who played in the last 120 days (1 = best)
    ranked = sorted([p for p in st.player_elo if st.plast.get(p, 0) >= active_since], key=lambda p: -st.player_elo[p]); rank = {p: i + 1 for i, p in enumerate(ranked)}
    def pinfo(p): return {"id": p, "name": PNAMES.get(p, f"player {p}"), "elo": round(st.player_elo[p], 1), "rank": rank.get(p), "of": len(ranked), "games": st.pgames.get(p, 0), "last_ts": st.plast.get(p, 0), "team": st.pteam.get(p)}
    def roster_report(t, expected, now):
        last = st.roster.get(t) or []; exp = list(expected or last)
        key = ",".join(str(x) for x in sorted(exp)); L = st.lineups[t].get(key) or {"games": 0, "first": None, "last": None}
        ins = [p for p in exp if p not in last]; outs = [p for p in last if p not in exp]
        best = max((v["games"] for v in st.lineups[t].values()), default=0)
        core = 0                                              # games where at least 4 of these 5 played together
        es = set(exp)
        for k2, v2 in st.lineups[t].items():
            ids = set(int(x) for x in k2.split(",") if x)
            if len(ids & es) >= 4: core += v2["games"]
        return {"expected": [pinfo(p) for p in exp], "in": [pinfo(p) for p in ins], "out": [pinfo(p) for p in outs], "known": bool(expected),
                "core4_matches": core, "source": "tournament roster" if expected else "last five seen playing",
                "games_together": L["games"], "days_together": round((now - L["first"]) / 86400, 1) if L["first"] else 0, "best_lineup_games": best,
                "new_to_team": [pinfo(p) for p in exp if st.pteam.get(p) not in (None, t)], "stand_in": any(st.pgames.get(p, 0) < 5 for p in exp)}
    now = int(time.time())
    for t in list(teams):                                    # a roster report for every team, so "Any two" has names and cohesion
        try:
            teams[t]["report"] = roster_report(int(t), None, now)
            for pl in teams[t]["report"]["expected"]: players[str(pl["id"])] = round(st.player_elo[pl["id"]], 1)
        except Exception:
            pass
    up_out = []
    for m in upcoming:
        for t, nm, ac in ((m["team_a"], m["name_a"], m["acr_a"]), (m["team_b"], m["name_b"], m["acr_b"])):
            st.names.setdefault(t, nm); st.acr.setdefault(t, ac)
        ros = rosters.get(str(m["tournament_id"]), {}); ra = ros.get(str(m["team_a"])) or []; rb = ros.get(str(m["team_b"])) or []
        for t in (m["team_a"], m["team_b"]):
            if str(t) not in teams: teams[str(t)] = {"name": st.names.get(t, f"team {t}"), "acronym": st.acr.get(t, ""), "elo": round(st.team_elo[t], 1), "sos": round(st.sos(t), 1), "tier_avg": round(st.tier_avg(t), 2),
                                                        "entry": st.entry.get(t, BASE), "form": [int(v) for v in st.form[t]], "last_ts": st.last_ts.get(t, 0), "roster": st.roster.get(t, []), "games": st.games[t]}
        up_out.append({"id": m["id"], "ts": m["ts"], "a": str(m["team_a"]), "b": str(m["team_b"]), "bo": m["bo"], "tier": m["tier"], "event": m["event"], "name": m["name"],
                       "status": m.get("status", ""), "winner": str(m["winner"]) if m.get("winner") else None, "score": m.get("score") or [],
                       "roster_a": roster_report(m["team_a"], ra, now), "roster_b": roster_report(m["team_b"], rb, now)})
    for u in up_out:
        for r in (u["roster_a"], u["roster_b"]):
            for p in r["expected"]: players[str(p["id"])] = round(st.player_elo[p["id"]], 1)
    recent = [{"id": m["id"], "ts": m["ts"], "a": str(m["team_a"]), "b": str(m["team_b"]), "winner": str(m["winner"]) if m.get("winner") else None,
               "score": [sum(1 for g in m["games"] if g["winner"] == m["team_a"]), sum(1 for g in m["games"] if g["winner"] == m["team_b"])]} for m in matches if m["ts"] >= now - 21 * 86400]
    out = {"game": "dota2", "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "matches_used": len(matches), "games_used": int(len(X)),
           "base": BASE, "form_n": FORM_N, "features": FEATURE_NAMES, "stack": stack, "version": 2,
           "model": {"w": [round(float(v), 6) for v in final["w"]], "b": round(float(final["b"]), 6),
                     "mu": [round(float(v), 6) for v in final["mu"]], "sd": [round(max(float(v), 1e-6), 6) for v in final["sd"]]},
           "holdout": {"n": int(len(yte)), "accuracy": round(float(acc), 4), "logloss": round(float(ll), 4), "brier": round(float(brier), 4), "calibration": cal},
           "teams": teams, "players": players, "player_ranks": {str(p): rank[p] for p in ranked}, "players_ranked": len(ranked), "player_names": {str(p): PNAMES.get(p, "") for p in players if PNAMES.get(p)}, "h2h": h2h, "upcoming": up_out, "recent": recent, "moves": moves}
    json.dump(out, open(args.out, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"\nwrote {args.out}: {len(teams)} active teams, {len(players)} players, {len(up_out)} upcoming matches, {os.path.getsize(args.out)//1024} KB")


if __name__ == "__main__":
    main()
