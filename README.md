# dota-model

Dota 2 pro-match predictor: `trainer.py` learns from PandaScore results, `build.py` bakes the model into a phone page, and the Android app shows it.

## Phone app

**Download:** `dota2-model.apk` in this repo (Android 7.0+). Builds made by GitHub (same signing key, so they update the installed app) are also on the [apk release](https://github.com/gulamkhan1050-cell/dota-model/releases/tag/apk). Allow "install unknown apps" for your browser, then open it.

- The page and the model are **inside the app**: it opens offline.
- When online it downloads the newest `model.js` (the **update model** workflow retrains twice a day and publishes it through jsDelivr), so new data needs no new APK. The top line says "updated over the internet" when it did.
- Live Polymarket prices load straight from Polymarket's public API.

## How it updates

| Workflow | When | Does |
|---|---|---|
| `update model` | 04:30 and 10:30 UTC daily, or by hand | `trainer.py` → `build.py` → commits `model.js` → refreshes the jsDelivr copy |
| `build apk` | when `app.template.html`, `build.py` or `android/` change, or by hand | builds `dota2-model.apk` and puts it on the **apk** release |

Build the APK yourself: `python build.py`, then `android/build.sh` (the four tools it needs are listed at its top). `android/keystore.jks` is the app's signing key and is kept in this repo, so every build installs as an update over the old app.

## Versions and the weekly update

The app shows its version (**v1.1**, **v1.2**, ...) and a **What's new** list under the title.

| Workflow | When | Does |
|---|---|---|
| `weekly release` | every 3 days at 03:15 UTC (08:45 India), or by hand | `tune.py` re-checks team K, player K, ridge strength and the per-map cap on the newest results (writes `tune.json`, which `trainer.py` reads) → retrains → `release.py` writes the next version and its notes (`release.json`) → `build.py` → publishes `app.shell.html` |

- The APK's start page is `loader.html`: online it opens the published `app.shell.html` (so a new version needs no new APK), offline the last page it saw or the copy inside the APK.
- Each match card shows the **Esports Intel** grade (A / B, locked 1 minute before the start, won / lost after) from `https://esports-intel.onrender.com/api/grades`, and whether it agrees with this model.
- Settings change only when clearly better on the newest 20% of games (log-loss gain of 0.001 or more).
