"""CLI for Talos AI Gateway configuration."""
import click
import json
from pathlib import Path
from typing import Optional

from app.domain.router_ai import router as llm_router


@click.group()
def cli():
    """Talos AI Gateway CLI."""
    pass


@cli.group()
def agent():
    """Manage AI agents/providers."""
    pass


@agent.command("add")
@click.option("--id", required=True, help="Unique agent/upstream ID")
@click.option("--provider", required=True, 
              type=click.Choice(["openai", "azure", "anthropic", "google", "groq", "together", "mistral", "ollama", "custom"]),
              help="Provider type")
@click.option("--endpoint", required=True, help="API endpoint URL")
@click.option("--credentials", default="", help="Credentials reference (secret:NAME or env:VAR)")
@click.option("--tags", default="{}", help="JSON tags (e.g. '{\"region\":\"us\"}')")
@click.option("--enabled/--disabled", default=True, help="Enable/disable agent")
def add_agent(id: str, provider: str, endpoint: str, credentials: str, tags: str, enabled: bool):
    """Add a custom AI agent/upstream."""
    try:
        tags_dict = json.loads(tags)
    except json.JSONDecodeError:
        click.echo(f"Error: Invalid JSON for tags: {tags}", err=True)
        return
    
    agent_config = {
        "id": id,
        "provider": provider,
        "endpoint": endpoint,
        "credentials_ref": credentials,
        "tags": tags_dict,
        "enabled": enabled
    }
    
    llm_router.create_upstream(agent_config)
    click.echo(f"✓ Agent '{id}' added successfully")
    click.echo(json.dumps(agent_config, indent=2))


@agent.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def list_agents(fmt: str):
    """List all configured agents."""
    upstreams = llm_router.list_upstreams()
    
    if fmt == "json":
        click.echo(json.dumps(upstreams, indent=2))
    else:
        click.echo(f"\n{'ID':<20} {'Provider':<12} {'Endpoint':<40} {'Status':<10}")
        click.echo("-" * 85)
        for u in upstreams:
            status = "✓ Active" if u.get("enabled", True) else "✗ Disabled"
            endpoint = u.get("endpoint", "")[:38]
            click.echo(f"{u['id']:<20} {u.get('provider', 'unknown'):<12} {endpoint:<40} {status:<10}")


@agent.command("remove")
@click.argument("agent_id")
def remove_agent(agent_id: str):
    """Remove an agent by ID."""
    if agent_id in llm_router.UPSTREAMS:
        del llm_router.UPSTREAMS[agent_id]
        click.echo(f"✓ Agent '{agent_id}' removed")
    else:
        click.echo(f"Error: Agent '{agent_id}' not found", err=True)


@cli.group()
def model():
    """Manage model groups."""
    pass


@model.command("add")
@click.option("--id", required=True, help="Model group ID")
@click.option("--name", required=True, help="Display name")
@click.option("--upstream", required=True, help="Upstream ID to use")
@click.option("--model-name", required=True, help="Model name at the upstream")
@click.option("--weight", default=100, type=int, help="Routing weight (default: 100)")
@click.option("--fallback", default="", help="Comma-separated fallback group IDs")
def add_model(id: str, name: str, upstream: str, model_name: str, weight: int, fallback: str):
    """Add a model group."""
    fallback_groups = [f.strip() for f in fallback.split(",") if f.strip()] if fallback else []
    
    model_config = {
        "id": id,
        "name": name,
        "deployments": [
            {"upstream_id": upstream, "model_name": model_name, "weight": weight}
        ],
        "fallback_groups": fallback_groups
    }
    
    llm_router.create_model_group(model_config)
    click.echo(f"✓ Model group '{id}' added successfully")


@model.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def list_models(fmt: str):
    """List all model groups."""
    groups = llm_router.list_model_groups()
    
    if fmt == "json":
        click.echo(json.dumps(groups, indent=2))
    else:
        click.echo(f"\n{'ID':<20} {'Name':<35} {'Deployments':<15}")
        click.echo("-" * 75)
        for g in groups:
            deployments = len(g.get("deployments", []))
            click.echo(f"{g['id']:<20} {g.get('name', ''):<35} {deployments}")


@cli.command("load")
@click.argument("config_file", type=click.Path(exists=True))
def load_config(config_file: str):
    """Load configuration from a JSON file.
    
    Example config file:
    {
        "upstreams": [...],
        "model_groups": [...],
        "routing_policies": [...]
    }
    """
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    upstreams_added = 0
    models_added = 0
    
    # Load upstreams
    for upstream in config.get("upstreams", []):
        llm_router.create_upstream(upstream)
        upstreams_added += 1
    
    # Load model groups
    for group in config.get("model_groups", []):
        llm_router.create_model_group(group)
        models_added += 1
    
    click.echo(f"✓ Loaded {upstreams_added} upstreams, {models_added} model groups from {config_file}")


@cli.command("export")
@click.option("--output", "-o", type=click.Path(), help="Output file path")
def export_config(output: Optional[str]):
    """Export current configuration to JSON."""
    config = {
        "upstreams": llm_router.list_upstreams(),
        "model_groups": llm_router.list_model_groups(),
        "routing_policies": list(llm_router.ROUTING_POLICIES.values())
    }
    
    json_output = json.dumps(config, indent=2)
    
    if output:
        with open(output, 'w') as f:
            f.write(json_output)
        click.echo(f"✓ Configuration exported to {output}")
    else:
        click.echo(json_output)


@cli.group()
def secret():
    """Manage secure secrets."""
    pass


@secret.command("add")
@click.option("--name", required=True, help="Secret name")
@click.option("--value", required=True, prompt=True, hide_input=True, help="Secret value (masked if omitted)")
def add_secret(name: str, value: str):
    """Add a single secret."""
    from app.domain.secrets import manager as secrets_manager
    secrets_manager.set_secret(name, value)
    click.echo(f"✓ Secret '{name}' stored successfully")


@secret.command("import")
@click.argument("file", type=click.Path(exists=True))
def import_secrets(file: str):
    """Import secrets from a JSON file (bulk).
    
    File format: {"name1": "value1", "name2": "value2"}
    """
    from app.domain.secrets import manager as secrets_manager
    with open(file, 'r') as f:
        data = json.load(f)
    
    if not isinstance(data, dict):
        click.echo("Error: File must be a JSON object mapping names to values.", err=True)
        return

    count = 0
    for k, v in data.items():
        secrets_manager.set_secret(k, str(v))
        count += 1
    
    click.echo(f"✓ Imported {count} secrets from {file}")


@secret.command("list")
def list_secrets():
    """List secrets (metadata only)."""
    from app.domain.secrets import manager as secrets_manager
    secrets = secrets_manager.list_secrets()
    if not secrets:
        click.echo("No secrets found.")
        return
        
    click.echo(f"\n{'Name':<30} {'Status':<15}")
    click.echo("-" * 45)
    for s in secrets:
        click.echo(f"{s['name']:<30} {'******':<15}")


@secret.command("remove")
@click.argument("name")
def remove_secret(name: str):
    """Remove a secret."""
    from app.domain.secrets import manager as secrets_manager
    if secrets_manager.delete_secret(name):
        click.echo(f"✓ Secret '{name}' removed")
    else:
        click.echo(f"Error: Secret '{name}' not found", err=True)


if __name__ == "__main__":
    cli()
