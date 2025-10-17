from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Any


def configure_logging(cfg: Dict[str, Any] | None = None) -> None:
    """Centralized logging setup. Idempotent and safe for dry-run.

    Args:
        cfg: optional dict with keys: level, file_path, file
    """
    cfg = cfg or {}
    level = (cfg.get("level") or os.getenv("LOG_LEVEL", "INFO")).upper()
    log_dir = cfg.get("file_path") or "logs"
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # Idempotent: if handlers already exist, assume logging configured
    if root.handlers:
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    file_path = cfg.get("file") or f"{log_dir}/app.log"
    try:
        fh = logging.FileHandler(file_path)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        # Fail-safe: if we cannot create file handler, continue with console only
        logging.getLogger(__name__).warning("Could not create file handler for logging; continuing with console only")

    try:
        root.setLevel(getattr(logging, level, logging.INFO))
    except Exception:
        root.setLevel(logging.INFO)
