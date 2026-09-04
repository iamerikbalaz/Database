import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger("reawote.worker")


@dataclass(frozen=True)
class WorkerSettings:
    poll_interval_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        value = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "30"))
        if value <= 0:
            raise ValueError("WORKER_POLL_INTERVAL_SECONDS must be greater than zero")
        return cls(poll_interval_seconds=value)


def run_once() -> str:
    """Execute one empty cycle until real background jobs are introduced."""
    logger.info("Worker cycle completed; no job integrations are configured")
    return "idle"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = WorkerSettings.from_environment()
    logger.info("REAWOTE worker started")

    while True:
        run_once()
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
