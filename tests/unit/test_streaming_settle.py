import pytest
import json
import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock
from app.domain.streaming import stream_with_settle

@pytest.mark.asyncio
async def test_stream_with_settle_success():
    # Mock stream generator
    async def mock_stream():
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"hello\"}}]}"
        yield "data: {\"choices\": [{\"delta\": {\"content\": \" world\"}}]}"
        yield "data: [DONE]"

    usage_manager = AsyncMock()
    
    # Run wrapper
    lines = []
    async for line in stream_with_settle(
        stream_gen=mock_stream(),
        usage_manager=usage_manager,
        request_id="req-1",
        team_id="team-1",
        key_id="key-1",
        org_id="org-1",
        model_group_id="gpt-4",
        provider="openai",
        estimate_usd=Decimal("0.01"),
        start_time=1000.0,
        initial_prompt_tokens=10
    ):
        lines.append(line)

    assert len(lines) == 3
    assert "hello" in lines[0]
    
    # Verify settlement called
    usage_manager.record_event.assert_called_once()
    args = usage_manager.record_event.call_args.kwargs
    assert args["request_id"] == "req-1"
    assert args["input_tokens"] == 10
    assert args["output_tokens"] == 2 # "hello" and " world" chunks
    assert args["status"] == "success"

@pytest.mark.asyncio
async def test_stream_with_settle_provider_usage():
    # Mock stream generator with explicit usage in last chunk
    async def mock_stream():
        yield "data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}}]}"
        yield "data: {\"usage\": {\"prompt_tokens\": 15, \"completion_tokens\": 5}}"
        yield "data: [DONE]"

    usage_manager = AsyncMock()
    
    async for _ in stream_with_settle(
        stream_gen=mock_stream(),
        usage_manager=usage_manager,
        request_id="req-2",
        team_id="team-1",
        key_id="key-1",
        org_id="org-1",
        model_group_id="gpt-4",
        provider="openai",
        estimate_usd=Decimal("0.01"),
        start_time=1000.0,
        initial_prompt_tokens=10
    ):
        pass

    # Verify settlement used provider reported values
    args = usage_manager.record_event.call_args.kwargs
    assert args["input_tokens"] == 15
    assert args["output_tokens"] == 5
    assert args["token_count_source"] == "provider_reported"
