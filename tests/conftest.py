import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="radardesk-tests-"))
os.environ.setdefault("ADSB_STATS_ENABLED", "true")
os.environ.setdefault("ADSB_STATS_DB_PATH", str(_tmp / "stats.db"))
os.environ.setdefault("ADSB_RUNTIME_CONFIG_FILE", str(_tmp / "runtime.json"))
os.environ.setdefault("ADSB_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("ADSB_ADMIN_USER", "test-admin")
os.environ.setdefault("ADSB_ADMIN_PASSWORD", "test-password")
os.environ.setdefault("ADSB_STATS_BACKUP_KEEP", "3")
os.environ.setdefault("ADSB_LOCAL_STATS_CACHE_S", "0")
