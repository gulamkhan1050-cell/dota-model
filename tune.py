"""
tune.py - the retune every 3 days, run by the release every 3 days before the trainer.

Re-checks the rating and model settings against the most recent results: for every combination of team K,
player K and ridge strength it rebuilds the ratings, fits the model on the older 80% of games and scores it
on the newest 20% (log-loss: how well-calibrated the chances were, not just right/wrong). The per-map cap is
then picked the same way. The winner is written to tune.json, which the trainer reads on every run.
Settings only change when they are clearly better (MIN_GAIN), so one noisy week does not swing the model.

Run:   python tune.py            (uses the trainer's cached data; PANDASCORE_TOKEN as for the trainer)
       python tune.py --demo     (synthetic data, offline check)
"""
import argparse, importlib, itertools, json, os, sys, time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = importlib.import_module(os.environ.get("TRAINER", "trainer"))

GRID = {"K_TEAM": [24.0, 32.0, 40.0], "K_PLAYER": [14.0, 20.0, 26.0], "L2": [0.3, 1.0, 3.0]}
CAPS = [0.80, 0.85, 0.90, 0.95]
MIN_GAIN = 0.001          # log-loss improvement needed before the settings change
WEEK = 7 * 86400


def logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def accuracy(p, y):
    return round(float(((p > 0.5) == (y == 1)).mean()), 4) if len(y) else None


def holdout(matches, rosters, market, k_team, k_player):
    """Ratings rebuilt with these K values: (train X, train y, holdout X, holdout y, holdout times)."""
    T.apply_tune({"K_TEAM": k_team, "K_PLAYER": k_player})
    _, X, y, ts, _, _ = T.build_features(matches, rosters, market)
    cut = int(len(X) * (1 - T.TEST_FRACTION))
    return X[:cut], y[:cut], X[cut:], y[cut:], np.array(ts[cut:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12); ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-backup", action="store_true"); ap.add_argument("--odds", default="odds_log.json")
    ap.add_argument("--out", default=T.TUNE_FILE); args = ap.parse_args()
    current = {"K_TEAM": T.K_TEAM, "K_PLAYER": T.K_PLAYER, "L2": T.L2, "CAP": T.CAP}
    market = [] if args.demo else T.load_market(args.odds, "cs2" if "cs" in T.__name__ else "dota2")
    matches, rosters, _ = T.load_data(args)
    if len(matches) < 300:
        sys.exit(f"only {len(matches)} matches - not enough to retune")
    now = max(m["ts"] for m in matches)

    results = []
    for kt, kp in itertools.product(GRID["K_TEAM"], GRID["K_PLAYER"]):
        Xtr, ytr, Xte, yte, tte = holdout(matches, rosters, market, kt, kp)
        for l2 in GRID["L2"]:
            p = T.predict(T.train_logistic(Xtr, ytr, l2), Xte)
            results.append({"K_TEAM": kt, "K_PLAYER": kp, "L2": l2, "logloss": logloss(p, yte), "p": p, "y": yte, "t": tte})
            print(f"  K_TEAM {kt:>4.0f}  K_PLAYER {kp:>4.0f}  L2 {l2:<4}  log-loss {results[-1]['logloss']:.4f}")
    same = lambda r, s: r["K_TEAM"] == s["K_TEAM"] and r["K_PLAYER"] == s["K_PLAYER"] and r["L2"] == s["L2"]
    cur = next((r for r in results if same(r, current)), None)
    if cur is None:  # the current settings are off the grid: score them too
        Xtr, ytr, Xte, yte, tte = holdout(matches, rosters, market, current["K_TEAM"], current["K_PLAYER"])
        p = T.predict(T.train_logistic(Xtr, ytr, current["L2"]), Xte)
        cur = {**{k: current[k] for k in ("K_TEAM", "K_PLAYER", "L2")}, "logloss": logloss(p, yte), "p": p, "y": yte, "t": tte}
    best = min(results, key=lambda r: r["logloss"])
    if cur["logloss"] - best["logloss"] < MIN_GAIN:
        best = cur

    def capped(r, c):
        return np.clip(r["p"], 1 - c, c)
    cap = min(CAPS, key=lambda c: logloss(capped(best, c), best["y"]))
    if logloss(capped(best, current["CAP"]), best["y"]) - logloss(capped(best, cap), best["y"]) < MIN_GAIN:
        cap = current["CAP"]

    week = lambda r: r["t"] >= now - WEEK
    chosen = {"K_TEAM": best["K_TEAM"], "K_PLAYER": best["K_PLAYER"], "L2": best["L2"], "CAP": cap}
    out = {"as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "settings": chosen, "previous": current,
           "changed": chosen != current, "tested": len(results),
           "holdout": {"games": int(len(best["y"])), "logloss_previous": round(cur["logloss"], 4),
                       "logloss_chosen": round(logloss(capped(best, cap), best["y"]), 4),
                       "accuracy_previous": accuracy(cur["p"], cur["y"]), "accuracy_chosen": accuracy(best["p"], best["y"])},
           "last_week": {"games": int(week(best).sum()),
                         "accuracy_previous": accuracy(cur["p"][week(cur)], cur["y"][week(cur)]),
                         "accuracy_chosen": accuracy(best["p"][week(best)], best["y"][week(best)])}}
    json.dump(out, open(args.out, "w", encoding="utf-8"), indent=1)
    print(f"\n{'changed' if out['changed'] else 'kept'}: {chosen}  (was {current})")
    print(f"holdout log-loss {out['holdout']['logloss_previous']} -> {out['holdout']['logloss_chosen']}; "
          f"last 7 days: {out['last_week']['games']} games")


if __name__ == "__main__":
    main()
