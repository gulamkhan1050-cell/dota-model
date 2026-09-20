"""
odds_logger_dota.py  -  Dota market prices, no API key needed (Polymarket public API).

Polymarket prices tier-1 Dota matches (Majors, IEM, BLAST, ESL, PGL...). It is a prediction market,
so the price IS the crowd's probability (no bookmaker margin). Opening = first traded price,
closing = last price before the match. Free, keyless, with full price history.

Run:   python odds_logger_dota.py --backfill 180    # once: history for the last 180 days
       python odds_logger_dota.py                   # one pass over upcoming matches
       python odds_logger_dota.py --loop            # keep running, one pass every 30 minutes
       python odds_logger_dota.py --loop --every 15 # ... or every 15 minutes

Optional: if you ever have an OddsPapi key, paste it into ODDSPAPI_KEY and the logger will ALSO
record Pinnacle/Bet365 lines (respecting the 250/month free quota).

Every pass appends the current price to a per-match series, so the apps can show how the market
moved: opened / now / high / low, and which way the money went since you first saw it.

Output: odds_log.json next to trainer.py (picked up automatically).
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
import requests

ODDSPAPI_KEY = ""            # optional
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
OUT = "odds_log.json"
UA = {"User-Agent": "DotaMatchLogger/1.0"}
DOTA_WORDS = re.compile(r"\bdota\b|the international|\bti1?\d\b|dreamleague|esl one|riyadh masters|epl", re.I)


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429: time.sleep(5 * (i + 1)); continue
            r.raise_for_status(); return r.json()
        except requests.RequestException as e:
            if i == tries - 1: raise
            time.sleep(2)


# ------------------------------------------------------------- find the Dota tag(s)
def cs2_tags():
    """Polymarket tag ids whose label/slug mention Dota 2 / Dota."""
    ids = set()
    try:
        for s in get(f"{GAMMA}/sports") or []:
            blob = json.dumps(s).lower()
            if DOTA_WORDS.search(blob):
                for k in ("tagId", "tag_id", "id"):
                    if s.get(k): ids.add(str(s[k]))
                for t in s.get("tags", []) or []:
                    if isinstance(t, dict) and t.get("id"): ids.add(str(t["id"]))
    except Exception as e: print("  /sports failed:", e)
    if not ids:
        try:
            off = 0
            while True:
                tags = get(f"{GAMMA}/tags", {"limit": 500, "offset": off}) or []
                for t in tags:
                    if DOTA_WORDS.search((t.get("label") or "") + " " + (t.get("slug") or "")): ids.add(str(t["id"]))
                if len(tags) < 500: break
                off += 500
        except Exception as e: print("  /tags failed:", e)
    return sorted(ids)


def events_for(tag_id, closed, since_iso):
    out, off = [], 0
    while True:
        params = {"tag_id": tag_id, "closed": "true" if closed else "false", "limit": 100, "offset": off, "order": "startDate", "ascending": "false"}
        if not closed: params["active"] = "true"
        rows = get(f"{GAMMA}/events", params) or []
        for e in rows:
            if closed and (e.get("startDate") or e.get("endDate") or "") < since_iso: return out
            out.append(e)
        if len(rows) < 100: break
        off += 100; time.sleep(0.3)
    return out


def match_markets(ev):
    """Yield (market, teamA, teamB, start_iso) for two-outcome team markets inside an event."""
    for m in ev.get("markets", []) or []:
        try:
            outs = json.loads(m.get("outcomes") or "[]"); toks = json.loads(m.get("clobTokenIds") or "[]"); prices = json.loads(m.get("outcomePrices") or "[]")
        except Exception: continue
        if len(outs) != 2 or len(toks) != 2: continue
        if set(o.lower() for o in outs) == {"yes", "no"}:            # "Will X win?" style: derive teams from the question
            q = m.get("question") or ""
            mm = re.search(r"will (.+?) (?:win|beat) (?:against |vs\.? )?(.+?)\??$", q, re.I)
            if not mm: continue
            outs = [mm.group(1).strip(), mm.group(2).strip()]
        start = m.get("gameStartTime") or m.get("startDate") or ev.get("startDate") or ""
        yield m, outs[0], outs[1], start, toks, prices


def price_history(token_id, fidelity=60):
    h = get(f"{CLOB}/prices-history", {"market": token_id, "interval": "max", "fidelity": fidelity}) or {}
    return sorted(h.get("history", []), key=lambda x: x.get("t", 0))


def record(log, key, ev, teamA, teamB, start, p_first, p_last, now, extra=None):
    """Store in the same shape the trainer reads: first.ml = {book: [oddsA, oddsB]} with fair (vig-free) decimal odds."""
    def ml(p): p = min(max(p, 0.02), 0.98); return {"polymarket": [round(1 / p, 3), round(1 / (1 - p), 3)]}
    rec = log.get(key) or {"game": "dota2", "league": ev.get("title") or ev.get("slug") or "", "home": teamA, "away": teamB, "date": start,
                            "first_seen": now, "first": {"ml": ml(p_first)}, "n": 0, "source": "polymarket"}
    rec.update({"last_seen": now, "last": {"ml": ml(p_last)}, "n": rec.get("n", 0) + 1})
    ser = rec.get("series") or []
    pl = round(float(p_last), 4)
    if not ser or abs(ser[-1][1] - pl) >= 0.005 or (now[:13] != str(ser[-1][2])[:13] if len(ser[-1]) > 2 else True):
        ser.append([int(time.time()), pl, now])                     # only store a point when the price actually moved
        rec["series"] = ser[-400:]
    ps = [x[1] for x in rec["series"]] or [pl]
    rec["open_p"] = rec["series"][0][1]; rec["last_p"] = pl; rec["hi_p"] = max(ps); rec["lo_p"] = min(ps); rec["move"] = round(pl - rec["series"][0][1], 4)
    if extra: rec.update(extra)
    log[key] = rec


def backfill(log, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tags = cs2_tags(); print(f"  Dota tag ids on Polymarket: {tags or 'none found'}")
    done = 0; seen = set()
    for tag in tags:
        for closed in (True, False):
            evs = events_for(tag, closed, since); print(f"  tag {tag} {'closed' if closed else 'open'}: {len(evs)} events")
            for ev in evs:
                for m, a, b, start, toks, prices in match_markets(ev):
                    key = "pm:" + str(m.get("id") or m.get("conditionId")); 
                    if key in seen or (key in log and log[key].get("closing")): continue
                    seen.add(key)
                    try:
                        hist = price_history(toks[0])
                    except Exception as e:
                        print("    history failed", a, "v", b, e); continue
                    if not hist: continue
                    # opening = first point; closing = last point before start (or last overall)
                    try: st = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
                    except Exception: st = None
                    before = [h for h in hist if st is None or h["t"] <= st] or hist
                    ser = [[int(h["t"]), round(float(h["p"]), 4), ""] for h in before]
                    record(log, key, ev, a, b, start, float(hist[0]["p"]), float(before[-1]["p"]), datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           {"closing": closed, "volume": m.get("volumeNum") or m.get("volume"), "series": ser[-400:]})
                    done += 1
                    if done % 25 == 0: print(f"    {done} matches"); json.dump(log, open(OUT, "w", encoding="utf-8"))
                    time.sleep(0.4)
    return done


def pass_upcoming(log):
    tags = cs2_tags(); done = 0; now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for tag in tags:
        for ev in events_for(tag, False, ""):
            for m, a, b, start, toks, prices in match_markets(ev):
                key = "pm:" + str(m.get("id") or m.get("conditionId"))
                try: p = float(prices[0])
                except Exception: continue
                if not (0 < p < 1): continue
                first = log.get(key, {}).get("first", {}).get("ml", {}).get("polymarket")
                p_first = 1 / first[0] if first else p
                record(log, key, ev, a, b, start, p_first, p, now, {"volume": m.get("volumeNum") or m.get("volume")}); done += 1
    return done


# ------------------------------------------------------------- optional OddsPapi (bookmaker lines)
def oddspapi_pass(log, backfill_days=0):
    if not ODDSPAPI_KEY: return 0
    base = "https://api.oddspapi.io/v4"; usage_f = "odds_usage.json"; month = datetime.now(timezone.utc).strftime("%Y-%m")
    u = json.load(open(usage_f)) if os.path.exists(usage_f) else {}
    if u.get("month") != month: u = {"month": month, "calls": 0}
    def og(path, **p):
        if u["calls"] >= 200: raise RuntimeError("OddsPapi monthly budget (200) reached")
        p["apiKey"] = ODDSPAPI_KEY; u["calls"] += 1; json.dump(u, open(usage_f, "w"))
        r = requests.get(f"{base}/{path}", params=p, timeout=30); r.raise_for_status(); return r.json()
    now = datetime.now(timezone.utc); n = 0
    try:
        a = now - timedelta(days=backfill_days) if backfill_days else now; b = now + timedelta(days=3)
        fx = []
        while a < b:
            c = min(a + timedelta(days=9), b)
            fx += [f for f in (og("fixtures", sportId=17, **{"from": a.strftime("%Y-%m-%d"), "to": c.strftime("%Y-%m-%d")}) or []) if re.search(r"major|iem|esl pro|blast|pgl|cologne|katowice", (f.get("tournamentName") or "").lower())]
            a = c + timedelta(days=1); time.sleep(1.5)
        for f in fx[:60]:
            key = "op:" + str(f["fixtureId"])
            if key in log: continue
            d = og("odds", fixtureId=f["fixtureId"]); bm = d.get("bookmakerOdds") or {}; got = {}
            for slug in ("pinnacle", "bet365"):
                m = ((bm.get(slug) or {}).get("markets") or {}).get("171")
                try: got[slug] = [float(m["outcomes"]["171"]["players"]["0"]["price"]), float(m["outcomes"]["172"]["players"]["0"]["price"])]
                except Exception: pass
            if got:
                log[key] = {"game": "dota2", "league": f.get("tournamentName", ""), "home": f.get("participant1Name"), "away": f.get("participant2Name"), "date": f.get("startTime"),
                            "first_seen": now.isoformat(timespec="seconds"), "first": {"ml": got}, "last_seen": now.isoformat(timespec="seconds"), "last": {"ml": got}, "n": 1, "source": "oddspapi"}
                n += 1
            time.sleep(1.5)
    except Exception as e: print("  oddspapi:", e)
    print(f"  oddspapi: {n} fixtures added, {u['calls']}/250 calls used this month")
    return n


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--loop", action="store_true"); ap.add_argument("--hours", type=float, default=0.5); ap.add_argument("--every", type=float, default=0, help="minutes between passes (overrides --hours)"); ap.add_argument("--backfill", type=int, default=0)
    args = ap.parse_args()
    while True:
        log = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
        print(f"{datetime.now():%Y-%m-%d %H:%M}  ({len(log)} matches logged so far)")
        if args.backfill:
            n = backfill(log, args.backfill); json.dump(log, open(OUT, "w", encoding="utf-8")); print(f"  polymarket backfill: {n} matches with price history")
            oddspapi_pass(log, args.backfill); json.dump(log, open(OUT, "w", encoding="utf-8")); print(f"  total {len(log)}"); return
        n = pass_upcoming(log); oddspapi_pass(log); json.dump(log, open(OUT, "w", encoding="utf-8"))
        print(f"  done: {n} upcoming matches priced. Total {len(log)}.")
        if not args.loop: break
        wait = args.every * 60 if args.every else args.hours * 3600
        print(f"  next pass in {wait/60:.0f} min"); time.sleep(wait)


if __name__ == "__main__":
    main()
