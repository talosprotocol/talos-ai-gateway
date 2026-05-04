import json
import time
import logging
from typing import AsyncGenerator
from decimal import Decimal
from app.domain.usage.manager import UsageManager

logger = logging.getLogger(__name__)

async def stream_with_settle(
    stream_gen: AsyncGenerator[str, None],
    usage_manager: UsageManager,
    request_id: str,
    team_id: str,
    key_id: str,
    org_id: str,
    model_group_id: str,
    provider: str,
    estimate_usd: Decimal,
    start_time: float,
    initial_prompt_tokens: int = 0
) -> AsyncGenerator[str, None]:
    """
    Wraps an upstream SSE stream, tracks tokens, and settles budget on completion.
    This implements "settle-on-end" semantics for AI streaming.
    """
    prompt_tokens = initial_prompt_tokens
    completion_tokens = 0
    status = "success"
    
    try:
        async for line in stream_gen:
            # Re-yield the raw SSE line
            yield f"{line}\n\n"
            
            # SSE line parsing: "data: {...}"
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                
                try:
                    data = json.loads(data_str)
                    
                    # 1. Check for provider-reported usage (OpenAI/Azure style)
                    usage = data.get("usage")
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                    else:
                        # 2. Heuristic: count completion chunks as tokens if no provider usage
                        # Most streaming providers yield 1 chunk per 1-2 tokens.
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                # Incremental count (very crude, but better than zero)
                                completion_tokens += 1
                except (json.JSONDecodeError, KeyError, IndexError):
                    # Swallow parsing errors during streaming to avoid breaking the client
                    pass

    except Exception as e:
        logger.error(f"Error during streaming for request {request_id}: {e}")
        status = "error"
        # We don't re-raise here as we want the finally block to settle the budget,
        # but the client will see a broken stream anyway.
        # Actually, re-raising is probably better for FastAPI to handle the error.
        raise
    finally:
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Phase 15: Final Settlement
        # We call record_event which handles cost calculation and budget settlement (idempotent).
        try:
            await usage_manager.record_event(
                request_id=request_id,
                team_id=team_id,
                key_id=key_id,
                org_id=org_id,
                surface="llm",
                target=model_group_id,
                provider=provider,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                latency_ms=latency_ms,
                status=status,
                token_count_source="provider_reported" if prompt_tokens > initial_prompt_tokens else "estimated",
                estimate_usd=estimate_usd
            )
            logger.info(f"Stream settled for {request_id}: {prompt_tokens} in, {completion_tokens} out, {latency_ms}ms")
        except Exception as e:
            logger.error(f"Failed to settle stream for {request_id}: {e}")
