"""Manual dashboard regeneration — useful for UI testing without waiting 30 min."""
import sys, os
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

from executor import Executor
from position_manager import load_state
from dashboard import build_dashboard
from config import DRY_RUN

ex = Executor(dry_run=DRY_RUN)
st = load_state()
print("signal_status keys:", list(st.get("signal_status", {}).keys()))
build_dashboard(ex, st)
print("Dashboard regenerated.")
