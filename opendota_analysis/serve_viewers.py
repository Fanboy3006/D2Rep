#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_viewers.py - host the viewer HTML over LAN so a phone can open them in
a real browser by URL.

Why: opening a transferred .html on a phone goes through the OS document
preview (iOS Quick Look / Android file viewers), which does not run JS/Canvas
fully -> the map canvas renders black. Opening via http:// in a real browser
works. This script serves dist/ over 0.0.0.0 and builds an index page listing
every viewer for tap-through.

Usage (from repo root):
    python opendota_analysis/serve_viewers.py [port]     # default 8017
Then on the phone (same WiFi), open:
    http://<printed-ip>:<port>/     (index)  or /viewer_<id>.html
"""
import http.server
import os
import socket
import socketserver
import sys

DIST = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8017


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def build_index():
    import glob
    import html as h
    files = sorted(glob.glob(os.path.join(DIST, "viewer_*.html")))
    files += sorted(glob.glob(os.path.join(DIST, "ward_heatmap_explorer.html")))
    files += sorted(glob.glob(os.path.join(DIST, "mobile_test.html")))
    rows = "".join(
        '<li><a href="%s">%s (%d KB)</a> <span style="color:#888">%s</span></li>'
        % (h.escape(os.path.basename(f)), h.escape(os.path.basename(f)),
           os.path.getsize(f) // 1024,
           "复盘" if "viewer_" in os.path.basename(f)
           else ("热力图" if "heatmap" in os.path.basename(f) else "自检"))
        for f in files
    )
    page = ('<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Dota2 复盘查看器</title>'
            '<body style="font-family:system-ui,sans-serif;background:#101216;color:#dfe3ea;'
            'margin:0;padding:20px">'
            '<h2 style="margin-top:0">Dota2 复盘查看器</h2>'
            '<p style="color:#9aa2b2">点击下面的文件即可在浏览器中打开（手机请用真浏览器访问本页）。</p>'
            '<ul style="line-height:2">%s</ul></body></html>' % rows)
    with open(os.path.join(DIST, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def main():
    build_index()
    os.chdir(DIST)
    ip = lan_ip()
    url = "http://%s:%d/" % (ip, PORT)
    with socketserver.TCPServer(("0.0.0.0", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
        print("Serving %s" % DIST)
        print("Phone (same WiFi) open: %s" % url)
        print("example: %sviewer_8822238357.html" % url)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
