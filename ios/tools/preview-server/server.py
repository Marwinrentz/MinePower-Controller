#!/usr/bin/env python3
"""Kleiner Vorschau-Server: Screenshots in Geräterahmen mit automatischem Neuladen.

Nur Standardbibliothek. Lauscht ausschließlich auf 127.0.0.1.

    python3 tools/preview-server/server.py --shots preview/screenshots --port 8765
"""
import argparse
import hashlib
import http.server
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def make_handler(shots: pathlib.Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(HERE), **kwargs)

        def log_message(self, *args):  # ruhig bleiben
            pass

        def do_GET(self):
            if self.path.startswith("/api/shots"):
                files = sorted(p.name for p in shots.glob("*.png")) if shots.exists() else []
                stamp = hashlib.sha1("".join(f"{f}{(shots / f).stat().st_mtime}" for f in files).encode()).hexdigest()
                body = json.dumps({"files": files, "version": stamp}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/shots/"):
                name = pathlib.Path(self.path.split("?")[0]).name
                target = shots / name
                if target.is_file() and target.suffix == ".png":
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)
                return
            super().do_GET()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--shots", default="preview/screenshots")
    args = parser.parse_args()
    shots = pathlib.Path(args.shots).resolve()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(shots))
    print(f"Vorschau auf http://127.0.0.1:{args.port} (Bilder: {shots})")
    server.serve_forever()


if __name__ == "__main__":
    main()
