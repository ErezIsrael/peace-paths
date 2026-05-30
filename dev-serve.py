#!/usr/bin/env python3
"""
Local dev server for Peace Paths.
Serves the Peace Room app from app/ and the Admin panel.

Usage:
    python dev-serve.py          # default port 8766
    python dev-serve.py --port 8766

Admin panel: http://localhost:8766/admin/
"""
import argparse
import http.server
import json
import os
import string
import re
import sys
import subprocess
import threading
import time
import shutil
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv optional

PROJECT_ROOT = Path(__file__).parent.resolve()
APP_DIR = PROJECT_ROOT / "app"
ADMIN_DIR = PROJECT_ROOT / "admin"
DATA_FILE = APP_DIR / "solutions.json"
LIVE_DATA_JSON = APP_DIR / "data.json"
SOLUTIONS_JSON = APP_DIR / "solutions.json"
SCRIPT = PROJECT_ROOT / "ai-analyze-prod.py"
TAXONOMY_FILE = PROJECT_ROOT / "taxonomy.json"
CATEGORIES_FILE = PROJECT_ROOT / "categories.json"

# Track analysis job state
analysis_status = {"running": False, "pid": None, "started": None, "log": "", "proc": None}


def sync_data():
    """Sync solutions.json → data.json for local dev server."""
    if not DATA_FILE.exists():
        print(f"  ! {DATA_FILE} not found")
        return False
    shutil.copy2(DATA_FILE, LIVE_DATA_JSON)
    print(f"  synced {DATA_FILE} -> {LIVE_DATA_JSON}")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"  {len(data.get('solutions', []))} solutions, "
          f"{sum(len(s.get('events', [])) for s in data.get('solutions', []))} events")
    return True


