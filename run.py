"""
Application entry point.

Usage:
    python run.py          # runs in development mode
    flask run              # alternative using Flask CLI
"""

import os
from app import create_app

# Select config from FLASK_ENV or default to development
app = create_app(os.environ.get("FLASK_ENV", "development"))

if __name__ == "__main__":
    # Debug (auto-reload and the in-browser debugger) follows the config: on for development,
    # off for production. For real deployments use gunicorn / wsgi.py instead of this file.
    app.run(debug=app.config["DEBUG"], port=int(os.environ.get("PORT", 5000)))
