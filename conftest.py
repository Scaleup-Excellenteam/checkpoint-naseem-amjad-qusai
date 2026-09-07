"""Makes the repository root importable so tests can do
`from server.server import app`, whatever directory pytest is run from."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
