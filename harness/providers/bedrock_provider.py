"""BedrockProvider — wraps AWS Bedrock SDK for LLM access."""

from __future__ import annotations

from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


class BedrockProvider(BaseProvider):
    """AWS Bedrock provider.

    Wraps the AWS Bedrock Runtime SDK for chat completions with support for:
    - Claude models via Bedrock
    - Streaming responses
    - Tool use (model-dependent)

    Requires AWS credentials configured (env vars, profile, or IAM role).
    """

    def __init__(
        self,
        region: str = "us-east-1",
        model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        max_tokens: int = 4096,
        bearer_token: str = "",
    ) -> None:
        self._region = region
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._bearer_token = bearer_token
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the Bedrock Runtime client.

        Auth priority:
        1. Bearer token (AWS_BEARER_TOKEN_BEDROCK) — long-term API key for dev
        2. Standard AWS credentials (IAM role, env vars, profile) — for production
        """
        if self._client is None:
            try:
                import boto3
                import os

                token = self._bearer_token or os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
                if token:
                    # Use bearer token auth (Bedrock long-term API key)
                    from botocore.config import Config
                    self._client = boto3.client(
                        "bedrock-runtime",
                        region_name=self._region,
                        config=Config(signature_version="bearer"),
                        aws_access_key_id="",
                        aws_secret_access_key="",
                        aws_session_token=token,
                    )
                else:
                    # Standard IAM auth (env vars, profile, role)
                    self._client = boto3.client(
                        "bedrock-runtime", region_name=self._region
                    )
            except ImportError as exc:
                raise ImportError(
                    "boto3 package not installed. Install with: pip install boto3"
                ) from exc
        return self._client

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat completion via AWS Bedrock."""
        import json

        client = self._get_client()
        model_id = kwargs.pop("model", self._model_id)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)
        tools = kwargs.pop("tools", None)

        # Separate system message
        system_prompts = []
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompts.append({"text": msg.get("content", "")})
            else:
                chat_messages.append({
                    "role": msg.get("role", "user"),
                    "content": [{"text": msg.get("content", "")}],
                })

        converse_kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": chat_messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system_prompts:
            converse_kwargs["system"] = system_prompts
        if tools:
            converse_kwargs["toolConfig"] = {"tools": tools}
        converse_kwargs.update(kwargs)

        response = client.converse(**converse_kwargs)

        # Parse response
        content = ""
        tool_calls = []
        output = response.get("output", {})
        message = output.get("message", {})
        for block in message.get("content", []):
            if "text" in block:
                content += block["text"]
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append({
                    "id": tu.get("toolUseId", ""),
                    "name": tu.get("name", ""),
                    "arguments": tu.get("input", {}),
                })

        usage = response.get("usage", {})
        tokens_used = usage.get("inputTokens", 0) + usage.get("outputTokens", 0)

        return LLMResponse(
            content=content,
            tokens_used=tokens_used,
            tool_calls=tool_calls,
            model=model_id,
            stop_reason=response.get("stopReason"),
            raw={"responseMetadata": response.get("ResponseMetadata", {})},
        )

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion from AWS Bedrock."""
        client = self._get_client()
        model_id = kwargs.pop("model", self._model_id)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)

        system_prompts = []
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompts.append({"text": msg.get("content", "")})
            else:
                chat_messages.append({
                    "role": msg.get("role", "user"),
                    "content": [{"text": msg.get("content", "")}],
                })

        converse_kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": chat_messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system_prompts:
            converse_kwargs["system"] = system_prompts
        converse_kwargs.update(kwargs)

        response = client.converse_stream(**converse_kwargs)
        stream = response.get("stream", [])
        for event in stream:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                text = delta.get("text", "")
                yield LLMChunk(content=text, delta=text)
            elif "messageStop" in event:
                yield LLMChunk(is_final=True)
        yield LLMChunk(is_final=True)

    @property
    def provider_name(self) -> str:
        return "bedrock"

    @property
    def default_model(self) -> str:
        return self._model_id
