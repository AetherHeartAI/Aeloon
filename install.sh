#!/usr/bin/env bash
set -euo pipefail

AELOON_HOME="${AELOON_HOME:-$HOME/.aeloon}"
AELOON_VENV="$AELOON_HOME/venv"
AELOON_BIN="$AELOON_HOME/bin"
CONFIG_PATH="$AELOON_HOME/config.json"
WORKSPACE_PATH="$AELOON_HOME/workspace"
PYTHON_VERSION="${AELOON_PYTHON_VERSION:-3.12}"
GITHUB_REPO="https://github.com/AetherHeartAI/Aeloon"
DEFAULT_PROVIDER_KEY="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"

VERSION=""
FROM_SOURCE=false
SOURCE_DIR=""
NO_MODIFY_PATH=false
OFFLINE=false
FORCE_REINSTALL=false

LOCAL_WHEEL_FILE=""
LOCAL_WHEELHOUSE_DIR=""

SELECTED_PROVIDER=""
PROVIDER_API_KEY=""
SELECTED_MODEL=""
SELECTED_API_BASE=""
SELECTED_CHANNEL="none"
WEB_PORT="18790"
INTERACTIVE_SETUP_DONE=false
USED_BUNDLED_DEFAULT=false

CHANNEL_ALLOW_FROM=""
CHANNEL_TOKEN=""
CHANNEL_APP_TOKEN=""
CHANNEL_BRIDGE_TOKEN=""
CHANNEL_SECRET=""
EMAIL_CONSENT_GRANTED="false"
EMAIL_IMAP_HOST=""
EMAIL_IMAP_PORT="993"
EMAIL_IMAP_USERNAME=""
EMAIL_IMAP_PASSWORD=""
EMAIL_SMTP_HOST=""
EMAIL_SMTP_PORT="587"
EMAIL_SMTP_USERNAME=""
EMAIL_SMTP_PASSWORD=""
EMAIL_FROM_ADDRESS=""

detect_web_ui_static_dir() {
    local candidate
    for candidate in "$SCRIPT_DIR/dist/ui" "$SCRIPT_DIR/ui/dist"; do
        if [ -f "$candidate/index.html" ]; then
            printf '%s' "$candidate"
            return
        fi
    done
}

prompt_port() {
    local prompt="$1"
    local default_value="$2"
    local reply

    while :; do
        reply="$(prompt_line "$prompt" "$default_value")"
        case "$reply" in
            ''|*[!0-9]*)
                warn "Please enter a numeric port."
                ;;
            *)
                if [ "$reply" -lt 1 ] || [ "$reply" -gt 65535 ]; then
                    warn "Port must be between 1 and 65535."
                else
                    printf '%s' "$reply"
                    return
                fi
                ;;
        esac
    done
}

if [ -t 1 ]; then
    BOLD="\033[1m"
    GREEN="\033[0;32m"
    YELLOW="\033[0;33m"
    RED="\033[0;31m"
    BLUE="\033[0;34m"
    RESET="\033[0m"
else
    BOLD=""
    GREEN=""
    YELLOW=""
    RED=""
    BLUE=""
    RESET=""
fi

info() {
    printf "${GREEN}[aeloon]${RESET} %s\n" "$*"
}

warn() {
    printf "${YELLOW}[aeloon]${RESET} %s\n" "$*"
}

error() {
    printf "${RED}[aeloon]${RESET} %s\n" "$*" >&2
}

die() {
    error "$@"
    exit 1
}

have_prompt_tty() {
    [ -t 0 ]
}

prompt_print() {
    printf "%b" "$*" >&2
}

prompt_line() {
    local prompt="$1"
    local default_value="${2:-}"
    local reply

    if ! have_prompt_tty; then
        printf '%s' "$default_value"
        return
    fi

    if [ -n "$default_value" ]; then
        prompt_print "${BLUE}${prompt}${RESET} ${BOLD}[${default_value}]${RESET}: "
    else
        prompt_print "${BLUE}${prompt}${RESET}: "
    fi

    IFS= read -r reply || true
    if [ -z "$reply" ]; then
        reply="$default_value"
    fi
    printf '%s' "$reply"
}

