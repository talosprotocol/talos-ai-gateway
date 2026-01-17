# Talos AI Gateway

Unified LLM Inference + MCP Tool Gateway with RBAC and Audit.

## Features

- **Multi-Region Support**: Single-primary, multi-region read with automatic replica fallback
- **Advanced Upstreams**: Native support for Ollama (local/cloud) with API key rotation
- **LLM Inference**: OpenAI-compatible `/v1/chat/completions`
- **MCP Discovery**: Dynamic tool discovery and invocation
- **RBAC**: Deny-by-default admin control plane with wildcard scope support
- **Virtual Keys**: Unified auth for data plane
- **TGA Capabilities**: Cryptographically signed tool authorization (JWS/EdDSA)
- **Secrets Encryption**: AES-GCM envelope encryption for all upstream credentials
- **Rate Limiting**: Distributed token bucket (Redis) per key/team
- **Audit**: Deterministic hash-chained event logs for all operations

## Quick Start

```bash
# Start infrastructure
make up

# Run development server
make dev

# Run tests
make test
```

## API Endpoints

### Data Plane (Virtual Key Auth)

| Endpoint                                      | Description            |
| --------------------------------------------- | ---------------------- |
| `POST /v1/chat/completions`                   | OpenAI-compatible chat |
| `GET /v1/models`                              | List allowed models    |
| `GET /mcp/v1/servers`                         | List MCP servers       |
| `GET /mcp/v1/servers/{id}/tools`              | List tools             |
| `POST /mcp/v1/servers/{id}/tools/{name}:call` | Invoke tool            |

### Admin Plane (RBAC Auth)

| Endpoint                                | Description       |
| --------------------------------------- | ----------------- |
| `GET /admin/v1/llm/upstreams`           | List upstreams    |
| `GET /admin/v1/llm/model_groups`        | List model groups |
| `GET /admin/v1/mcp/servers`             | List MCP registry |
| `POST /admin/v1/mcp/policies/{team_id}` | Set team policy   |

## Configuration

Environment variables:

| Variable                    | Default                                         | Description                                      |
| --------------------------- | ----------------------------------------------- | ------------------------------------------------ |
| `DATABASE_WRITE_URL`        | `postgresql://talos:talos@localhost:5432/talos` | Primary DB for mutations                         |
| `DATABASE_READ_URL`         | (defaults to write URL)                         | Local replica for eventual consistency reads     |
| `REGION_ID`                 | `local`                                         | Deployment region identifier                     |
| `REDIS_URL`                 | `redis://localhost:6379/0`                      | Redis for rate limiting and caching              |
| `TALOS_MASTER_KEY`          | (required in prod)                              | AEAD Master Key for Secret Storage (32-byte hex) |
| `TALOS_KEK_ID`              | `v1`                                            | Key version for rotation                         |
| `TGA_SUPERVISOR_PUBLIC_KEY` | (optional)                                      | Ed25519 public key for TGA capability validation |

## Architecture

```
app/
├── api/
│   ├── public_ai/     # /v1/*
│   ├── public_mcp/    # /mcp/v1/*
│   ├── a2a/           # /a2a/* (Agent-to-Agent)
│   └── admin/         # /admin/v1/*
├── middleware/        # Auth, Rate Limit, Redaction
├── domain/
│   ├── tga/           # TGA capability validation
│   ├── secrets/       # Secret management
│   └── ...            # Other business logic
└── adapters/         # External integrations
```

## License

Apache 2.0
