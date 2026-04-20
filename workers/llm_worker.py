"""LLM worker — Phase 4 placeholder.

Responsibilities (to be implemented in Phase 4):
  - Build structured prompts from normalised message data and deterministic evidence.
  - Call the local Ollama endpoint (never a cloud API).
  - Parse and validate structured JSON output against the ManipulationDimension schema.
  - Retry once with strict schema guidance if output is malformed.
  - Degrade gracefully: emit a partial verdict with confidence=0 and
    mark it as DEGRADED if Ollama is unavailable or output is unparseable.
  - Track prompt versions for auditability.

All inference MUST remain within the local deployment boundary.
"""

import asyncio
import logging

log = logging.getLogger(__name__)


async def run() -> None:
    """Entry point for the LLM worker process.

    Phase 4 scaffold — no-op loop until implemented.
    """
    log.info("llm_worker: starting (Phase 4 scaffold — no inference performed)")
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        log.info("llm_worker: shutting down")


if __name__ == "__main__":
    asyncio.run(run())
