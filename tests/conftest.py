import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="dre_test_")
os.environ.setdefault("DRE_DB_URL", f"sqlite:///{_tmp_dir}/test.db")
os.environ.setdefault("DRE_DATA_DIR", _tmp_dir)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
