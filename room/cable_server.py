"""
cable_server.py — threaded HTTP server for the cable playground.

Serves files from the current working directory and accepts POST /save with
a JSON body, which it writes to `state_file`.  Disables caching for any
*.json so a reload always sees the latest layout.

Also accepts POST /diag — plain text from the browser's arrow-field
diagnostic — and prints it to the terminal between separator rules, so
the diagnostic lives in the same scrollback as the rest of the Python
output instead of only in the browser's devtools console.  The POST
returns 200 immediately; the browser does not wait for the print.
"""

import json
import os
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def serve(port=8765,
          open_browser=True,
          html_file="cable_playground.html",
          state_file="room_layout.json"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def do_POST(self):
            if self.path == "/save":
                self._handle_save()
            elif self.path == "/diag":
                self._handle_diag()
            else:
                self.send_response(404)
                self.end_headers()

        # ---- POST /save ------------------------------------------------
        def _handle_save(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                data = json.loads(body)
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                n = (len(data.get("floorGrids", [])) +
                     len(data.get("wallGrids", [])) +
                     len(data.get("floorCables", [])) +
                     len(data.get("wallCables", [])))
                print(f"  saved {n} item(s) to {state_file}", flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": str(e)}).encode("utf-8"))

        # ---- POST /diag ------------------------------------------------
        # Plain text from the browser's arrow diagnostic.  Printed
        # between two rules so a long dump is easy to spot in the
        # scrollback.  flush=True on every line so a redirected stdout
        # (tee, nohup, >) still shows output live.
        def _handle_diag(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                text = body.decode("utf-8", errors="replace")
                rule = "=" * 72
                print("",      flush=True)
                print(rule,    flush=True)
                print(" ARROW DIAGNOSTIC", flush=True)
                print(rule,    flush=True)
                print(text,    flush=True)
                print(rule,    flush=True)
                print("",      flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": str(e)}).encode("utf-8"))

        def end_headers(self):
            if self.path.endswith(".json"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
            pass

    httpd = None
    used_port = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            used_port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not find a free port in [{port}, {port+20}).")
        return

    url = f"http://127.0.0.1:{used_port}/{html_file}"
    print(f"Serving {os.getcwd()}")
    print(f"  → open  {url}")
    print(f"  (state is saved to ./{state_file})")
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
