#!/usr/bin/env python3
"""
Local dev server for Peace Paths dev environment.
Serves the app from dev-environment/app/ with dynamic JSON loading.

Usage:
    cd dev-environment
    python dev-serve.py          # default port 8765
    python dev-serve.py --port 8080
"""
import argparse
import http.server
import json
import os
import sys
import shutil
import subprocess
import threading
import io
import time
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DEV_ROOT = Path(__file__).parent.resolve()
PROJECT_ROOT = DEV_ROOT.parent
APP_DIR = DEV_ROOT / "app"
ADMIN_DIR = DEV_ROOT / "admin"
DATA_FILE = APP_DIR / "data.json"
SOLUTIONS_JSON = APP_DIR / "solutions.json"
TAXONOMY_FILE = PROJECT_ROOT / "taxonomy.json"
CATEGORIES_FILE = PROJECT_ROOT / "categories.json"
PROMPTS_FILE = DEV_ROOT / "prompts.json"
SOURCE_PROFILES = DEV_ROOT / "source-profiles.json"

def sync_data():
    """Sync solutions.json → data.json if data.json doesn't exist."""
    if DATA_FILE.exists():
        print(f"  data.json already exists")
        return True
    if not SOLUTIONS_JSON.exists():
        print(f"  ! Neither data.json nor solutions.json found in {APP_DIR}")
        return False
    shutil.copy2(SOLUTIONS_JSON, DATA_FILE)
    print(f"  synced solutions.json -> data.json")
    return True


# ── Analysis job state ─────────────────────────────────
analysis_status = {
    "running": False, "pid": None, "started": None, "log": "", "proc": None,
    "stages": {
        "fetch": {"label": "Fetching RSS", "icon": "📡", "done": False, "count": 0, "detail": ""},
        "classify": {"label": "Classifying", "icon": "🧠", "done": False, "count": 0, "detail": ""},
        "cluster": {"label": "Clustering", "icon": "🔗", "done": False, "count": 0, "detail": ""},
        "phase": {"label": "Determining Phases", "icon": "🗺️", "done": False, "count": 0, "detail": ""},
        "narrative": {"label": "Generating Narratives", "icon": "📝", "done": False, "count": 0, "detail": ""},
        "shift": {"label": "Detecting Shifts", "icon": "🔍", "done": False, "count": 0, "detail": ""},
        "translate": {"label": "Translating", "icon": "🌐", "done": False, "count": 0, "detail": ""},
        "output": {"label": "Writing Output", "icon": "💾", "done": False, "count": 0, "detail": ""},
    },
    "current_stage": None,
    "pct": 0,
}
SCRIPT = DEV_ROOT / "ai-analyze-prod.py"  # Narrative pipeline v2.0.0


def _parse_log_line(text, stages, status):
    """Parse a log line and update stage tracking."""
    import re
    m = re.search(r'→\s*(\d+)\s+articles?\s+fetched', text)
    if m:
        stages["fetch"]["done"] = True
        stages["fetch"]["count"] = int(m.group(1))
        stages["fetch"]["detail"] = f'{int(m.group(1))} articles'
        status["current_stage"] = "fetch"
        status["pct"] = 5
        return
    m = re.search(r'\[(\d+)/(\d+)\s+\((\d+)%\)\]', text)
    if m:
        stages["classify"]["count"] = int(m.group(1))
        stages["classify"]["detail"] = f'{m.group(1)}/{m.group(2)} ({m.group(3)}%)'
        status["current_stage"] = "classify"
        status["pct"] = 10 + int(m.group(3))
        return
    m = re.search(r'Total:\s*(\d+)\s+relevant', text)
    if m:
        stages["classify"]["done"] = True
        stages["classify"]["count"] = int(m.group(1))
        stages["classify"]["detail"] = f'{int(m.group(1))} relevant'
        status["pct"] = 40
        return
    m = re.search(r'→\s*(\d+)\s+clusters?\s+from\s+(\d+)', text)
    if m:
        stages["cluster"]["done"] = True
        stages["cluster"]["count"] = int(m.group(1))
        stages["cluster"]["detail"] = f'{int(m.group(1))} clusters from {m.group(2)} articles'
        status["current_stage"] = "cluster"
        status["pct"] = 50
        return
    m = re.search(r'✓\s+Phases\s+for\s+(\d+)', text)
    if m:
        stages["phase"]["done"] = True
        stages["phase"]["count"] = int(m.group(1))
        stages["phase"]["detail"] = f'{int(m.group(1))} solutions'
        status["current_stage"] = "phase"
        status["pct"] = 60
        return
    if "Generating narratives for" in text:
        m = re.search(r'for\s+(\d+)', text)
        if m:
            stages["narrative"]["count"] = int(m.group(1))
            status["current_stage"] = "narrative"
            status["pct"] = 65
    if ": narrative generated" in text or ": fallback narrative" in text:
        stages["narrative"]["done"] = True
        status["pct"] = 75
        return
    m = re.search(r'✓\s+(\d+)\s+shifts?\s+detected', text)
    if m:
        stages["shift"]["done"] = True
        stages["shift"]["count"] = int(m.group(1))
        stages["shift"]["detail"] = f'{int(m.group(1))} shifts'
        status["current_stage"] = "shift"
        status["pct"] = 80
        return
    m = re.search(r'Translating\s+(\d+)\s+unique', text)
    if m:
        stages["translate"]["done"] = True
        stages["translate"]["count"] = int(m.group(1))
        stages["translate"]["detail"] = f'{int(m.group(1))} texts'
        status["current_stage"] = "translate"
        status["pct"] = 88
        return
    if "Written to" in text or "Done in" in text:
        stages["output"]["done"] = True
        status["current_stage"] = "output"
        status["pct"] = 95
        return


