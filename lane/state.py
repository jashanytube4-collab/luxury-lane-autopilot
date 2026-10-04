"""Persistent state, stored as JSON in state/ and committed back to the repo by the workflow.

catalog.json       every clip ever discovered, with its status (new/used/rejected/failed/duplicate)
fingerprints.json  visual fingerprints of used clips, so re-posts of the same footage by another account are caught
cursors.json       where the Instagram history backfill stopped for each account
days/<date>.json   the posting plan for one day: slot times and what was uploaded into each
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, STATE_DIR

log = logging.getLogger("lane.state")


def _read(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    def __init__(self, root: Path = STATE_DIR) -> None:
        self.root = root
        self.catalog: dict[str, dict] = _read(root / "catalog.json", {})
        self.fingerprints: dict[str, list[int]] = _read(root / "fingerprints.json", {})
        self.cursors: dict[str, dict] = _read(root / "cursors.json", {})

    # ---- catalog -------------------------------------------------------
    def add_candidate(self, key: str, record: dict) -> bool:
        if key in self.catalog:
            # Refresh engagement numbers but never touch status.
            self.catalog[key]["likes"] = record.get("likes", self.catalog[key].get("likes", 0))
            return False
        self.catalog[key] = {**record, "status": "new", "found_at": now_iso()}
        return True

    def mark(self, key: str, status: str, **extra) -> None:
        rec = self.catalog.setdefault(key, {})
        rec.update(extra)
        rec["status"] = status
        rec["updated_at"] = now_iso()

    def candidates(self) -> list[tuple[str, dict]]:
        return [(k, r) for k, r in self.catalog.items() if r.get("status") == "new"]

    # ---- days ------------------------------------------------------------
    def day_path(self, date: str) -> Path:
        return self.root / "days" / f"{date}.json"

    def load_day(self, date: str) -> dict | None:
        return _read(self.day_path(date), None)

    def save_day(self, date: str, plan: dict) -> None:
        _write(self.day_path(date), plan)

    # ---- persistence -------------------------------------------------------
    def save(self) -> None:
        _write(self.root / "catalog.json", self.catalog)
        _write(self.root / "fingerprints.json", self.fingerprints)
        _write(self.root / "cursors.json", self.cursors)

    def checkpoint(self, message: str) -> None:
        """Save, and in CI commit + push immediately so a crash can never cause a repeat upload."""
        self.save()
        if os.environ.get("LANE_GIT_PUSH") != "1":
            return
        try:
            subprocess.run(["git", "add", "state"], cwd=ROOT, check=True)
            diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
            if diff.returncode == 0:
                return
            subprocess.run(["git", "commit", "-q", "-m", message], cwd=ROOT, check=True)
            for _ in range(3):
                if subprocess.run(["git", "push", "-q"], cwd=ROOT).returncode == 0:
                    return
                subprocess.run(["git", "pull", "-q", "--rebase"], cwd=ROOT)
            log.error("git push failed three times; state is saved locally and will be pushed later")
        except subprocess.CalledProcessError as e:
            log.error("git checkpoint failed: %s", e)