def run_analysis(mode="--fast"):
    """Run ai-analyze-prod.py in the background."""
    global analysis_status
    env = os.environ.copy()
    env.setdefault("LLAMA_CPP_URL", "http://localhost:8080")  # .env loaded at startup
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(SCRIPT), mode]
    log_lines = []
    try:
        print(f"\n  [Analysis] Starting: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        analysis_status = {"running": True, "pid": proc.pid, "started": str(datetime.now()), "log": "", "proc": proc}
        # Hard timeout: 30 minutes — kill if analysis hangs
        TIMEOUT = 30 * 60
        deadline = time.time() + TIMEOUT
        # Read raw bytes and decode as UTF-8 to avoid surrogate issues
        buf = b""
        while True:
            if time.time() > deadline:
                print(f"\n  [Analysis] TIMEOUT after {TIMEOUT}s, killing PID {proc.pid}")
                proc.kill()
                break
            ch = proc.stdout.read(1)
            if not ch:
                # EOF — process exited
                break
            buf += ch
            # Process complete lines (handle \r\n on Windows)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log_lines.append(text)
                    print(f"  [Analysis] {text}")
                    analysis_status["log"] = "\n".join(log_lines)
        proc.stdout.close()
        proc.wait()
        analysis_status["running"] = False
        analysis_status["log"] = "\n".join(log_lines)
        print(f"\n  [Analysis] Done (exit code {proc.returncode})")
        sync_data()
    except Exception as e:
        analysis_status["running"] = False
        analysis_status["log"] = "\n".join(log_lines) + f"\nError: {e}"
        print(f"  [Analysis] Error: {e}")


def infer_keywords(name, description):
    """Generate keyword candidates from a category's name and description."""
    text = (name + " " + description).lower()
    words = text.split()
    # Filter to meaningful words >= 3 chars, skip common stop words
    stops = {"the","and","for","are","but","not","all","with","its","from","this","that",
             "new","also","including","reports","across","various","between","ongoing",
             "efforts","role","potential","growing","increasing","impact","issues",
             "related","both","while","over","under","into","after","before","upon",
             "covering","articles"}
    seen = set()
    result = []
    for w in words:
        clean = w.strip(string.punctuation)
        if len(clean) >= 3 and clean not in stops and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result

def generate_category(name):
    """Ask the LLM to generate description, phases, keywords, and icon for a category name."""
    url = os.environ.get("LLAMA_CPP_URL", "http://localhost:8080")
    model = os.environ.get("AI_MODEL", "Qwen3.6-27B")
    api_key = os.environ.get("LLAMA_API_KEY", "")

    prompt = (
        f"Generate a category definition for a Middle East peace initiative tracker.\n"
        f"The category is about: '{name}'\n\n"
        f"Output exactly this JSON format (no markdown, no extra text):\n"
        f'{{"description": "...", "icon": "...emoji...", "phases": ["phase1", "phase2", "phase3", "phase4", "phase5"], "keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"]}}'
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert on Middle East politics and peace initiatives. Generate concise, accurate category metadata."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    from urllib.request import Request, urlopen
    req = Request(f"{url}/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(req, timeout=30) as f:
            response = json.loads(f.read().decode())
        text = response.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else "".join(lines[1:]).strip()
        result = json.loads(text)
        # Validate required fields
        if "phases" not in result or not isinstance(result.get("phases"), list):
            raise ValueError("Missing phases")
        if "keywords" not in result or not isinstance(result.get("keywords"), list):
            raise ValueError("Missing keywords")
        return result
    except Exception as e:
        # Fallback to keyword inference
        kws = infer_keywords(name, "")
        return {
            "description": f"Articles related to {name}",
            "icon": "📊",
            "phases": ["Emerged", "Developing", "Gaining Traction", "Maturing", "Resolved"],
            "keywords": kws[:8],
            "_fallback": True,
        }


def load_taxonomy():
    """Load AI-proposed categories from taxonomy.json."""
    if not TAXONOMY_FILE.exists():
        return []
    data = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
    result = []
    for cat in data.get("categories", []):
        kws = cat.get("keywords") or infer_keywords(cat["name"], cat.get("description", ""))
        result.append({
            "id": cat["id"],
            "icon": cat.get("icon", "📊"),
            "name": cat["name"],
            "description": cat.get("description", ""),
            "phases": cat.get("phases") or ["Emerged", "Developing", "Gaining Traction", "Maturing", "Resolved"],
            "keywords": kws,
        })
    return result


def load_categories():
    """Load categories from categories.json."""
    if not CATEGORIES_FILE.exists():
        return []
    with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_categories(categories):
    """Save categories list to categories.json."""
    with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(categories, f, indent=2, ensure_ascii=False)
    print(f"  [Admin] Saved {len(categories)} categories to categories.json")



class DevHandler(http.server.BaseHTTPRequestHandler):
    MIMES = {
        '.html': 'text/html', '.js': 'application/javascript',
        '.css': 'text/css', '.json': 'application/json',
        '.png': 'image/png', '.svg': 'image/svg+xml',
    }

    def do_GET(self):
        # Admin API
        if self.path == "/api/admin/categories":

            cats = load_categories()
            self._json_response(cats)
            return
        if self.path == "/api/admin/taxonomy":
            cats = load_taxonomy()
            self._json_response(cats)
            return
        if self.path == "/api/admin/ai-health":
            for p in (SOLUTIONS_JSON, LIVE_DATA_JSON):
                if p.exists():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        health = data.get("aiHealth", None)
                        if health:
                            self._json_response(health)
                            return
                    except:
                        pass
            self._json_response({"status": "unknown", "refusals": 0, "refusalRate": 0})
            return
        if self.path == "/api/analysis/status":
            # Strip proc object before serializing
            status = {k: v for k, v in analysis_status.items() if k != "proc"}
            self._json_response(status)
            return
        if self.path.startswith("/api/analysis/run"):
            mode = self.path.split("mode=")[1] if "mode=" in self.path else "--fast"
            # If previous process died but flag is still set, reset
            if analysis_status["running"]:
                proc = analysis_status.get("proc")
                if proc and proc.poll() is not None:
                    analysis_status["running"] = False
            if analysis_status["running"]:
                self._json_response({"error": "analysis already running"})
            else:
                t = threading.Thread(target=run_analysis, args=(mode,), daemon=True)
                t.start()
                self._json_response({"message": f"analysis started (mode={mode})"})
            return

        # Admin page — serve directly from ADMIN_DIR (local only, never deployed)
        if self.path.startswith("/admin"):
            self._serve_admin(self.path)
            return

        # favicon.ico — suppress browser request
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Static files from APP_DIR
        self._serve_static(self.path)

    def log_message(self, format, *args):
        # Print all requests for debugging
        print(f"  [HTTP] {args[0]}")

    def _serve_admin(self, path):
        """Serve admin files directly from ADMIN_DIR (local dev only)."""
        path = path.split('?')[0]
        if path == '/admin' or path == '/admin/':
            path = '/index.html'
        fpath = ADMIN_DIR / path.removeprefix('/admin').lstrip('/')
        if fpath.is_file():
            ext = fpath.suffix
            ct = self.MIMES.get(ext, 'application/octet-stream')
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404, f"Not found: {path}")

    def _serve_static(self, path):
        # Strip query string
        path = path.split('?')[0]
        # Map '/' or directory paths to index.html
        if path == '/':
            path = '/index.html'
        # Serve data.json if it exists (set by deploy/test or sync), fallback to solutions.json
        if path == '/data.json':
            if LIVE_DATA_JSON.exists():
                fpath = LIVE_DATA_JSON
            elif SOLUTIONS_JSON.exists():
                fpath = SOLUTIONS_JSON
        else:
            fpath = APP_DIR / path.removeprefix('/')
        if fpath.is_dir():
            fpath = fpath / 'index.html'
        if not fpath.is_file():
            # Fallback: fonts live under PROJECT_ROOT/app/fonts/
            if path.startswith('/fonts/'):
                fpath = PROJECT_ROOT / 'app' / 'fonts' / path.removeprefix('/fonts/').lstrip('/')
            elif path.startswith('/app/'):
                fpath = PROJECT_ROOT / path.removeprefix('/')
        if not fpath.is_file():
            # Fallback: top-level files (index.html, styles.css, app.js)
            fpath = PROJECT_ROOT / path.removeprefix('/')
        if fpath.is_file():
            ext = fpath.suffix
            ct = self.MIMES.get(ext, 'application/octet-stream')
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404, f"Not found: {path}")

    def do_POST(self):
        if self.path == "/api/analysis/cancel":
            proc = analysis_status.get("proc")
            if proc and proc.poll() is None:
                proc.kill()
                analysis_status["running"] = False
                analysis_status["log"] += "\n[Cancelled by user]"
                self._json_response({"ok": True, "message": "Analysis cancelled"})
            else:
                analysis_status["running"] = False
                self._json_response({"ok": True, "message": "No analysis running"})
            return
        if self.path == "/api/admin/generate":
            data = self._read_json()
            name = data.get("name", "").strip()
            if not name:
                self._json_error("Name is required")
                return
            try:
                result = generate_category(name)
                # Generate kebab-case ID from name
                import re as _re
                cat_id = _re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
                result["id"] = cat_id
                result["name"] = name
                self._json_response(result)
            except Exception as e:
                self._json_error(f"Generation failed: {e}")
            return
        if self.path == "/api/admin/categories":
            data = self._read_json()
            load_categories()  # validate readability
            try:
                categories = load_categories()
                # Check for duplicate ID
                if any(c["id"] == data["id"] for c in categories):
                    self._json_error(f"Category '{data['id']}' already exists")
                    return
                categories.append(data)
                save_categories(categories)
                self._json_response({"ok": True})
            except Exception as e:
                print(f"  [ERROR] POST /api/admin/categories: {e}", flush=True)
                self._json_error(str(e))
            return
        if self.path == "/api/admin/categories/bulk-import":
            data = self._read_json()
            try:
                existing = load_categories()
                existing_ids = {c["id"] for c in existing}
                imported = []
                for cat in data.get("categories", []):
                    if cat["id"] not in existing_ids:
                        existing.append(cat)
                        existing_ids.add(cat["id"])
                        imported.append(cat["id"])
                if imported:
                    save_categories(existing)
                self._json_response({"ok": True, "imported": imported})
            except Exception as e:
                self._json_error(str(e))
            return
        if self.path == "/api/admin/deploy":
            data = self._read_json()
            target = data.get("target", "")  # 'test' or 'live'
            try:
                result = deploy_categories(target)
                self._json_response(result)
            except Exception as e:
                self._json_error(str(e))
            return
        if self.path == "/api/admin/categories/bulk-delete":
            data = self._read_json()
            ids = data.get("ids", [])
            try:
                categories = load_categories()
                before = len(categories)
                categories = [c for c in categories if c["id"] not in set(ids)]
                deleted = before - len(categories)
                save_categories(categories)
                self._json_response({"ok": True, "deleted": deleted, "ids": ids})
            except Exception as e:
                self._json_error(str(e))
            return
        if self.path == "/api/admin/taxonomy/bulk-delete":
            data = self._read_json()
            ids = data.get("ids", [])
            try:
                raw = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
                cats = raw.get("categories", [])
                before = len(cats)
                cats = [c for c in cats if c["id"] not in set(ids)]
                deleted = before - len(cats)
                raw["categories"] = cats
                TAXONOMY_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True, "deleted": deleted, "ids": ids})
            except Exception as e:
                self._json_error(str(e))
            return
        if self.path == "/api/admin/categories/move":
            data = self._read_json()
            cat_id = data.get("id")
            try:
                tax = load_taxonomy()
                tax_cat = next((c for c in tax if c["id"] == cat_id), None)
                if not tax_cat:
                    self._json_error(f"Taxonomy category '{cat_id}' not found")
                    return
                existing = load_categories()
                if any(c["id"] == cat_id for c in existing):
                    self._json_error(f"Category '{cat_id}' already exists in SOLUTIONS")
                    return
                # Taxonomy categories lack phases/keywords — add defaults
                tax_cat["phases"] = tax_cat.get("phases") or ["Emerged", "Developing", "Gaining Traction", "Maturing", "Resolved"]
                tax_cat["keywords"] = tax_cat.get("keywords") or []
                existing.append(tax_cat)
                save_categories(existing)
                self._json_response({"ok": True, "imported": cat_id})
            except Exception as e:
                self._json_error(str(e))
            return
        if self.path == "/api/admin/categories/bulk-core-toggle":
            data = self._read_json()
            ids = data.get("ids", [])
            set_core = data.get("setCore", True)
            try:
                categories = load_categories()
                updated = 0
                for cat in categories:
                    if cat["id"] in set(ids):
                        cat["core"] = set_core
                        updated += 1
                save_categories(categories)
                self._json_response({"ok": True, "updated": updated, "core": set_core})
            except Exception as e:
                self._json_error(str(e))
            return
        self._json_error("Not found")

    def do_PUT(self):
        if self.path.startswith("/api/admin/categories/"):
            cat_id = self.path.split("/")[-1]
            data = self._read_json()
            data["id"] = cat_id  # ensure ID matches URL
            try:
                categories = load_categories()
                for i, c in enumerate(categories):
                    if c["id"] == cat_id:
                        categories[i] = data
                        break
                else:
                    self._json_error(f"Category '{cat_id}' not found")
                    return
                save_categories(categories)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_error(str(e))
            return
        self._json_error("Not found")

    def do_DELETE(self):
        if self.path.startswith("/api/admin/categories/"):
            cat_id = self.path.split("/")[-1]
            try:
                categories = load_categories()
                categories = [c for c in categories if c["id"] != cat_id]
                save_categories(categories)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_error(str(e))
            return
        self._json_error("Not found")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length))

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, msg, code=400):
        self._json_response({"error": msg})