def _make_stages():
    return {
        "fetch": {"label": "Fetching RSS", "icon": "📡", "done": False, "count": 0, "detail": ""},
        "classify": {"label": "Classifying", "icon": "🧠", "done": False, "count": 0, "detail": ""},
        "cluster": {"label": "Clustering", "icon": "🔗", "done": False, "count": 0, "detail": ""},
        "phase": {"label": "Determining Phases", "icon": "🗺️", "done": False, "count": 0, "detail": ""},
        "narrative": {"label": "Generating Narratives", "icon": "📝", "done": False, "count": 0, "detail": ""},
        "shift": {"label": "Detecting Shifts", "icon": "🔍", "done": False, "count": 0, "detail": ""},
        "translate": {"label": "Translating", "icon": "🌐", "done": False, "count": 0, "detail": ""},
        "output": {"label": "Writing Output", "icon": "💾", "done": False, "count": 0, "detail": ""},
    }


def run_analysis(mode="--fast"):
    """Run ai-analyze-prod.py in the background."""
    global analysis_status
    env = os.environ.copy()
    env.setdefault("LLAMA_CPP_URL", os.environ.get("LLAMA_CPP_URL", "http://localhost:8080"))
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(SCRIPT), mode, "--skip-upload"]
    log_lines = []
    try:
        print(f"\n  [Analysis] Starting: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        analysis_status["running"] = True
        analysis_status["pid"] = proc.pid
        analysis_status["started"] = str(datetime.now())
        analysis_status["log"] = ""
        analysis_status["proc"] = proc
        analysis_status["stages"] = _make_stages()
        analysis_status["current_stage"] = None
        analysis_status["pct"] = 0
        TIMEOUT = 30 * 60
        deadline = time.time() + TIMEOUT
        buf = b""
        while True:
            if time.time() > deadline:
                print(f"\n  [Analysis] TIMEOUT after {TIMEOUT}s, killing PID {proc.pid}")
                proc.kill()
                break
            ch = proc.stdout.read(1)
            if not ch:
                break
            buf += ch
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log_lines.append(text)
                    print(f"  [Analysis] {text}")
                    analysis_status["log"] = "\n".join(log_lines)
                    _parse_log_line(text, analysis_status["stages"], analysis_status)
        proc.stdout.close()
        proc.wait()
        analysis_status["running"] = False
        analysis_status["log"] = "\n".join(log_lines)
        analysis_status["pct"] = 100
        print(f"\n  [Analysis] Done (exit code {proc.returncode})")
        # Copy result from app/data.json into dev-environment
        live_data = PROJECT_ROOT / "app" / "data.json"
        if live_data.exists():
            shutil.copy2(live_data, DATA_FILE)
            print(f"  Copied app/data.json → dev-environment/app/data.json")
    except Exception as e:
        analysis_status["running"] = False
        analysis_status["log"] = "\n".join(log_lines) + f"\nError: {e}"


class DevHandler(http.server.BaseHTTPRequestHandler):
    MIMES = {
        '.html': 'text/html', '.js': 'application/javascript',
        '.css': 'text/css', '.json': 'application/json',
        '.png': 'image/png', '.svg': 'image/svg+xml',
        '.ttf': 'font/ttf',
    }

    def do_GET(self):
        # Admin API endpoints
        if self.path == "/api/admin/data":
            if DATA_FILE.exists():
                data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                self._json_response(data)
            else:
                self._json_response({"error": "data.json not found"})
            return
        if self.path == "/api/admin/categories":
            if CATEGORIES_FILE.exists():
                cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                self._json_response(cats)
            else:
                self._json_response([])
            return
        if self.path == "/api/admin/prompts":
            if PROMPTS_FILE.exists():
                prompts = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
                self._json_response(prompts)
            else:
                self._json_response({})
            return

        # Taxonomy
        if self.path == "/api/admin/taxonomy":
            if TAXONOMY_FILE.exists():
                tax = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
                self._json_response(tax.get("categories", tax) if isinstance(tax, dict) else tax)
            else:
                self._json_response([])
            return

        # AI health stub
        if self.path == "/api/admin/ai-health":
            self._json_response({"status": "healthy", "totalClassified": 0, "refusals": 0, "refusalRate": 0, "lastRun": datetime.now().isoformat()})
            return

        # Auto-update status stub (dev env doesn't do auto-update)
        if self.path == "/api/admin/auto-update":
            self._json_response({
                "enabled": False, "deploy": False,
                "fast": {"enabled": False, "interval": 3600, "last_run": None, "next_run": None,
                          "last_run_str": "—", "next_run_str": "—"},
                "daily": {"enabled": False, "interval": 86400, "last_run": None, "next_run": None,
                          "last_run_str": "—", "next_run_str": "—"}
            })
            return

        # Analysis status
        if self.path == "/api/analysis/status":
            status = {k: v for k, v in analysis_status.items() if k != "proc"}
            self._json_response(status)
            return

        # Analysis run — also handle GET (admin panel uses fetch without method)
        if self.path.startswith("/api/analysis/run"):
            qs = self.path.split('?')[1] if '?' in self.path else ''
            params = dict(p.split('=') for p in qs.split('&') if '=' in p)
            mode = params.get("mode", "--fast")
            if analysis_status["running"]:
                proc = analysis_status.get("proc")
                if proc and proc.poll() is not None:
                    analysis_status["running"] = False
                if analysis_status["running"]:
                    self._json_response({"error": "analysis already running"})
                    return
                t = threading.Thread(target=run_analysis, args=(mode,), daemon=True)
                t.start()
                self._json_response({"message": f"analysis started (mode={mode})"})
            else:
                t = threading.Thread(target=run_analysis, args=(mode,), daemon=True)
                t.start()
                self._json_response({"message": f"analysis started (mode={mode})"})
            return

        # Admin page — serve directly from ADMIN_DIR
        # (Only match /admin/* NOT /api/admin/*)
        if self.path.startswith("/admin") and not self.path.startswith("/api/"):
            self._serve_admin(self.path)
            return

        # favicon.ico — suppress browser request
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Strip query string
        path = self.path.split('?')[0]

        # Map root to index.html
        if path == '/':
            path = '/index.html'

        # Map data.json
        if path == '/data.json':
            if DATA_FILE.exists():
                fpath = DATA_FILE
            elif SOLUTIONS_JSON.exists():
                fpath = SOLUTIONS_JSON
            else:
                # Return empty data structure
                self._json_response({"solutions": [], "activeSolutions": []})
                return
        else:
            fpath = APP_DIR / path.removeprefix('/')

        if fpath.is_dir():
            fpath = fpath / 'index.html'

        if not fpath.is_file():
            # Fallback: check parent project's app/fonts
            if path.startswith('/fonts/'):
                parent_fonts = Path(__file__).parent.parent / 'app' / 'fonts' / path.removeprefix('/fonts/').lstrip('/')
                if parent_fonts.is_file():
                    fpath = parent_fonts
            elif path.startswith('/app/'):
                fpath = Path(__file__).parent.parent / path.removeprefix('/')

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

    def _serve_admin(self, path):
        """Serve admin files directly from ADMIN_DIR."""
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

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(body.decode('utf-8'))

        if self.path == "/api/admin/narrative":
            sol_id = data.get("solutionId", "")
            field = data.get("field", "")
            value = data.get("value")
            try:
                if not DATA_FILE.exists():
                    self._json_response({"error": "data.json not found"})
                    return
                data_json = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                sol = next((s for s in data_json.get("solutions", []) if s["id"] == sol_id), None)
                if not sol:
                    self._json_response({"error": f"Solution '{sol_id}' not found"})
                    return
                if "narrative" not in sol:
                    sol["narrative"] = {}
                sol["narrative"][field] = value
                DATA_FILE.write_text(json.dumps(data_json, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)})
            return
        if self.path == "/api/admin/narratives-save":
            try:
                if not DATA_FILE.exists():
                    self._json_response({"error": "data.json not found"})
                    return
                data_json = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                incoming = {s["id"]: s.get("narrative") for s in data.get("solutions", []) if s.get("narrative")}
                for sol in data_json.get("solutions", []):
                    if sol["id"] in incoming and incoming[sol["id"]]:
                        sol["narrative"] = incoming[sol["id"]]
                DATA_FILE.write_text(json.dumps(data_json, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True, "saved": len(incoming)})
            except Exception as e:
                self._json_response({"error": str(e)})
            return
        # ── Category CRUD ────────────────────────────
        if self.path == "/api/admin/categories":
            action = data.get("action", "")
            if action == "add":
                try:
                    if not CATEGORIES_FILE.exists():
                        cats = []
                    else:
                        cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                    new_cat = {
                        "id": data.get("id", ""),
                        "name": data.get("name", ""),
                        "phases": data.get("phases", []),
                        "core": data.get("core", True),
                    }
                    cats.append(new_cat)
                    CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                    self._json_response({"ok": True})
                except Exception as e:
                    self._json_response({"error": str(e)})
            elif action == "update":
                try:
                    cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                    for cat in cats:
                        if cat["id"] == data.get("id"):
                            for k in ("name", "phases", "core"):
                                if k in data:
                                    cat[k] = data[k]
                            break
                    CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                    self._json_response({"ok": True})
                except Exception as e:
                    self._json_response({"error": str(e)})
            elif action == "delete":
                try:
                    cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                    cats = [c for c in cats if c["id"] != data.get("id")]
                    CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                    self._json_response({"ok": True})
                except Exception as e:
                    self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/categories/bulk-delete":
            try:
                ids = set(data.get("ids", []))
                cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                cats = [c for c in cats if c["id"] not in ids]
                CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True, "deleted": len(ids)})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/categories/bulk-import":
            try:
                existing = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8")) if CATEGORIES_FILE.exists() else []
                existing_ids = {c["id"] for c in existing}
                for cat in data.get("categories", []):
                    if cat["id"] not in existing_ids:
                        existing.append(cat)
                CATEGORIES_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True, "imported": len(data.get("categories", []))})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/categories/move":
            try:
                cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                cat_id = data.get("id")
                target_id = data.get("targetId")
                direction = data.get("direction", "after")
                cat = next((c for c in cats if c["id"] == cat_id), None)
                if not cat:
                    self._json_response({"error": "Category not found"})
                    return
                idx = cats.index(cat)
                target = next((c for c in cats if c["id"] == target_id), None)
                if target:
                    target_idx = cats.index(target)
                    cats.pop(idx)
                    new_idx = target_idx + (1 if direction == "after" else 0)
                    cats.insert(new_idx, cat)
                CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/categories/bulk-core-toggle":
            try:
                ids = data.get("ids", [])
                set_core = data.get("setCore", True)
                cats = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                for cat in cats:
                    if cat["id"] in ids:
                        cat["core"] = set_core
                CATEGORIES_FILE.write_text(json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/taxonomy/bulk-delete":
            try:
                ids = set(data.get("ids", []))
                tax = json.loads(TAXONOMY_FILE.read_text(encoding="utf-8"))
                tax = [c for c in tax if c["id"] not in ids]
                TAXONOMY_FILE.write_text(json.dumps(tax, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True, "deleted": len(ids)})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/prompts":
            try:
                key = data.get("key", "")
                value = data.get("value")
                if PROMPTS_FILE.exists():
                    prompts = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
                else:
                    prompts = {}
                if key and value is not None:
                    prompts[key] = value
                PROMPTS_FILE.write_text(json.dumps(prompts, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        if self.path == "/api/admin/auto-update":
            self._json_response({"ok": True, "message": "Auto-update not available in dev environment"})
            return

        if self.path == "/api/admin/deploy":
            self._json_response({"ok": False, "error": "Deploy not available in dev environment"})
            return

        if self.path == "/api/admin/verify":
            try:
                results = run_web_verification(data.get("solutions", []))
                self._json_response(results)
            except Exception as e:
                self._json_response({"error": str(e)})
            return

        # ── Analysis endpoints ─────────────────────────
        if self.path.startswith("/api/analysis/run"):
            qs = self.path.split('?')[1] if '?' in self.path else ''
            params = dict(p.split('=') for p in qs.split('&') if '=' in p)
            mode = params.get("mode", "--fast")
            if analysis_status["running"]:
                proc = analysis_status.get("proc")
                if proc and proc.poll() is not None:
                    analysis_status["running"] = False
                if analysis_status["running"]:
                    self._json_response({"error": "analysis already running"})
                    return
                t = threading.Thread(target=run_analysis, args=(mode,), daemon=True)
                t.start()
                self._json_response({"message": f"analysis started (mode={mode})"})
            else:
                t = threading.Thread(target=run_analysis, args=(mode,), daemon=True)
                t.start()
                self._json_response({"message": f"analysis started (mode={mode})"})
            return

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

        self.send_error(404, "Not found")

    def log_message(self, format, *args):
        print(f"  [HTTP] {args[0]}")

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_web_verification(solutions):
    """Verify AI-generated narratives using web search via local SearXNG."""
    search_url = os.environ.get("SEARXNG_URL", "http://192.168.2.213:8888")
    results = []

    for sol in solutions:
        narrative = sol.get("narrative", {})
        if not narrative:
            continue

        claims = []
        if narrative.get("longTerm"):
            lt = narrative["longTerm"]
            claims.append({"type": "longTerm", "text": lt["en"] if isinstance(lt, dict) else lt})
        if narrative.get("weeklyHighlight"):
            wh = narrative["weeklyHighlight"]
            claims.append({"type": "weeklyHighlight", "text": wh["en"] if isinstance(wh, dict) else wh})
        for ev in narrative.get("keyEvents", []):
            title = ev.get("title", "")
            if isinstance(title, dict):
                title = title.get("en", "")
            if title:
                claims.append({"type": "keyEvent", "text": title})

        search_results = []
        total_score = 0
        verified_count = 0
        discrepancies = []

        for claim in claims:
            query = claim["text"][:120]
            try:
                url = f"{search_url}/search?q={urllib.parse.quote(query)}&format=json&categories=general&language=en"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    search_data = json.loads(resp.read().decode())
                results_list = search_data.get("results", [])
                if results_list:
                    top = results_list[0]
                    score = 0
                    claim_words = set(claim["text"].lower().split())
                    title_words = set(top.get("title", "").lower().split())
                    if claim_words:
                        score = int(len(claim_words & title_words) / len(claim_words) * 60)
                    source = top.get("source", "").lower()
                    if any(s in source for s in ["reuters", "ap", "bbc", "al jazeera"]):
                        score += 15
                    score = min(score, 100)
                    total_score += score
                    verified_count += 1
                    if score < 40:
                        discrepancies.append(f"Low confidence: '{claim['text'][:60]}...'")
                    search_results.append({
                        "claim": claim["text"][:100], "claim_type": claim["type"],
                        "score": score, "source": top.get("source", ""),
                        "title": top.get("title", ""), "url": top.get("url", ""),
                        "snippet": top.get("snippet", "")[:200]
                    })
                else:
                    discrepancies.append(f"No results: '{claim['text'][:60]}...'")
            except Exception as e:
                discrepancies.append(f"Search failed: {str(e)[:50]}")

        avg_score = int(total_score / verified_count) if verified_count > 0 else 0
        nt = narrative.get("longTerm", "")
        if isinstance(nt, dict):
            nt = nt.get("en", "")

        results.append({
            "id": sol["id"], "name": sol.get("name", ""), "icon": sol.get("icon", "📰"),
            "score": avg_score, "verified_count": verified_count,
            "total_claims": len(claims), "narrativeText": nt,
            "queries": [c["text"][:80] for c in claims],
            "results": search_results, "discrepancies": discrepancies,
            "summary": f"{verified_count}/{len(claims)} verified. Score: {avg_score}%"
        })

    return {"verified": results, "timestamp": datetime.now().isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    if not args.no_sync:
        if not sync_data():
            print("  Creating empty data.json...")
            DATA_FILE.write_text(json.dumps({"solutions": [], "activeSolutions": []}, indent=2), encoding="utf-8")

    print(f"\n  Serving Peace Paths (dev) on http://localhost:{args.port}")
    print(f"  Press Ctrl+C to stop\n")

    os.chdir(APP_DIR)
    try:
        class ReuseServer(http.server.HTTPServer):
            allow_reuse_address = True
            daemon_threads = True
        with ReuseServer(("127.0.0.1", args.port), DevHandler) as httpd:
            httpd.serve_forever()
    except OSError as e:
        print(f"\n  ERROR: Could not bind to port {args.port}: {e}")
        print("  Another server may be using this port.")
        print("  Try a different port: python dev-serve.py --port 8767")
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
