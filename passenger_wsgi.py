import os
import sys

# Add project root directory to Python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Ensure current working directory is the project folder
os.chdir(CURRENT_DIR)

from app.config import ensure_directories
from app.auth_mgr import init_auth
from app.web.api import app as fastapi_app

# Initialize directories & admin database on startup
ensure_directories()
try:
    init_auth()
except Exception:
    pass

# For cPanel Phusion Passenger:
# Adapt modern ASGI FastAPI application to WSGI
try:
    from a2wsgi import ASGIMiddleware
    application = ASGIMiddleware(fastapi_app)
except ImportError:
    application = fastapi_app
