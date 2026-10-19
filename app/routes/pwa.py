"""
Progressive Web App support: web manifest, service worker and offline page.

The service worker caches only public static assets and the offline page. It never
caches pages or API responses, so patient data is never stored on the device by us.
"""

import os

from flask import Blueprint, current_app, jsonify, make_response, render_template, send_from_directory

pwa_bp = Blueprint("pwa", __name__)


@pwa_bp.route("/manifest.webmanifest")
def manifest():
    resp = jsonify({
        "name": "MediTrack",
        "short_name": "MediTrack",
        "description": "Book appointments, manage visits and keep your health records in one place.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f3f7ff",
        "theme_color": "#2563EB",
        "icons": [
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/img/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@pwa_bp.route("/sw.js")
def service_worker():
    """Served from the site root so its scope covers the whole app."""
    resp = make_response(send_from_directory(
        os.path.join(current_app.root_path, "..", "static", "js"), "sw.js", mimetype="application/javascript",
    ))
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@pwa_bp.route("/offline")
def offline():
    return render_template("offline.html")
