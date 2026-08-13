#!/bin/sh
# Install amplifier-runtime from one resolved Git commit on macOS or Linux.
set -eu

repo_url=${AMPLIFIER_RUNTIME_REPO_URL:-https://github.com/michaeljabbour/amplifier-runtime.git}
requested_ref=${AMPLIFIER_RUNTIME_REF:-main}
update_shell=${AMPLIFIER_RUNTIME_UPDATE_SHELL:-1}
temp_dir=""

fail() { printf 'install failed: %s\n' "$*" >&2; exit 1; }
validation_fail() { printf 'install validation failed: %s\n' "$*" >&2; exit 1; }
cleanup() {
    if [ -n "$temp_dir" ] && [ -d "$temp_dir" ]; then rm -rf "$temp_dir"; fi
}
trap cleanup 0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ref) [ "$#" -ge 2 ] || fail "--ref requires a value"; requested_ref=$2; shift 2 ;;
        --no-update-shell) update_shell=0; shift ;;
        -h|--help)
            printf '%s\n' 'Usage: install.sh [--ref REF] [--no-update-shell]'
            exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

case "$repo_url" in
    https://*|file://*) ;;
    *) fail "repository URL must use https:// or file://" ;;
esac
case "$repo_url" in https://*@*) fail "repository URL must not contain credentials" ;; esac
case "$requested_ref" in
    ""|-*|*[!A-Za-z0-9._/-]*) fail "invalid ref '$requested_ref'" ;;
    refs/*) fail "use a branch or tag without refs/" ;;
esac
command -v git >/dev/null 2>&1 || fail "git is required"

is_sha() {
    [ "${#1}" -eq 40 ] || return 1
    case "$1" in *[!0-9A-Fa-f]*) return 1 ;; *) return 0 ;; esac
}
resolve_ref() {
    if is_sha "$1"; then printf '%s\n' "$1" | tr A-F a-f; return; fi
    refs=$(git ls-remote --exit-code "$repo_url" \
        "refs/heads/$1" "refs/tags/$1" "refs/tags/$1^{}" 2>/dev/null) ||
        fail "could not resolve '$1' from $repo_url"
    for target in "refs/heads/$1" "refs/tags/$1^{}" "refs/tags/$1"; do
        sha=$(printf '%s\n' "$refs" | awk -v wanted="$target" '$2 == wanted { print $1; exit }')
        if [ -n "$sha" ]; then is_sha "$sha" || fail "remote returned an invalid commit"; printf '%s\n' "$sha" | tr A-F a-f; return; fi
    done
    fail "remote did not return '$1'"
}
find_uv() {
    if [ -n "${AMPLIFIER_RUNTIME_UV_BIN:-}" ] && [ -x "$AMPLIFIER_RUNTIME_UV_BIN" ]; then printf '%s\n' "$AMPLIFIER_RUNTIME_UV_BIN"; return; fi
    if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
    if [ -n "${HOME:-}" ] && [ -x "$HOME/.local/bin/uv" ]; then printf '%s\n' "$HOME/.local/bin/uv"; return; fi
    return 1
}

resolved_sha=$(resolve_ref "$requested_ref")
printf 'Installing Amplifier Runtime source commit %s\n' "$resolved_sha"
if ! uv_bin=$(find_uv); then
    command -v curl >/dev/null 2>&1 || fail "curl is required to install uv"
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/amplifier-runtime-install.XXXXXX") || fail "could not create temporary directory"
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$temp_dir/uv.sh" || fail "could not download uv installer"
    sh "$temp_dir/uv.sh" || fail "uv installation failed"
    uv_bin=$(find_uv) || fail "uv installed but could not be found"
fi

"$uv_bin" tool install --reinstall --no-config "git+$repo_url@$resolved_sha" || fail "uv could not install Amplifier Runtime"
tool_bin=$("$uv_bin" tool dir --bin) || validation_fail "uv could not locate its tool directory"
runtime_bin="$tool_bin/amplifier-runtime"
[ -x "$runtime_bin" ] || validation_fail "installation produced no amplifier-runtime executable"
version=$($runtime_bin --version) || validation_fail "runtime could not report its version"
"$runtime_bin" serve --help >/dev/null || validation_fail "runtime serve contract is unavailable"
"$runtime_bin" provider status --format json >/dev/null || validation_fail "runtime provider contract is unavailable"
if [ "$update_shell" = 1 ]; then "$uv_bin" tool update-shell >/dev/null 2>&1 || printf 'warning: add %s to PATH\n' "$tool_bin" >&2; fi
printf 'Installed and verified %s - %s\n' "$runtime_bin" "$version"