def deploy_categories(target, selected_ids=None):
    """Deploy solutions.json to test or live environment.

    target: 'test' -> copies solutions.json to data.json (local dev, no Cloudflare)
    target: 'live' -> copies to data.json + wrangler pages deploy to Cloudflare
    selected_ids: unused now (deploy uses latest analysis output)
    """
    import shutil

    if not SOLUTIONS_JSON.exists():
        return {"error": "No analysis data found. Run analysis first."}

    data = json.loads(SOLUTIONS_JSON.read_text(encoding="utf-8"))

    # Filter out solutions for categories no longer in categories.json
    cat_ids = {c["id"] for c in load_categories()}
    data["solutions"] = [s for s in data.get("solutions", []) if s["id"] in cat_ids]
    # Recalculate activeSolutions
    data["activeSolutions"] = [s["id"] for s in data["solutions"]]
    count = len(data["solutions"])

    # Always sync to data.json so the dev server serves the latest data
    LIVE_DATA_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [Deploy] Wrote {count} solutions to {LIVE_DATA_JSON}")

    if target == "test":
        # Local only — no Cloudflare upload
        return {"ok": True, "deployed": count, "target": "test"}

    elif target == "live":
        # Upload data.json to Cloudflare KV (served via Pages Function)
        print("  [Deploy] Uploading data.json to Cloudflare KV...")
        project_root = str(PROJECT_ROOT)
        kv_id = "badf4fb7acfe4d1c905db77ed8d5e70f"
        cmd = f'npx wrangler kv key put "data.json" --namespace-id={kv_id} --path="{LIVE_DATA_JSON}" --remote'
        result = subprocess.run(
            cmd, shell=True,
            cwd=project_root,
            capture_output=True
        )
        try:
            result.stdout = result.stdout.decode('utf-8', errors='replace')
            result.stderr = result.stderr.decode('utf-8', errors='replace')
        except Exception:
            pass
        if result.returncode == 0:
            print("  [Deploy] Success — data.json uploaded to KV")
            return {"ok": True, "deployed": count, "target": "live", "url": "https://peace-paths.pages.dev"}
        else:
            print(f"  [Deploy] KV upload failed: {result.stderr[:200]}")
            return {"ok": False, "error": "KV upload failed", "stderr": result.stderr[:200]}

    return {"error": f"Unknown target: {target}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args()

    # Fix Windows console encoding
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    if not args.no_sync:
        if not sync_data():
            sys.exit(1)

    # Reset analysis state on startup (zombie processes from previous run)
    analysis_status["running"] = False
    analysis_status["pid"] = None
    analysis_status["log"] = ""
    analysis_status["proc"] = None

    print(f"\n  Serving Peace Room on http://localhost:{args.port}")
    print(f"  Admin panel: http://localhost:{args.port}/admin/")
    print(f"  Press Ctrl+C to stop\n")

    os.chdir(APP_DIR)
    try:
        with http.server.HTTPServer(("127.0.0.1", args.port), DevHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
