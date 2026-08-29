"""Per-run isolation for the test suite.

Must run before anything imports `src.config`, which reads the audit path once at
import time. Without this the suite shares one audit database across runs, and the
tamper-evidence tests fail on the second run against a chain the first run left behind.
"""
import os
import tempfile

os.environ["SENTINEL_AUDIT_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="sentinel-tests-"), "audit.db"
)
