"""Makes the repository root importable so tests can do
`from server.server import app`, whatever directory pytest is run from.

Also redirects the server's database before it is ever imported, so running
the suite never creates or touches the developer's server/accounts.sqlite3.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault(
    "TSPO_DATABASE_PATH",
    os.path.join(tempfile.mkdtemp(prefix="tspo-tests-"), "tspo.sqlite3"),
)
