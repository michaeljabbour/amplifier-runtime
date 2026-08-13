"""Thin command surface for the neutral Amplifier runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
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
def settings_get(target: str | None) -> None:
    """List sections, or read one section or redacted setting."""
    from .kernel import settings_service
    from .model import settings_schema

    paths, keys = _settings_locations()
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
@click.option("--actor", default=None, metavar="ID")
@click.option(
    "--actor-kind",
    type=click.Choice(("human", "automation")),
    default="automation",
    show_default=True,
)
@click.option("--attachable/--no-attachable", default=False, show_default=True)
def serve(
    bundle: str | None,
    model: str | None,
    provider: str | None,
    mode: str | None,
    resume_id: str | None,
    attach: str | None,
    actor: str | None,
    actor_kind: str,
    attachable: bool,
) -> None:
    """Serve one interactive session as bidirectional JSONL on stdio."""

    if (model is None) != (provider is None):
        raise click.UsageError("--model and --provider must be supplied together")

    from .kernel.serve import serve as serve_runtime

    kwargs: dict[str, Any] = {
        "mode": mode,
        "model": model,
        "provider": provider,
        "resume_id": resume_id,
        "attach": attach,
        "actor": actor,
        "actor_kind": actor_kind,
        "attachable": attachable,
    }
    raise SystemExit(asyncio.run(serve_runtime(bundle, **kwargs)))


if __name__ == "__main__":
    main()
