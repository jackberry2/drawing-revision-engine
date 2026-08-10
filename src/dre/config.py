import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_DRAWINGS_BUCKET = os.environ.get("DRE_DRAWINGS_BUCKET", "drawings")

DETECT_MODEL = os.environ.get("DRE_DETECT_MODEL", "claude-sonnet-5")
REASONING_MODEL = os.environ.get("DRE_REASONING_MODEL", "claude-sonnet-5")
