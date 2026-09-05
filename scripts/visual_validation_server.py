"""Servidor descartável para inspeção visual; nunca abre data/erp.sqlite3."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn
from app import create_app

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="visual_", dir=ROOT / "artifacts") as temporary:
        app = create_app(database_url=f"sqlite+pysqlite:///{(Path(temporary) / 'visual.db').as_posix()}", credentials={"adm": "adm"})
        try:
            uvicorn.run(app, host="127.0.0.1", port=8876, log_level="warning")
        finally:
            app.state.engine.dispose()
