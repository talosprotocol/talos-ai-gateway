"""Builders for the A2A v1 discovery surface."""

from fastapi import Request

from app.api.a2a_v1.models import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    SecurityScheme,
)


PROJECT_NAME = "Talos AI Gateway"
PROJECT_DESCRIPTION = "Talos-secured AI agent gateway with MCP and audit-backed orchestration."
PROJECT_VERSION = "0.1.0"
PROJECT_DOCS_URL = "https://docs.talosprotocol.com"
PROJECT_PROVIDER_URL = "https://talosprotocol.com"
TALOS_ATTESTATION_EXTENSION = "https://talosprotocol.com/extensions/a2a/attestation/v1"
TALOS_SECURE_CHANNELS_EXTENSION = "https://talosprotocol.com/extensions/a2a/secure-channels/v1"
TALOS_COMPAT_EXTENSION = "https://talosprotocol.com/extensions/a2a/compat-jsonrpc/v0"


def build_agent_card(
    request: Request,
    *,
    include_compat_extension: bool,
    include_extended_details: bool = False,
) -> dict:
    extensions = [
        AgentExtension(
            uri=TALOS_ATTESTATION_EXTENSION,
            description="Talos attestation headers and request verification metadata.",
        ),
        AgentExtension(
            uri=TALOS_SECURE_CHANNELS_EXTENSION,
            description="Talos encrypted session and frame transport layered on top of A2A.",
        ),
    ]
    if include_compat_extension:
        extensions.append(
            AgentExtension(
                uri=TALOS_COMPAT_EXTENSION,
                description="Legacy Talos A2A JSON-RPC compatibility endpoint during migration.",
            )
        )

    card = AgentCard(
        name=PROJECT_NAME,
        description=PROJECT_DESCRIPTION,
        version=PROJECT_VERSION,
        provider=AgentProvider(
            organization="Talos Protocol",
            url=PROJECT_PROVIDER_URL,
        ),
        documentationUrl=PROJECT_DOCS_URL,
        supportedInterfaces=[
            AgentInterface(
                url=str(request.url_for("a2a_v1_rpc")),
                protocolBinding="JSONRPC",
                protocolVersion="1.0",
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            pushNotifications=True,
            extensions=extensions,
            extendedAgentCard=True,
        ),
        skills=[
            AgentSkill(
                id="talos-tool-orchestration",
                name="Tool Orchestration",
                description="Execute MCP-backed tasks with Talos audit and policy enforcement.",
                tags=["mcp", "audit", "policy"],
                inputModes=["text"],
                outputModes=["text"],
                examples=[
                    "Route a tenant-safe MCP tool call and return the audited result.",
                ]
                if include_extended_details
                else [],
                securityRequirements=[{"bearerAuth": []}],
            ),
            AgentSkill(
                id="talos-secure-agent-coordination",
                name="Secure Agent Coordination",
                description="Coordinate multi-agent workflows with Talos attestation and secure-channel extensions.",
                tags=["security", "attestation", "a2a"],
                inputModes=["text"],
                outputModes=["text"],
                examples=[
                    "Create an audited handoff between two agents using Talos secure-channel extensions.",
                ]
                if include_extended_details
                else [],
                securityRequirements=[{"bearerAuth": []}],
            ),
            *(
                [
                    AgentSkill(
                        id="talos-governed-admin-operations",
                        name="Governed Admin Operations",
                        description="Operate Talos admin and governance surfaces with RBAC and audit constraints.",
                        tags=["admin", "governance", "rbac"],
                        inputModes=["text"],
                        outputModes=["text"],
                        examples=[
                            "List available MCP servers for the current tenant and explain policy constraints.",
                        ],
                        securityRequirements=[{"bearerAuth": []}],
                    )
                ]
                if include_extended_details
                else []
            ),
        ],
        securitySchemes={
            "bearerAuth": SecurityScheme(
                type="http",
                scheme="bearer",
                bearerFormat="API key",
            )
        },
        securityRequirements=[{"bearerAuth": []}],
    )
    return card.model_dump(exclude_none=True)
