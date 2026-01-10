# Talos AI Gateway

Unified LLM Inference + MCP Tool Gateway with RBAC and Audit.

## Features

- **LLM Inference**: OpenAI-compatible `/v1/chat/completions`
- **MCP Discovery**: Dynamic tool discovery and invocation
- **RBAC**: Deny-by-default admin control plane
- **Virtual Keys**: Unified auth for data plane
- **Rate Limiting**: Token bucket per key/team
- **Audit**: Event emission for all operations

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

| Variable       | Default                                         | Description    |
| -------------- | ----------------------------------------------- | -------------- |
| `DATABASE_URL` | `postgresql://talos:talos@localhost:5432/talos` | Postgres       |
| `REDIS_URL`    | `redis://localhost:6379/0`                      | Redis          |
| `MASTER_KEY`   | (required in prod)                              | Encryption key |

## Architecture

```
app/
├── api/
│   ├── public_ai/     # /v1/*
│   ├── public_mcp/    # /mcp/v1/*
│   └── admin/         # /admin/v1/*
├── middleware/        # Auth, Rate Limit, Redaction
├── domain/           # Business logic
└── adapters/         # External integrations
```

## License

Apache 2.0
