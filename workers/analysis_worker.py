"""Analysis worker — Phase 4 placeholder.

Responsibilities (to be implemented in Phase 4):
  - Consume messages from the analysis queue (Redis list or similar).
  - Run deterministic security checks (SPF/DKIM/DMARC, URL analysis,
    attachment risk, display-name mismatch).
  - Submit structured analysis requests to the LLM worker.
  - Persist AnalysisResult records and per-dimension scores to the database.
  - Feed results to the policy engine.
  - Mark messages with cached verdicts for fast IMAP proxy responses.

This worker should run as a separate process in production but can be
started as a background task in development for simplicity.
"""

import asyncio
import logging

log = logging.getLogger(__name__)


async def run() -> None:
    """Entry point for the analysis worker process.

    Phase 4 scaffold — no-op loop until implemented.
    """
    log.info("analysis_worker: starting (Phase 4 scaffold — no messages processed)")
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        log.info("analysis_worker: shutting down")


if __name__ == "__main__":
    asyncio.run(run())
