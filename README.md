# Amplifier Runtime

`amplifier-runtime` is the UI-neutral session host shared by Amplifier's
terminal and desktop clients. It builds sessions through Amplifier Foundation,
drives Amplifier Core, normalizes runtime events, and owns durable session
identity, persistence, replay, approvals, interruption, and writer leases.

It is a Python package and executable, not an Amplifier bundle. Bundles remain
the declarative composition selected by a client or durable settings.

```text
TUI / CLI -- in-process API --\
                              amplifier-runtime -> Foundation -> Core
Studio ---- JSONL protocol ---/
```

## Install

macOS or Linux:

```sh
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/michaeljabbour/amplifier-runtime/main/scripts/install.sh \
  | sh
```

Windows PowerShell:

```powershell
$script = Invoke-RestMethod -UseBasicParsing \
  'https://raw.githubusercontent.com/michaeljabbour/amplifier-runtime/main/scripts/install.ps1'
& ([scriptblock]::Create($script))
```

Both installers resolve `main` to a full commit before installation and verify
the executable, JSONL host, and provider-status contract. To install a reviewed
revision directly, pass `--ref <40-character-sha>` or `-Ref <sha>`.

## Client contract

```sh
amplifier-runtime serve --attachable
amplifier-runtime provider status --format json
amplifier-runtime provider add openai --api-key-stdin --yes
amplifier-runtime settings get behavior
amplifier-runtime config paths --json
```

`serve` is a bidirectional, schema-versioned JSONL protocol on stdio. Clients
must request history replay after resume or attachment and must preserve the
single-writer lease: attach to a live owner rather than starting a second host
for the same durable session.

## Migration state

The implementation is being extracted from `amplifier-app-tui` without a
rewrite. The TUI uses this package as its exclusive kernel/model import path;
its formerly local copies are non-executable compatibility history. Studio
launches this executable directly. Client-specific rendering remains in each
client repository.

## Development

```sh
uv sync
uv run ruff check .
uv run pyright src tests
uv run pytest -q
uv run amplifier-runtime --version
uv run amplifier-runtime serve --help
```

The runtime never imports Textual, prompt-toolkit, Tauri, SolidJS, or client
rendering code. Protocol changes require schema/fixture tests and compatibility
validation through the TUI and Studio adapters.
