from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DB_URL = os.environ.get("DRE_DB_URL", "sqlite:///data/dre.db")
DATA_DIR = Path(os.environ.get("DRE_DATA_DIR", "data"))
RUNS_DIR = DATA_DIR / "runs"

DETECT_MODEL = os.environ.get("DRE_DETECT_MODEL", "claude-sonnet-5")
REASONING_MODEL = os.environ.get("DRE_REASONING_MODEL", "claude-sonnet-5")
