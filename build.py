"""
build.py - bake model.json into the Dota 2 app.
Run:   python build.py
Makes: app.html and apk-upload.zip (contains index.html)
"""
import json, os, sys, zipfile
here = os.path.dirname(os.path.abspath(__file__))
tpl = open(os.path.join(here, "app.template.html"), encoding="utf-8").read()
mp = os.path.join(here, "model.json")
if not os.path.exists(mp): sys.exit("model.json not found - run trainer.py first")
model = json.load(open(mp, encoding="utf-8"))
blob = json.dumps(model, separators=(",", ":")).replace("</", "<\\/")
url = os.environ.get("MODEL_URL", "")
if len(sys.argv) > 1: url = sys.argv[1]
out = tpl.replace("__MODEL_JSON__", blob).replace("__MODEL_URL__", url)
open(os.path.join(here, "model.js"), "w", encoding="utf-8").write("window.MODEL_REMOTE=" + blob + ";")
open(os.path.join(here, "app.html"), "w", encoding="utf-8").write(out)
with zipfile.ZipFile(os.path.join(here, "apk-upload.zip"), "w", zipfile.ZIP_DEFLATED) as z: z.writestr("index.html", out)
print("also wrote model.js - publish this file daily and the app picks it up without a new APK" if url else "no MODEL_URL set: the app will use the baked model only (pass the URL: python build.py https://...)")
print(f"wrote app.html and apk-upload.zip ({len(out)//1024} KB): {len(model['teams'])} teams, {len(model.get('upcoming',[]))} upcoming, {len(model['players'])} players, as of {model['as_of']}")
