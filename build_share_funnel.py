#!/usr/bin/env python3
"""
Bake the weekly shareable funnel dashboard.

Reads:  shopify_funnel_data.json  (written by the weekly Cowork scheduled task)
        share_template.html       (static dashboard template)
Writes: shopify_funnel.html      (self-contained, shareable anywhere)
Then:   commits & pushes shopify_funnel.html to GitHub (if github_repo.txt
        and github_token.txt exist), which triggers GitHub Pages redeploy.

Run from the project folder:  python3 build_share_funnel.py
Skip the push:                python3 build_share_funnel.py --no-push
"""
import json, os, re, subprocess, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "shopify_funnel_data.json")
TEMPLATE = os.path.join(HERE, "share_template.html")
OUTPUT = os.path.join(HERE, "shopify_funnel.html")
REPO_FILE = os.path.join(HERE, "github_repo.txt")    # e.g. ohpolly-kieran/funnel-dashboard
TOKEN_FILE = os.path.join(HERE, "github_token.txt")  # GitHub PAT — gitignored, never commit

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)

def run(cmd, **kw):
    print("  $", " ".join(c if "token" not in c.lower() and "@github" not in c else "<redacted>" for c in cmd))
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, **kw)

# ---------- 1. Validate data ----------
if not os.path.exists(DATA_FILE):
    fail(f"{DATA_FILE} not found — the weekly fetch step must run first.")
with open(DATA_FILE) as f:
    data = json.load(f)

for key in ("bakedAt", "store", "cuts"):
    if key not in data:
        fail(f"shopify_funnel_data.json missing '{key}'")
for cut in ("all", "direct", "search", "social"):
    if cut not in data["cuts"]:
        fail(f"cuts missing '{cut}'")
    for q in ("daily", "yoy", "devCur", "devPrev", "ctryCur", "ctryPrev"):
        if q not in data["cuts"][cut]:
            fail(f"cuts.{cut} missing '{q}'")
for q in ("srcCur", "srcPrev"):
    if q not in data["cuts"]["all"]:
        fail(f"cuts.all missing '{q}'")

n_days = len(data["cuts"]["all"]["daily"])
if n_days < 30:
    fail(f"cuts.all.daily has only {n_days} rows — expected ~35 days. Refusing to bake stale/partial data.")

# Sanity: bakedAt should be recent
baked = date.fromisoformat(data["bakedAt"])
age = (date.today() - baked).days
if age > 8:
    print(f"WARNING: snapshot is {age} days old (bakedAt={data['bakedAt']})")

# ---------- 1b. Merge GA4 first-touch data if available ----------
ft_path = os.path.join(HERE, "first_touch.json")
if os.path.exists(ft_path):
    with open(ft_path) as f:
        data["ga4Channels"] = json.load(f)
    print(f"Merged first_touch.json (window {data['ga4Channels']['window']['start']} → {data['ga4Channels']['window']['end']})")
else:
    print("No first_touch.json — dashboard's Channel CVR section will be hidden. Run fetch_first_touch.py on the Mac to enable it.")

# ---------- 2. Inject into template ----------
with open(TEMPLATE) as f:
    html = f.read()
if "__SNAPSHOT_JSON__" not in html:
    fail("share_template.html has no __SNAPSHOT_JSON__ placeholder")
html = html.replace("__SNAPSHOT_JSON__", json.dumps(data, separators=(",", ":")))
with open(OUTPUT, "w") as f:
    f.write(html)
size_kb = os.path.getsize(OUTPUT) // 1024
print(f"Baked {OUTPUT} ({size_kb} KB, data to {data['bakedAt']}, store: {data['store']})")

# ---------- 3. Push to GitHub ----------
if "--no-push" in sys.argv:
    print("Skipping push (--no-push)")
    sys.exit(0)
if not (os.path.exists(REPO_FILE) and os.path.exists(TOKEN_FILE)):
    print("No github_repo.txt / github_token.txt — baked file only, not pushed.")
    sys.exit(0)

repo = open(REPO_FILE).read().strip().removeprefix("https://github.com/").removesuffix(".git")
token = open(TOKEN_FILE).read().strip()
if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
    fail(f"github_repo.txt should contain 'owner/repo', got: {repo}")

push_url = f"https://x-access-token:{token}@github.com/{repo}.git"

if not os.path.isdir(os.path.join(HERE, ".git")):
    print("Initialising git repo (first run)…")
    run(["git", "init", "-b", "main"])

# Narrow commit: the baked dashboard AND its template. Committing the template
# is deliberate — leaving it uncommitted let a `git pull --rebase` silently
# reset it to an old version (regression, 24 Jun 2026). Never `git add .` here —
# this folder contains credentials and 100MB+ data files.
run(["git", "add", "--", "shopify_funnel.html", "share_template.html"])
r = run(["git", "-c", "user.name=Funnel Bot", "-c", "user.email=funnel-bot@ohpolly.com",
         "commit", "-m", f"Weekly funnel bake — data to {data['bakedAt']}"])
if r.returncode != 0:
    if "nothing to commit" in (r.stdout + r.stderr):
        print("No changes since last bake — nothing to push.")
        sys.exit(0)
    # A failed commit must NEVER be ignored — otherwise we push stale content
    # while claiming success (this bit us on 12 Jun 2026: a stale .git lock file).
    if "index.lock" in r.stderr or "HEAD.lock" in r.stderr:
        fail(f"git commit failed due to a stale lock file. Delete .git/HEAD.lock and "
             f".git/index.lock in the project folder, then re-run.\n{r.stderr[-500:]}")
    fail(f"git commit failed — dashboard NOT updated:\n{r.stderr[-800:]}")

r = run(["git", "push", push_url, "main"])
if r.returncode != 0:
    # First push to an empty repo with unrelated history, or remote ahead
    r2 = run(["git", "pull", "--rebase", "--allow-unrelated-histories", push_url, "main"])
    r = run(["git", "push", push_url, "main"])
    if r.returncode != 0:
        fail(f"git push failed:\n{r.stderr[-1500:]}")
print(f"Pushed. Dashboard will be live at GitHub Pages for {repo} within ~2 minutes.")
