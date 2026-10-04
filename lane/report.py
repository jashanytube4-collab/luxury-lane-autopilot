"""Logging, the end-of-run summary, and optional Telegram alerts."""
from __future__ import annotations

import logging
import os
import sys

import requests

log = logging.getLogger("lane")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "googleapiclient", "urllib3", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RunReport:
    """Collects what happened in a run; written to the GitHub job summary and sent to Telegram."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.problems: list[str] = []

    def note(self, msg: str) -> None:
        log.info(msg)
        self.lines.append(msg)

    def problem(self, msg: str) -> None:
        log.error(msg)
        self.problems.append(msg)

    def render(self) -> str:
        out = ["## The Luxury Lane — run report", ""]
        out += [f"- {l}" for l in self.lines]
        if self.problems:
            out += ["", "### Needs attention", ""] + [f"- ⚠️ {p}" for p in self.problems]
        return "\n".join(out) + "\n"

    def publish(self, bot_token: str, chat_id: str) -> None:
        text = self.render()
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(text)
        print(text)
        # Telegram only when something needs attention, so the phone stays quiet on good days.
        if bot_token and chat_id and self.problems:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text[:4000]},
                    timeout=20,
                )
            except requests.RequestException as e:
                log.warning("Telegram alert failed: %s", e)
