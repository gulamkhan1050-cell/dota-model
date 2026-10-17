"""
build.py - build the Dota 2 app pages from model.json (or the last model.js) and release.json.
Run:   python build.py
Makes: app.html (standalone, model inside), app.shell.html (the page published weekly, opened online),
       loader.html and apk-upload.zip (index.html for the APK: opens the newest page, offline falls back to its own copy)
"""
import json, os, sys, zipfile, base64
here = os.path.dirname(os.path.abspath(__file__))
tpl = open(os.path.join(here, "app.template.html"), encoding="utf-8").read()
mp = os.path.join(here, "model.json")
js = os.path.join(here, "model.js")
if os.path.exists(mp):
    model = json.load(open(mp, encoding="utf-8"))
elif os.path.exists(js):  # no fresh training run: reuse the last published model
    txt = open(js, encoding="utf-8").read().strip()
    model = json.loads(txt[txt.index("=") + 1:].rstrip(";"))
else:
    sys.exit("model.json not found - run trainer.py first")
rp = os.path.join(here, "release.json")
release = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {"version": "1.0", "date": "", "history": []}
blob = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")
rel = json.dumps(release, separators=(",", ":")).replace("</", "<\\/")
# where the phone app downloads the daily model and the weekly page (jsDelivr serves them from this public repo)
url = os.environ.get("MODEL_URL") or "https://cdn.jsdelivr.net/gh/gulamkhan1050-cell/dota-model@main/model.js"
shell_url = "https://cdn.jsdelivr.net/gh/gulamkhan1050-cell/dota-model@main/app.shell.html"
if len(sys.argv) > 1: url = sys.argv[1]
page = tpl.replace("__MODEL_URL__", url).replace("__RELEASE_JSON__", rel)
out = page.replace("__MODEL_JSON__", blob)                       # standalone page, model inside (as before)
shell = page.replace("__MODEL_JSON__", "window.__APK_BAKED||null")  # the online page: the APK hands it its own model copy for offline use
loader = open(os.path.join(here, "loader.template.html"), encoding="utf-8").read() \
    .replace("__SHELL_URL__", shell_url).replace("__MODEL_JSON__", blob) \
    .replace("__SHELL_B64__", base64.b64encode(shell.encode("utf-8")).decode("ascii")).replace("__VERSION__", release["version"])
open(os.path.join(here, "model.js"), "w", encoding="utf-8").write("window.MODEL_REMOTE=" + blob + ";")
open(os.path.join(here, "app.html"), "w", encoding="utf-8").write(out)
open(os.path.join(here, "app.shell.html"), "w", encoding="utf-8").write(shell)
open(os.path.join(here, "loader.html"), "w", encoding="utf-8").write(loader)
with zipfile.ZipFile(os.path.join(here, "apk-upload.zip"), "w", zipfile.ZIP_DEFLATED) as z: z.writestr("index.html", loader)
print(f"v{release['version']}: wrote app.html, app.shell.html ({len(shell)//1024} KB, the page the app opens online), loader.html and apk-upload.zip "
      f"(index.html for the APK: opens the newest page, falls back to its own copy offline)")
print(f"model: {len(model['teams'])} teams, {len(model.get('upcoming',[]))} upcoming, {len(model['players'])} players, as of {model['as_of']}")
