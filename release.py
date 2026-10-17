"""
release.py - the weekly version: 1.1, 1.2, ... (after 1.9 comes 2.0).

Writes release.json with the new number, today's date and what changed: the retune (tune.py), how the model did
over the last week, the data it now uses, and every fix committed since the last release (commit subjects,
leaving out the bots' daily "model ..." commits). build.py bakes it into the page ("What's new").
Run:   python release.py            (after tune.py and the trainer)
       python release.py --version 1.4 --note "..."   (by hand)
"""
import argparse, json, os, re, subprocess
from datetime import datetime, timezone

here = os.path.dirname(os.path.abspath(__file__))
RP = os.path.join(here, "release.json")
BOT = re.compile(r"^(\w+ )?model \d{4}-\d{2}-\d{2}$|^release v\d", re.I)


def bump(v):
    major, minor = (int(x) for x in v.split("."))
    return f"{major + 1}.0" if minor >= 9 else f"{major}.{minor + 1}"


def git(*a):
    try:
        return subprocess.run(["git", *a], cwd=here, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def fixes(since):
    log = git("log", "--format=%s", f"{since}..HEAD") if since else git("log", "--format=%s", "-30")
    return [s for s in log.splitlines() if s and not BOT.match(s)][:12]


def pct(x):
    return f"{x * 100:.1f}%" if x is not None else "-"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--version"); ap.add_argument("--note", action="append", default=[]); args = ap.parse_args()
    rel = json.load(open(RP, encoding="utf-8")) if os.path.exists(RP) else {"version": "1.0", "history": []}
    version = args.version or bump(rel["version"])
    notes = list(args.note)
    tp = os.path.join(here, "tune.json")
    if os.path.exists(tp):
        t = json.load(open(tp, encoding="utf-8")); s, p, h = t["settings"], t["previous"], t["holdout"]
        if t.get("changed"):
            ch = ", ".join(f"{k} {p[k]:g} -> {s[k]:g}" for k in s if s[k] != p.get(k))
            notes.append(f"Model retuned on the newest {h['games']} games: {ch} (log-loss {h['logloss_previous']} -> {h['logloss_chosen']}, "
                         f"accuracy {pct(h['accuracy_previous'])} -> {pct(h['accuracy_chosen'])})")
        else:
            notes.append(f"Model settings re-checked on the newest {h['games']} games ({t['tested']} combinations): the current ones are still the best")
        w = t.get("last_week") or {}
        if w.get("games"):
            notes.append(f"Last 7 days: {w['games']} maps/games, {pct(w.get('accuracy_chosen'))} picked right")
    mp = os.path.join(here, "model.json")
    if os.path.exists(mp):
        m = json.load(open(mp, encoding="utf-8"))
        notes.append(f"Data: {m['matches_used']:,} matches, {len(m['teams'])} teams, holdout accuracy {pct(m['holdout']['accuracy'])} per map")
    notes += [f"Fix: {f}" for f in fixes(rel.get("commit"))]
    entry = {"version": version, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "notes": notes}
    rel = {"version": version, "date": entry["date"], "commit": git("rev-parse", "HEAD"), "history": [entry] + rel.get("history", [])[:19]}
    json.dump(rel, open(RP, "w", encoding="utf-8"), indent=1)
    print(f"release v{version}:"); [print("  -", n) for n in notes]


if __name__ == "__main__":
    main()
