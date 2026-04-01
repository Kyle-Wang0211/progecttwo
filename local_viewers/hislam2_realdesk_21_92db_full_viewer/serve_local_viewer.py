#!/usr/bin/env python3
import http.server
import os
import socketserver
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def choose_port(start=8765, end=8799):
    for port in range(start, end + 1):
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
            return httpd, port
        except OSError:
            continue
    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    return httpd, httpd.server_address[1]


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    httpd, port = choose_port()
    (root / "viewer_port.txt").write_text(str(port), encoding="utf-8")
    print(f"viewer running on http://127.0.0.1:{port}/index.html", flush=True)
    try:
        httpd.serve_forever()
    finally:
        try:
            (root / "viewer_port.txt").unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
