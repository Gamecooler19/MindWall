"""Analysis domain — deterministic security checks and LLM orchestration.

Phase 4 will implement:
  - Deterministic checks (SPF/DKIM/DMARC, display-name mismatch, URL analysis,
    attachment risk, credential-capture patterns)
  - Ollama client integration (local Llama 3.1 8B)
  - Structured LLM prompt building
  - JSON output parsing and validation against ManipulationDimension schema
  - Combined feature vector for the policy engine
  - Graceful degradation when Ollama is unavailable
"""