prompt_secret() {
    local prompt="$1"
    local reply

    if ! have_prompt_tty; then
        printf '%s' ""
        return
    fi

    prompt_print "${BLUE}${prompt}${RESET}: "
    stty -echo
    IFS= read -r reply || true
    stty echo
    prompt_print "\n"
    printf '%s' "$reply"
}

prompt_yes_no() {
    local prompt="$1"
    local default_value="${2:-y}"
    local reply

    reply="$(prompt_line "$prompt (y/n)" "$default_value")"
    case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
        y|yes) return 0 ;;
        n|no) return 1 ;;
        *)
            warn "Please answer y or n."
            prompt_yes_no "$prompt" "$default_value"
            return $? ;;
    esac
}

cursor_up_lines() {
    local count="$1"
    while [ "$count" -gt 0 ]; do
        prompt_print "\033[1A\033[2K\r"
        count=$((count - 1))
    done
}

menu_select() {
    local prompt="$1"
    local default_index="$2"
    shift 2
    local items=("$@")
    local count="${#items[@]}"
    local selected="$default_index"
    local rendered=false
    local key extra value label i

    if [ "$count" -eq 0 ]; then
        return 1
    fi

    if ! have_prompt_tty; then
        printf '%s' "${items[$default_index]%%$'\t'*}"
        return
    fi

    prompt_print "${BLUE}${prompt}${RESET}\n"
    prompt_print "${YELLOW}Use ↑/↓ (or ←/→) and Enter to select.${RESET}\n"

    while :; do
        if [ "$rendered" = true ]; then
            cursor_up_lines "$count"
        fi

        for ((i = 0; i < count; i++)); do
            value="${items[$i]%%$'\t'*}"
            label="${items[$i]#*$'\t'}"
            if [ "$label" = "$value" ]; then
                label="$value"
            fi

            if [ "$i" -eq "$selected" ]; then
                prompt_print "${GREEN}> ${label}${RESET}\n"
            else
                prompt_print "  ${label}\n"
            fi
        done

        rendered=true
        IFS= read -rsn1 key || true

        case "$key" in
            ""|$'\n'|$'\r')
                prompt_print "\n"
                printf '%s' "${items[$selected]%%$'\t'*}"
                return
                ;;
            $'\x1b')
                IFS= read -rsn2 extra || true
                key="$key$extra"
                case "$key" in
                    $'\x1b[A'|$'\x1b[D')
                        selected=$((selected - 1))
                        if [ "$selected" -lt 0 ]; then
                            selected=$((count - 1))
                        fi
                        ;;
                    $'\x1b[B'|$'\x1b[C')
                        selected=$((selected + 1))
                        if [ "$selected" -ge "$count" ]; then
                            selected=0
                        fi
                        ;;
                esac
                ;;
            k|K)
                selected=$((selected - 1))
                if [ "$selected" -lt 0 ]; then
                    selected=$((count - 1))
                fi
                ;;
            j|J)
                selected=$((selected + 1))
                if [ "$selected" -ge "$count" ]; then
                    selected=0
                fi
                ;;
        esac
    done
}

usage() {
    cat <<EOF
Aeloon Installer

Usage:
  bash install.sh [options]

Options:
  --version <ref>       Install from a specific git ref (tag / branch / commit)
  --from-source [DIR]   Install from a local checkout, or the given directory
  --reinstall           Reinstall or upgrade Aeloon even if it is already installed
  --offline             Require local wheel artifacts; do not use the network for packages
  --no-modify-path      Do not modify shell profile files
  -h, --help            Show this help

Environment:
  AELOON_HOME           Install location (default: ~/.aeloon)
  AELOON_PYTHON_VERSION Python version for uv venv (default: 3.12)

Examples:
  curl -fsSL https://raw.githubusercontent.com/AetherHeartAI/Aeloon/main/install.sh | bash
  bash install.sh --from-source
  bash install.sh --from-source /path/to/Aeloon
  bash install.sh --offline
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version)
            [ $# -ge 2 ] || die "--version requires a value"
            VERSION="$2"
            shift 2
            ;;
        --from-source)
            FROM_SOURCE=true
            if [ $# -ge 2 ] && [[ "$2" != --* ]]; then
                SOURCE_DIR="$(cd "$2" && pwd)" || die "Directory not found: $2"
                shift
            fi
            shift
            ;;
        --offline)
            OFFLINE=true
            shift
            ;;
        --reinstall)
            FORCE_REINSTALL=true
            shift
            ;;
        --no-modify-path)
            NO_MODIFY_PATH=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) die "Unsupported OS: $OS. This installer currently supports macOS and Linux." ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$FROM_SOURCE" = false ] && [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/aeloon" ]; then
    FROM_SOURCE=true
    SOURCE_DIR="$SCRIPT_DIR"
