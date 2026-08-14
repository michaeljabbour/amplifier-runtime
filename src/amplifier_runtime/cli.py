"""Thin command surface for the neutral Amplifier runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Literal

import click

from . import __version__

_SCOPES = ("global", "project", "local")


def _scope_options(function):  # type: ignore[no-untyped-def]
    for scope in reversed(_SCOPES):
        function = click.option(f"--{scope}", f"is_{scope}", is_flag=True)(function)
    return function


def _selected_scope(
    is_global: bool, is_project: bool, is_local: bool
) -> Literal["global", "project", "local"]:
    selected = [
        scope
        for scope, enabled in zip(_SCOPES, (is_global, is_project, is_local), strict=True)
        if enabled
    ]
    if len(selected) > 1:
        raise click.UsageError("choose only one of --global, --project, or --local")
    return selected[0] if selected else "global"  # type: ignore[return-value]


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="amplifier-runtime")
@click.pass_context
def main(context: click.Context) -> None:
    """Run and control UI-neutral Amplifier sessions."""

    if context.invoked_subcommand is None:
        click.echo(context.get_help())


@main.group()
def provider() -> None:
    """Inspect and configure model providers for runtime sessions."""


@provider.command("list")
@click.option("--format", "output_format", type=click.Choice(("text", "json")), default="text")
def provider_list(output_format: str) -> None:
    """List configured providers (the active entry is the primary route)."""
    from .kernel import setup

    providers = setup.configured_providers()
    payload = [
        {
            "name": entry.name,
            "module": entry.module_id,
            "model": entry.model or "",
            "active": entry.primary,
            "priority": entry.priority,
            "scope": entry.scope,
        }
        for entry in providers
    ]
    if output_format == "json":
        click.echo(json.dumps(payload, sort_keys=True))
        return
    if not payload:
        click.echo("No providers configured.")
        return
    for entry in payload:
        marker = "*" if entry["active"] else " "
        model = f" ({entry['model']})" if entry["model"] else ""
        click.echo(f"{marker} {entry['name']} · {entry['module']}{model}")


@provider.command("status")
@click.option("--format", "output_format", type=click.Choice(("text", "json")), default="text")
def provider_status(output_format: str) -> None:
    """Report whether the runtime can mount a configured provider."""
    from .kernel import setup

    configured = setup.has_configured_provider()
    payload = {
        "configured": configured,
        "message": "Provider is configured" if configured else "No provider is configured",
        "remediation": "" if configured else "Run amplifier-runtime provider add",
    }
    if output_format == "json":
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        click.echo(payload["message"])
        if payload["remediation"]:
            click.echo(payload["remediation"])


@provider.command("add")
@click.argument("provider_type")
@click.option("--api-key-stdin", is_flag=True, help="Read the credential from stdin.")
@click.option("--base-url", default=None)
@click.option("--model", default=None)
@click.option("--yes", is_flag=True, help="Confirm the non-interactive write.")
def provider_add(
    provider_type: str,
    api_key_stdin: bool,
    base_url: str | None,
    model: str | None,
    yes: bool,
) -> None:
    """Configure one provider without exposing its credential in argv."""
    from .kernel import bundle_admin, setup

    if not api_key_stdin or not yes:
        raise click.UsageError("provider add requires --api-key-stdin and --yes")
    api_key = click.get_text_stream("stdin").read().strip()
    if not api_key:
        raise click.UsageError("--api-key-stdin received an empty API key")
    token = provider_type.strip().lower()
    module_id = token if token.startswith("provider-") else f"provider-{token}"
    source = setup.PROVIDER_SOURCES.get(module_id)
    if source is None:
        known = ", ".join(sorted(name.removeprefix("provider-") for name in setup.PROVIDER_SOURCES))
        raise click.UsageError(f"unknown provider '{provider_type}' (known: {known})")
    variables = setup.PROVIDER_CREDENTIAL_VARS.get(module_id) or []
    key_var = variables[0] if variables else f"{setup.provider_env_prefix(module_id)}_API_KEY"
    base_url_var = f"{setup.provider_env_prefix(module_id)}_BASE_URL"
    keys_path = setup.keys_file()
    setup.write_key(keys_path, key_var, api_key)
    if base_url:
        setup.write_key(keys_path, base_url_var, base_url.strip())
    entry = setup.provider_config_entry(
        module_id,
        key_var=key_var,
        model=(model or "").strip() or None,
        base_url=(base_url or "").strip() or None,
        base_url_var=base_url_var,
        source=source,
    )
    target = setup.write_provider_config(bundle_admin.settings_paths(None, None), "global", entry)
    click.echo(f"configured provider {module_id} -> {target}")


@main.group()
def bundle() -> None:
    """Discover and register bundles for runtime sessions."""


@bundle.command("list")
@click.option("--all", "all_bundles", is_flag=True)
@click.option("--format", "output_format", type=click.Choice(("text", "json")), default="text")
def bundle_list(all_bundles: bool, output_format: str) -> None:
    """List available bundles and the active/default selection."""
    from .kernel import bundle_admin
    from .kernel.config import DEFAULT_BUNDLE

    entries = bundle_admin.list_bundles(all_bundles=all_bundles)
    active_name = bundle_admin.current_bundle()
    payload = []
    for entry in entries:
        default_active = active_name is None and entry.name == DEFAULT_BUNDLE
        payload.append(
            {
                "name": entry.name,
                "active": entry.active or active_name == entry.name or default_active,
                "location": entry.uri or ("(on disk)" if entry.source == "local" else ""),
                "status": "default" if default_active else ("app" if entry.source == "app" else ""),
                "source": entry.source,
            }
        )
    if output_format == "json":
        click.echo(json.dumps(payload, sort_keys=True))
        return
    if not payload:
        click.echo("No bundles found.")
        return
    for entry in payload:
        marker = "*" if entry["active"] else " "
        status = f" [{entry['status']}]" if entry["status"] else ""
        click.echo(f"{marker} {entry['name']}{status} · {entry['location']}")


@bundle.command("add")
@click.argument("uri")
@click.option("--name", "name", default=None)
@click.option(
    "--warm/--no-warm",
    default=False,
    help="Prepare and install the bundle's modules before registering it.",
)
@_scope_options
def bundle_add(
    uri: str,
    name: str | None,
    warm: bool,
    is_global: bool,
    is_project: bool,
    is_local: bool,
) -> None:
    """Validate and register one bundle URI for discovery."""
    from .kernel import bundle_admin

    info = asyncio.run(bundle_admin.load_bundle_info(uri))
    if info is None:
        raise click.ClickException(f"could not load bundle from: {uri}")
    if warm:
        result = asyncio.run(bundle_admin.warm_bundle(uri, project_dir=Path.cwd()))
        if not result.ok:
            raise click.ClickException(f"could not warm bundle '{uri}': {result.message}")
        click.echo(f"warmed {uri} · {result.message}")
    resolved_name = (name or info.name).strip()
    if not resolved_name:
        raise click.UsageError("bundle name cannot be empty")
    scope = _selected_scope(is_global, is_project, is_local)
    target = bundle_admin.add_bundle(
        bundle_admin.settings_paths(None, None), resolved_name, uri, scope
    )
    click.echo(f"registered {resolved_name} -> {uri} ({scope}: {target})")


@bundle.command("warm")
@click.argument("uri")
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False, resolve_path=True),
    default=None,
    help="Project context used while resolving the bundle.",
)
def bundle_warm(uri: str, project_dir: Path | None) -> None:
    """Prepare a bundle and install its modules before a session boots."""
    from .kernel import bundle_admin

    result = asyncio.run(bundle_admin.warm_bundle(uri, project_dir=project_dir or Path.cwd()))
    if not result.ok:
        raise click.ClickException(f"could not warm bundle '{uri}': {result.message}")
    click.echo(f"warmed {uri} · {result.message}")


@main.group()
def settings() -> None:
    """Read and update durable runtime settings."""


def _settings_locations():  # type: ignore[no-untyped-def]
    from .kernel import setup
    from .kernel.bundle_admin import settings_paths

    return settings_paths(None, None), setup.keys_file()


def _source_line(resolved) -> str:  # type: ignore[no-untyped-def]
    if resolved.source == "env":
        return f"source: env ({resolved.field.env_var})"
    if resolved.source_file is not None:
        return f"source: {resolved.source} ({resolved.source_file})"
    return f"source: {resolved.source}"


@settings.command("get")
@click.argument("target", required=False)
@click.option("--json", "as_json", is_flag=True, help="Emit a redacted machine-readable snapshot.")
def settings_get(target: str | None, as_json: bool) -> None:
    """List sections, or read one section or redacted setting."""
    from .kernel import settings_service
    from .model import settings_schema

    paths, keys = _settings_locations()
    if as_json:
        field = settings_schema.field_by_path(target) if target else None
        if target is None:
            fields = settings_schema.FIELDS
        elif field is not None:
            fields = (field,)
        else:
            fields = settings_schema.fields_in_section(target)
            if not fields:
                raise click.UsageError(f"unknown setting or section '{target}'")
        values = []
        for selected in fields:
            resolved = settings_service.resolve_field(paths, keys, selected)
            values.append(
                {
                    "path": selected.path,
                    "display": resolved.display,
                    "source": resolved.source,
                    "sourceLabel": _source_line(resolved).removeprefix("source: "),
                    "sourceFile": str(resolved.source_file) if resolved.source_file else None,
                    "applies": selected.applies,
                    "remoteWritable": not selected.secret,
                }
            )
        click.echo(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "type": "settings.values",
                    "projectDir": str(Path.cwd().resolve()),
                    "version": __version__,
                    "values": values,
                    "paths": {
                        "global": str(paths.global_settings),
                        "project": str(paths.project_settings),
                        "local": str(paths.local_settings),
                        "keys": str(keys),
                    },
                    "recentChanges": settings_service.recent_changes(keys.parent, limit=5),
                }
            )
        )
        return
    if target is None:
        for section in settings_schema.SECTIONS:
            click.echo(f"{section.id}  {section.summary}")
        return
    field = settings_schema.field_by_path(target)
    if field is not None:
        resolved = settings_service.resolve_field(paths, keys, field)
        click.echo(resolved.display)
        click.echo(_source_line(resolved))
        return
    fields = settings_schema.fields_in_section(target)
    if not fields:
        raise click.UsageError(f"unknown setting or section '{target}'")
    for section_field in fields:
        resolved = settings_service.resolve_field(paths, keys, section_field)
        click.echo(f"{section_field.path} = {resolved.display}")
        click.echo(f"  {_source_line(resolved)}")


@settings.command("set")
@click.argument("path")
@click.argument("value")
@_scope_options
def settings_set(path: str, value: str, is_global: bool, is_project: bool, is_local: bool) -> None:
    """Validate and persist one setting."""
    from .kernel import settings_service
    from .model import settings_schema

    field = settings_schema.field_by_path(path)
    if field is None:
        raise click.UsageError(f"unknown setting '{path}'")
    try:
        settings_schema.parse_field_value(field, value)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    paths, keys = _settings_locations()
    ok, message = settings_service.set_value(
        paths, keys, path, value, _selected_scope(is_global, is_project, is_local)
    )
    click.echo(message, err=not ok)
    if not ok:
        raise SystemExit(1)


@settings.command("unset")
@click.argument("path")
@_scope_options
def settings_unset(path: str, is_global: bool, is_project: bool, is_local: bool) -> None:
    """Remove one setting from a durable scope."""
    from .kernel import settings_service
    from .model import settings_schema

    if settings_schema.field_by_path(path) is None:
        raise click.UsageError(f"unknown setting '{path}'")
    paths, keys = _settings_locations()
    ok, message = settings_service.unset_value(
        paths, keys, path, _selected_scope(is_global, is_project, is_local)
    )
    click.echo(message, err=not ok)
    if not ok:
        raise SystemExit(1)


@dataclass(frozen=True)
class ConfigPaths:
    global_settings: str
    project_settings: str
    local_settings: str
    keys: str
    routing: str


def _config_paths() -> ConfigPaths:
    from .kernel import bundle_admin, setup

    paths = bundle_admin.settings_paths(None, None)
    home = paths.global_settings.parent
    return ConfigPaths(
        global_settings=str(paths.global_settings),
        project_settings=str(paths.project_settings),
        local_settings=str(paths.local_settings),
        keys=str(setup.keys_file()),
        routing=str(home / "routing"),
    )


@main.group()
def config() -> None:
    """Inspect redacted runtime configuration metadata."""


@config.command("paths")
@click.option("--json", "as_json", is_flag=True)
def config_paths(as_json: bool) -> None:
    """Show durable settings paths without reading secret values."""
    paths = _config_paths()
    if as_json:
        click.echo(json.dumps({"schema": "amplifier-runtime/config-paths/v1", **asdict(paths)}))
        return
    for label, value in asdict(paths).items():
        click.echo(f"{label}: {value}")


@main.command()
@click.option("--bundle", default=None, help="Bundle name, path, or URI.")
@click.option("--model", default=None, help="Model override for this session.")
@click.option("--provider", default=None, help="Provider override for this session.")
@click.option("--mode", default=None, help="Initial interaction mode.")
@click.option("--resume", "resume_id", default=None, metavar="SESSION_ID")
@click.option("--attach", default=None, metavar="REF")
@click.option(
    "--project-dir",
    type=click.Path(path_type=Path, file_okay=False, resolve_path=True),
    default=None,
    help="Project working tree for this session (defaults to the current directory).",
)
@click.option("--actor", default=None, metavar="ID")
@click.option(
    "--actor-kind",
    type=click.Choice(("human", "automation")),
    default="automation",
    show_default=True,
)
@click.option("--attachable/--no-attachable", default=False, show_default=True)
@click.option(
    "--detached/--no-detached",
    default=False,
    show_default=True,
    help="Keep an attachable owner alive after its launching pipe closes.",
)
@click.option("--peer-principal", default=None, hidden=True)
@click.option(
    "--peer-kind",
    type=click.Choice(("human", "automation")),
    default="automation",
    hidden=True,
)
@click.option("--peer-permissions", default="read,write,control", hidden=True)
def serve(
    bundle: str | None,
    model: str | None,
    provider: str | None,
    mode: str | None,
    resume_id: str | None,
    attach: str | None,
    project_dir: Path | None,
    actor: str | None,
    actor_kind: str,
    attachable: bool,
    detached: bool,
    peer_principal: str | None,
    peer_kind: str,
    peer_permissions: str,
) -> None:
    """Serve one interactive session as bidirectional JSONL on stdio."""

    if (model is None) != (provider is None):
        raise click.UsageError("--model and --provider must be supplied together")
    if detached and not attachable:
        raise click.UsageError("--detached requires --attachable")

    from .kernel.serve import serve as serve_runtime

    kwargs: dict[str, Any] = {
        "mode": mode,
        "model": model,
        "provider": provider,
        "resume_id": resume_id,
        "attach": attach,
        "project_dir": project_dir,
        "actor": actor,
        "actor_kind": actor_kind,
        "attachable": attachable,
        "detached": detached,
        "peer_principal": peer_principal,
        "peer_kind": peer_kind,
        "peer_permissions": peer_permissions,
    }
    raise SystemExit(asyncio.run(serve_runtime(bundle, **kwargs)))


if __name__ == "__main__":
    main()
