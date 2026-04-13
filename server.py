#!/usr/bin/env python3
"""
server.py — Local sync server for the Job Search Dashboard.
Serves the HTML dashboard + a REST API backed by contacts.json.
Access: http://localhost:8765
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

BASE_DIR   = Path(__file__).parent
DATA_FILE  = BASE_DIR / "data" / "contacts.json"
CRITERIA_FILE = BASE_DIR / "data" / "criteria.json"
HTML_FILE  = BASE_DIR / "job-search-dashboard.html"
PORT       = 8765


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _get_env_key(name: str) -> str:
    val = os.getenv(name, "")
    if val:
        return val
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def _ai_generate(prompt: str, system: str = "") -> str:
    """Call Groq first, fall back to Gemini."""
    groq_key = _get_env_key("GROQ_API_KEY")
    if groq_key:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            body = json.dumps({"model": GROQ_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 2048}).encode()
            req = urllib.request.Request(
                GROQ_URL, data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {groq_key}", "User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  Groq error, trying Gemini: {e}")

    gemini_key = _get_env_key("GEMINI_API_KEY")
    if gemini_key:
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"[System]: {system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        body = json.dumps({"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}).encode()
        req = urllib.request.Request(
            f"{GEMINI_URL}?key={gemini_key}", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]

    raise RuntimeError("No AI provider available. Add GROQ_API_KEY to .env")


def _sync_excel(contacts: list) -> None:
    """Fire-and-forget Excel sync — runs seed_jobs update_excel in a subprocess."""
    try:
        script = BASE_DIR / "seed_jobs.py"
        if script.exists():
            subprocess.Popen(
                [sys.executable, "-c",
                 f"import sys; sys.path.insert(0,'{BASE_DIR}'); "
                 f"from seed_jobs import update_excel, load_json; "
                 f"from pathlib import Path; "
                 f"update_excel({json.dumps(contacts)})"],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception:
        pass


class DashboardHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        path = args[0] if args else ""
        if any(x in str(path) for x in ["/api/", "GET / ", "GET /job"]):
            print(f"  [{self.address_string()}] {fmt % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        if path == "" or path == "/":
            self._serve_file(HTML_FILE, "text/html")
        elif path == "/api/contacts":
            contacts = load_json(DATA_FILE, [])
            self._json_response(contacts)
        elif path == "/api/criteria":
            criteria = load_json(CRITERIA_FILE, {})
            self._json_response(criteria)
        elif path == "/api/status":
            contacts = load_json(DATA_FILE, [])
            by_status: dict = {}
            for c in contacts:
                s = c.get("status", "not_contacted")
                by_status[s] = by_status.get(s, 0) + 1
            self._json_response({"total": len(contacts), "by_status": by_status})
        else:
            # Serve static files (venv excluded)
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"

        try:
            data = json.loads(body)
        except Exception:
            self._error(400, "Invalid JSON")
            return

        if path == "/api/contacts":
            # Full replace (import from dashboard)
            if not isinstance(data, list):
                self._error(400, "Expected a JSON array")
                return
            save_json(DATA_FILE, data)
            _sync_excel(data)
            self._json_response({"ok": True, "count": len(data)})

        elif path == "/api/contacts/add":
            contacts = load_json(DATA_FILE, [])
            contacts.append(data)
            save_json(DATA_FILE, contacts)
            _sync_excel(contacts)
            self._json_response({"ok": True, "id": data.get("id")})

        elif path == "/api/criteria":
            save_json(CRITERIA_FILE, data)
            self._json_response({"ok": True})

        elif path == "/api/ai":
            prompt = data.get("prompt", "")
            system = data.get("system", "")
            if not prompt:
                self._error(400, "prompt is required")
                return
            try:
                text = _ai_generate(prompt, system)
                self._json_response({"text": text})
            except Exception as e:
                self._error(500, str(e))

        elif path == "/api/reseed":
            try:
                result = subprocess.run(
                    [sys.executable, str(BASE_DIR / "seed_jobs.py")],
                    capture_output=True, text=True, timeout=120, cwd=str(BASE_DIR)
                )
                output = result.stdout + result.stderr
                contacts = load_json(DATA_FILE, [])
                self._json_response({"ok": result.returncode == 0, "count": len(contacts), "output": output})
            except Exception as e:
                self._json_response({"ok": False, "output": str(e)})

        else:
            self._error(404, "Not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data, status: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, file_path: Path, content_type: str):
        if not file_path.exists():
            self._error(404, f"File not found: {file_path.name}")
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)


def main():
    os.chdir(BASE_DIR)
    contacts = load_json(DATA_FILE, [])
    print(f"\n🚀 Job Search Dashboard Server")
    print(f"   URL:      http://localhost:{PORT}")
    print(f"   Contacts: {len(contacts)} loaded from contacts.json")
    print(f"   API:      http://localhost:{PORT}/api/contacts")
    print(f"\n   Press Ctrl+C to stop.\n")

    host = os.getenv("SERVER_HOST", "0.0.0.0")
    server = HTTPServer((host, PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