fi

detect_local_bundle_artifacts() {
    local wheel_count
    local wheel_files=()

    if [ -d "$SCRIPT_DIR/dist" ]; then
        wheel_files=("$SCRIPT_DIR"/dist/*.whl)
        if [ -e "${wheel_files[0]}" ]; then
            wheel_count="${#wheel_files[@]}"
            LOCAL_WHEEL_FILE="${wheel_files[0]}"
            if [ "$wheel_count" -gt 1 ]; then
                warn "Multiple wheel files found in $SCRIPT_DIR/dist; using $LOCAL_WHEEL_FILE"
            fi
        fi
    fi

    if [ -d "$SCRIPT_DIR/wheelhouse" ]; then
        wheel_files=("$SCRIPT_DIR"/wheelhouse/*)
        if [ -e "${wheel_files[0]}" ]; then
            LOCAL_WHEELHOUSE_DIR="$SCRIPT_DIR/wheelhouse"
        fi
    fi
}

detect_local_bundle_artifacts

if [ "$OFFLINE" = true ] && [ -z "$LOCAL_WHEEL_FILE" ]; then
    die "Offline mode requires a local wheel in $SCRIPT_DIR/dist"
fi

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        info "uv found: $(command -v uv)"
        return
    fi

    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            export PATH="$(dirname "$candidate"):$PATH"
            info "uv found: $candidate"
            return
        fi
    done

    if [ "$OFFLINE" = true ]; then
        die "Offline mode requires uv to already be installed on the machine."
    fi

    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    command -v uv >/dev/null 2>&1 || die "Failed to install uv. See https://docs.astral.sh/uv/"
    info "uv installed successfully"
}

resolve_install_target() {
    if [ -n "$LOCAL_WHEEL_FILE" ] && [ "$FROM_SOURCE" = false ]; then
        printf '%s' "$LOCAL_WHEEL_FILE"
        return
    fi

    if [ "$FROM_SOURCE" = true ]; then
        if [ -n "$SOURCE_DIR" ]; then
            printf '%s' "$SOURCE_DIR"
            return
        fi
        printf '%s' "$SCRIPT_DIR"
        return
    fi

    if [ -n "$VERSION" ]; then
        printf '%s' "git+${GITHUB_REPO}.git@${VERSION}"
        return
    fi

    printf '%s' "${GITHUB_REPO}/archive/refs/heads/main.tar.gz"
}

create_venv() {
    local -a venv_args
    if [ -x "$AELOON_VENV/bin/python" ]; then
        info "Existing environment found, reusing it..."
        info "Python environment ready: $($AELOON_VENV/bin/python --version)"
        return
    fi

    if [ -d "$AELOON_VENV" ]; then
        warn "Existing environment directory found without a usable Python executable; attempting to complete it."
    else
        info "Creating Python ${PYTHON_VERSION} environment..."
    fi

    venv_args=(venv "$AELOON_VENV" --python "$PYTHON_VERSION" --quiet --allow-existing)
    if [ "$OFFLINE" = true ]; then
        venv_args+=(--no-python-downloads)
    fi

    uv "${venv_args[@]}"
    [ -x "$AELOON_VENV/bin/python" ] || die "Failed to create virtual environment"
    info "Python environment ready: $($AELOON_VENV/bin/python --version)"
}

install_aeloon() {
    local target
    local -a install_args

    if [ "$FORCE_REINSTALL" = false ] \
        && [ -x "$AELOON_VENV/bin/aeloon" ] \
        && "$AELOON_VENV/bin/python" -c "import aeloon" >/dev/null 2>&1; then
        info "Existing Aeloon install found, reusing it."
        info "Pass --reinstall to upgrade or reinstall the package."
        return
    fi

    target="$(resolve_install_target)"
    install_args=(install --python "$AELOON_VENV/bin/python" --upgrade)

    if [ "$OFFLINE" = true ]; then
        install_args+=(--offline)
    fi

    if [ -n "$LOCAL_WHEELHOUSE_DIR" ]; then
        install_args+=(--no-index --find-links "$LOCAL_WHEELHOUSE_DIR")
        info "Using bundled dependency wheelhouse: $LOCAL_WHEELHOUSE_DIR"
    fi

    info "Installing Aeloon..."
    uv pip "${install_args[@]}" "$target"
    [ -x "$AELOON_VENV/bin/aeloon" ] || die "Installation failed: aeloon executable not found"
    info "Aeloon installed successfully"
}

create_wrapper() {
    mkdir -p "$AELOON_BIN"
    cat > "$AELOON_BIN/aeloon" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_AELOON_HOME="$(cd "$SCRIPT_DIR/.." && pwd)"
AELOON_HOME="${AELOON_HOME:-$DEFAULT_AELOON_HOME}"
export AELOON_HOME
REAL_BIN="$AELOON_HOME/venv/bin/aeloon"

if [ ! -x "$REAL_BIN" ]; then
    echo "Error: Aeloon environment not found at $AELOON_HOME/venv" >&2
    exit 1
fi

exec "$REAL_BIN" "$@"
EOF
    chmod +x "$AELOON_BIN/aeloon"
    info "Wrapper created at $AELOON_BIN/aeloon"
}

append_path_line() {
    local profile="$1"
    local line='export PATH="$HOME/.aeloon/bin:$PATH"'

    [ -f "$profile" ] || touch "$profile"
    if ! grep -Fqs "$line" "$profile"; then
        printf '\n%s\n' "$line" >> "$profile"
        info "Updated PATH in $profile"
    fi
}

update_path() {
    if [ "$NO_MODIFY_PATH" = true ]; then
        warn "Skipping shell profile changes. Add $AELOON_BIN to PATH manually."
        return
    fi

    append_path_line "$HOME/.zshrc"
    append_path_line "$HOME/.bashrc"
    append_path_line "$HOME/.bash_profile"
}

run_onboard() {
    mkdir -p "$AELOON_HOME"

    if [ -f "$CONFIG_PATH" ]; then
        info "Existing config found at $CONFIG_PATH; keeping it."
        mkdir -p "$WORKSPACE_PATH"
        return
    fi

    info "Initializing config and workspace..."
    "$AELOON_VENV/bin/aeloon" onboard --config "$CONFIG_PATH" --workspace "$WORKSPACE_PATH"
}

installer_python() {
    "$AELOON_VENV/bin/python" -m aeloon.install_support "$@"
}

print_provider_catalog() {
    installer_python providers-text
}

provider_menu_entries() {
    printf '%s\n' $'default\tDefault - OpenRouter free model with bundled key'
    installer_python providers-menu
}

provider_exists() {
    installer_python providers | "$AELOON_VENV/bin/python" -c '
import json, sys
provider = sys.argv[1].strip().replace("-", "_")
payload = json.load(sys.stdin)
names = {item["name"] for item in payload["providers"]}
raise SystemExit(0 if provider in names else 1)
' "$1"
}

recommended_model() {
    installer_python recommended-model --provider "$1"
}

detect_models_json() {
    installer_python detect-models --provider "$1" --api-key "$2" --api-base "$3"
}

print_model_detection() {
    printf '%s' "$1" | "$AELOON_VENV/bin/python" -c '
import json, sys
data = json.load(sys.stdin)
models = data.get("models") or []
msg = data.get("message") or ""
if models:
    print("Detected models:")
    for model in models[:12]:
        print(f"  - {model}")
    if len(models) > 12:
        print(f"  ... and {len(models) - 12} more")
else:
    print("Detected models: none")
if msg:
    print(msg)
'
}

suggest_model_from_detection() {
    printf '%s' "$1" | "$AELOON_VENV/bin/python" -c '
import json, sys
data = json.load(sys.stdin)
models = data.get("models") or []
recommended = data.get("recommended") or ""
if models:
    print(recommended if recommended in models else models[0])
else:
    print(recommended)
'
}

suggest_free_model_from_detection() {
    printf '%s' "$1" | "$AELOON_VENV/bin/python" -c '
import json, sys

data = json.load(sys.stdin)
models = data.get("models") or []
recommended = data.get("recommended") or ""
preferred_terms = ("qwen", "deepseek", "llama", "gemma", "mistral")

free_models = [model for model in models if ":free" in model or model.endswith("/free")]
for term in preferred_terms:
    for model in free_models:
        if term in model.lower():
            print(model)
            raise SystemExit(0)
if free_models:
    print(free_models[0])
'
}

model_menu_entries_from_detection() {
    printf '%s' "$1" | "$AELOON_VENV/bin/python" -c '
import json, sys

data = json.load(sys.stdin)
free_only = sys.argv[1] == "true"
models = data.get("models") or []
recommended = data.get("recommended") or ""
preferred_terms = ("qwen", "deepseek", "llama", "gemma", "mistral")

if free_only:
    models = [model for model in models if ":free" in model or model.endswith("/free")]

if not models:
    raise SystemExit(0)

preferred = ""
if free_only:
    for term in preferred_terms:
        preferred = next((model for model in models if term in model.lower()), "")
        if preferred:
            break
    if not preferred and recommended in models:
        preferred = recommended
else:
    preferred = recommended if recommended in models else models[0]

ordered = []
if preferred:
    ordered.append(preferred)
for model in models:
    if model not in ordered:
        ordered.append(model)

for model in ordered:
    print(f"{model}\t{model}")
' "$2"
}

choose_model_from_detection() {
    local detection_json="$1"
    local free_only="$2"
    local prompt_label="${3:-Model}"
    local fallback_model=""
    local items=()
    local line

    while IFS= read -r line; do
        [ -n "$line" ] && items+=("$line")
    done < <(model_menu_entries_from_detection "$detection_json" "$free_only")

    if [ "${#items[@]}" -gt 0 ]; then
        if [ "$free_only" = true ]; then
            prompt_print "Detected ${#items[@]} fully free models.\n"
        else
            prompt_print "Detected ${#items[@]} available models.\n"
        fi
        printf '%s' "$(menu_select "$prompt_label" 0 "${items[@]}")"
        return
    fi

    if [ "$free_only" = true ]; then
        fallback_model="$(suggest_free_model_from_detection "$detection_json")"
        [ -n "$fallback_model" ] || die "No fully free OpenRouter models were detected for the default setup. Please choose another provider."
    else
        fallback_model="$(suggest_model_from_detection "$detection_json")"
    fi

    printf '%s' "$(prompt_line "$prompt_label" "$fallback_model")"
}

prompt_optional_api_base() {
    local current_base="$1"
    local custom_base

    if prompt_yes_no "Override the default API base" "n"; then
        custom_base="$(prompt_line "API base URL" "$current_base")"
        printf '%s' "$custom_base"
        return
    fi
    printf '%s' "$current_base"
}

choose_provider() {
    local reply
    local items=()
    local line

    while IFS= read -r line; do
        [ -n "$line" ] && items+=("$line")
    done < <(provider_menu_entries)

    reply="$(menu_select "Provider setup" 0 "${items[@]}")"
    SELECTED_PROVIDER="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]' | tr '-' '_')"
}

collect_provider_settings() {
    info "Starting interactive provider setup..."
    "$AELOON_VENV/bin/aeloon" provider setup --config "$CONFIG_PATH"
    sync_selected_provider_from_config
}

sync_selected_provider_from_config() {
    local selected_fields=()

    while IFS= read -r line; do
        selected_fields+=("$line")
    done < <(CONFIG_PATH="$CONFIG_PATH" "$AELOON_VENV/bin/python" - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["CONFIG_PATH"]).expanduser()
data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
agents = data.get("agents", {}).get("defaults", {})
provider = (agents.get("provider") or "").strip()
providers = data.get("providers", {})
provider_cfg = providers.get(provider, {}) if provider else {}
print(provider)
print((agents.get("model") or "").strip())
print(provider_cfg.get("apiKey") or "")
print(provider_cfg.get("apiBase") or "")
PY

)

    SELECTED_PROVIDER="${selected_fields[0]:-}"
    SELECTED_MODEL="${selected_fields[1]:-}"
    PROVIDER_API_KEY="${selected_fields[2]:-}"
    SELECTED_API_BASE="${selected_fields[3]:-}"
}

require_allow_from() {
    local prompt="$1"
    while :; do
        CHANNEL_ALLOW_FROM="$(prompt_line "$prompt" "*")"
        if [ -n "$CHANNEL_ALLOW_FROM" ]; then
            return
        fi
        warn "allowFrom cannot be empty for enabled channels. Use * if you want to allow all senders."
    done
}

collect_channel_settings() {
    local reply
    local items=(
        $'none\tNone - configure channels later'
        $'web\tWeb UI (served by aeloon gateway)'
        $'feishu\tFeishu'
        $'qq\tQQ'
        $'dingtalk\tDingTalk'
        $'email\tEmail'
        $'wechat\tWeChat'
    )

    reply="$(menu_select "Channel setup" 0 "${items[@]}")"
    SELECTED_CHANNEL="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')"

    case "$SELECTED_CHANNEL" in
        none)
            ;;
        web)
            WEB_PORT="$(prompt_port "Web UI / gateway port" "$WEB_PORT")"
            ;;
        feishu)
            CHANNEL_TOKEN="$(prompt_line "Feishu app ID" "")"
            CHANNEL_SECRET="$(prompt_secret "Feishu app secret")"
            require_allow_from "Feishu allowFrom (comma separated user IDs, open IDs, or *)"
            ;;
        qq)
            CHANNEL_TOKEN="$(prompt_line "QQ app ID" "")"
            CHANNEL_SECRET="$(prompt_secret "QQ secret")"
            require_allow_from "QQ allowFrom (comma separated user IDs, or *)"
            ;;
        dingtalk)
            CHANNEL_TOKEN="$(prompt_line "DingTalk client ID" "")"
            CHANNEL_SECRET="$(prompt_secret "DingTalk client secret")"
            require_allow_from "DingTalk allowFrom (comma separated staff IDs, or *)"
            ;;
        email)
            EMAIL_CONSENT_GRANTED="true"
            EMAIL_IMAP_HOST="$(prompt_line "IMAP host" "imap.gmail.com")"
            EMAIL_IMAP_PORT="$(prompt_line "IMAP port" "993")"
            EMAIL_IMAP_USERNAME="$(prompt_line "IMAP username" "")"
            EMAIL_IMAP_PASSWORD="$(prompt_secret "IMAP password or app password")"
            EMAIL_SMTP_HOST="$(prompt_line "SMTP host" "smtp.gmail.com")"
            EMAIL_SMTP_PORT="$(prompt_line "SMTP port" "587")"
            EMAIL_SMTP_USERNAME="$(prompt_line "SMTP username" "$EMAIL_IMAP_USERNAME")"
            EMAIL_SMTP_PASSWORD="$(prompt_secret "SMTP password or app password")"
            EMAIL_FROM_ADDRESS="$(prompt_line "From address" "$EMAIL_SMTP_USERNAME")"
            require_allow_from "Email allowFrom (comma separated email addresses, or *)"
            ;;
        wechat)
            require_allow_from "WeChat allowFrom (comma separated IDs, or *)"
            ;;
    esac
}

apply_config_updates() {
    export CONFIG_PATH
    export SELECTED_PROVIDER PROVIDER_API_KEY SELECTED_MODEL SELECTED_API_BASE
    export SELECTED_CHANNEL CHANNEL_ALLOW_FROM CHANNEL_TOKEN CHANNEL_APP_TOKEN CHANNEL_BRIDGE_TOKEN CHANNEL_SECRET
    export WEB_PORT
    export EMAIL_CONSENT_GRANTED EMAIL_IMAP_HOST EMAIL_IMAP_PORT EMAIL_IMAP_USERNAME EMAIL_IMAP_PASSWORD
    export EMAIL_SMTP_HOST EMAIL_SMTP_PORT EMAIL_SMTP_USERNAME EMAIL_SMTP_PASSWORD EMAIL_FROM_ADDRESS

    "$AELOON_VENV/bin/python" - <<'PY'
import json
import os
from pathlib import Path


def parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


config_path = Path(os.environ["CONFIG_PATH"]).expanduser()
data = json.loads(config_path.read_text(encoding="utf-8"))

agents = data.setdefault("agents", {}).setdefault("defaults", {})
providers = data.setdefault("providers", {})
channels = data.setdefault("channels", {})
gateway = data.setdefault("gateway", {})

provider = os.environ.get("SELECTED_PROVIDER", "").strip()
if provider:
    agents["provider"] = provider
    agents["model"] = os.environ.get("SELECTED_MODEL", "").strip()

    provider_cfg = providers.setdefault(provider, {})
    api_key = os.environ.get("PROVIDER_API_KEY", "")
    api_base = os.environ.get("SELECTED_API_BASE", "")

    if provider not in {"openai_codex", "github_copilot"}:
        provider_cfg["apiKey"] = api_key
    if api_base:
        provider_cfg["apiBase"] = api_base
    elif provider == "ollama":
        provider_cfg["apiBase"] = api_base or "http://127.0.0.1:11434"

channel = os.environ.get("SELECTED_CHANNEL", "none")
allow_from = parse_list(os.environ.get("CHANNEL_ALLOW_FROM", ""))

if channel == "web":
    gateway["port"] = int(os.environ.get("WEB_PORT", "18790") or "18790")
elif channel == "feishu":
    cfg = channels.setdefault("feishu", {})
    cfg["enabled"] = True
    cfg["appId"] = os.environ.get("CHANNEL_TOKEN", "")
    cfg["appSecret"] = os.environ.get("CHANNEL_SECRET", "")
    cfg["allowFrom"] = allow_from
elif channel == "qq":
    cfg = channels.setdefault("qq", {})
    cfg["enabled"] = True
    cfg["appId"] = os.environ.get("CHANNEL_TOKEN", "")
    cfg["secret"] = os.environ.get("CHANNEL_SECRET", "")
    cfg["allowFrom"] = allow_from
elif channel == "dingtalk":
    cfg = channels.setdefault("dingtalk", {})
    cfg["enabled"] = True
    cfg["clientId"] = os.environ.get("CHANNEL_TOKEN", "")
    cfg["clientSecret"] = os.environ.get("CHANNEL_SECRET", "")
    cfg["allowFrom"] = allow_from
elif channel == "wechat":
    cfg = channels.setdefault("wechat", {})
    cfg["enabled"] = True
    cfg["allowFrom"] = allow_from
elif channel == "email":
    cfg = channels.setdefault("email", {})
    cfg["enabled"] = True
    cfg["consentGranted"] = os.environ.get("EMAIL_CONSENT_GRANTED", "false").lower() == "true"
    cfg["imapHost"] = os.environ.get("EMAIL_IMAP_HOST", "")
    cfg["imapPort"] = int(os.environ.get("EMAIL_IMAP_PORT", "993") or "993")
    cfg["imapUsername"] = os.environ.get("EMAIL_IMAP_USERNAME", "")
    cfg["imapPassword"] = os.environ.get("EMAIL_IMAP_PASSWORD", "")
    cfg["smtpHost"] = os.environ.get("EMAIL_SMTP_HOST", "")
    cfg["smtpPort"] = int(os.environ.get("EMAIL_SMTP_PORT", "587") or "587")
    cfg["smtpUsername"] = os.environ.get("EMAIL_SMTP_USERNAME", "")
    cfg["smtpPassword"] = os.environ.get("EMAIL_SMTP_PASSWORD", "")
    cfg["fromAddress"] = os.environ.get("EMAIL_FROM_ADDRESS", "")
    cfg["allowFrom"] = allow_from

config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

run_interactive_setup() {
    if ! have_prompt_tty; then
        warn "No TTY available for prompts. Applying the default OpenRouter free-model setup."
        USED_BUNDLED_DEFAULT=true
        "$AELOON_VENV/bin/aeloon" provider setup --config "$CONFIG_PATH" --bootstrap-default
        sync_selected_provider_from_config
        apply_config_updates
        INTERACTIVE_SETUP_DONE=true
        return
    fi

    prompt_print "\n${BOLD}Interactive setup${RESET}\n"
    prompt_print "We will configure your provider and optionally one channel.\n\n"

    collect_provider_settings
    collect_channel_settings
    apply_config_updates
    INTERACTIVE_SETUP_DONE=true
}

print_next_steps() {
    local agent_cmd="aeloon agent -m \"Hello Aeloon\" --config $CONFIG_PATH"
    local gateway_cmd="aeloon gateway --config $CONFIG_PATH"
    local web_url="http://127.0.0.1:${WEB_PORT}/"
    local web_ui_static_dir=""

    web_ui_static_dir="$(detect_web_ui_static_dir || true)"

    printf "\n${GREEN}Aeloon is installed.${RESET}\n"
    printf '%s\n' "- Home: $AELOON_HOME"
    printf '%s\n' "- Config: $CONFIG_PATH"
    printf '%s\n' "- Workspace: $WORKSPACE_PATH"
    printf '%s\n' "- CLI wrapper: $AELOON_BIN/aeloon"

    if [ -n "$SELECTED_PROVIDER" ]; then
        printf '%s\n' "- Provider: $SELECTED_PROVIDER"
        printf '%s\n' "- Model: $SELECTED_MODEL"
    fi
    if [ "$SELECTED_CHANNEL" != "none" ]; then
        printf '%s\n' "- Channel: $SELECTED_CHANNEL"
    fi
    if [ "$SELECTED_CHANNEL" = "web" ]; then
        printf '%s\n' "- Web URL: $web_url"
    fi

    printf "\nNext steps:\n"
    printf "  1. Reload your shell, or run: export PATH=\"$AELOON_BIN:\$PATH\"\n"
    printf "  2. Chat in CLI: %s\n" "$agent_cmd"
    if [ "$SELECTED_CHANNEL" != "none" ]; then
        printf "  3. Start gateway: %s\n" "$gateway_cmd"
    fi
    if [ "$SELECTED_CHANNEL" = "web" ]; then
        printf "  4. Open the Web UI: %s\n" "$web_url"
        if [ -n "$web_ui_static_dir" ]; then
            printf "     Static UI detected at: %s\n" "$web_ui_static_dir"
        else
            printf "     Build the UI first from this repo with: (cd ui && npm install && npm run build)\n"
        fi
    fi
    if [ "$SELECTED_PROVIDER" = "openai_codex" ]; then
        printf "  4. Authenticate: aeloon provider login openai-codex\n"
    fi
    if [ "$SELECTED_PROVIDER" = "github_copilot" ]; then
        printf "  4. Authenticate: aeloon provider login github-copilot\n"
    fi
    if [ "$SELECTED_CHANNEL" = "wechat" ]; then
        printf "  4. After starting the gateway, use /wechat login in chat to bind an account\n"
    fi
    if [ "$USED_BUNDLED_DEFAULT" = true ]; then
        printf "%s\n" "- Bootstrap: bundled OpenRouter free-model configuration"
    fi
    if [ "$INTERACTIVE_SETUP_DONE" = false ]; then
        printf "\nBefore first use, edit %s and add a provider API key.\n" "$CONFIG_PATH"
    fi
}

info "Installing Aeloon into $AELOON_HOME"
ensure_uv
create_venv
install_aeloon
create_wrapper
update_path
run_onboard
run_interactive_setup
print_next_steps
