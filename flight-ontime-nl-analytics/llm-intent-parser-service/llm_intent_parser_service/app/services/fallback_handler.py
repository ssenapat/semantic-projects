"""Model call + validation + bounded repair. An invalid output is never turned into a success."""
import logging
import os
import time

from ..errors import ParserError
from ..observability.metrics import Metrics
from ..schemas.request import IntentParserRequest
from .llm_client import LlmClient, LlmProviderError, LlmRateLimited
from .prompt_builder import SYSTEM_PROMPT, build_repair_message, build_user_message
from .response_validator import clarification_from_context, validate_outcome
from .structured_output_parser import outcome_json_schema, parse_outcome

log = logging.getLogger("intent_parser")


def parse_intent(req: IntentParserRequest, client: LlmClient, metrics: Metrics, *, max_repairs: int,
                 timeout_s: float):
    """Returns a validated outcome or raises ParserError (429/502/422 invalid_model_output)."""
    schema = outcome_json_schema()
    base_user = build_user_message(req.question, req.conversation_context, req.semantic_context)
    user = base_user
    opts = req.parser_options
    errors: list[str] = []
    for attempt in range(max_repairs + 1):
        t0 = time.perf_counter()
        try:
            raw = client.complete_json(system=SYSTEM_PROMPT, user=user, json_schema=schema,
                                       temperature=opts.temperature, max_output_tokens=opts.max_output_tokens,
                                       timeout_s=timeout_s)
        except LlmRateLimited:
            raise ParserError("rate_limited", "The language model provider is rate limiting requests.") from None
        except LlmProviderError:
            raise ParserError("llm_provider_failure", "The language model provider failed or timed out.") from None
        finally:
            metrics.set("intent_parser_llm_latency_ms", (time.perf_counter() - t0) * 1000)
        outcome, errors = parse_outcome(raw)
        if outcome is not None:
            result = validate_outcome(outcome, req.semantic_context, req.question, opts)
            if result.leakage:
                metrics.inc("intent_parser_sql_leakage_rejected_total")
            if result.deterministic is not None:
                return result.deterministic
            if result.ok:
                return outcome
            errors = result.errors
        # Validator messages only by default; raw model output may echo user text, so it is opt-in (debug only).
        log.warning("intent_parse attempt %d/%d failed validation: error_count=%d errors=%.500s raw_len=%d%s",
                    attempt + 1, max_repairs + 1, len(errors), errors, len(str(raw or "")),
                    f" raw={str(raw)[:500]}" if os.environ.get("INTENT_DEBUG_RAW_OUTPUT") == "1" else "")
        if attempt < max_repairs:
            metrics.inc("intent_parser_model_retry_total")
            user = build_repair_message(base_user, errors)
    if opts.allow_clarification:
        # The model could not produce a valid answer, but the question itself matches a clarify trigger:
        # ask the user rather than reporting a service failure.
        clar = clarification_from_context(req.question, req.semantic_context)
        if clar is not None:
            return clar
    metrics.inc("intent_parser_invalid_output_total")
    raise ParserError("invalid_model_output", "The model output failed validation after the allowed repair attempts.")
