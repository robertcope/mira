#!/bin/bash
set -e

# MIRA Deployment Script
# This script automates the complete deployment of MIRA OSS

# ============================================================================
# VISUAL OUTPUT CONFIGURATION
# ============================================================================

# Parse arguments
LOUD_MODE=false
for arg in "$@"; do
    if [ "$arg" = "--loud" ]; then
        LOUD_MODE=true
    fi
done

# ANSI color codes (muted/professional palette)
RESET='\033[0m'
DIM='\033[2m'
BOLD='\033[1m'
GRAY='\033[38;5;240m'
BLUE='\033[38;5;75m'
GREEN='\033[38;5;77m'
YELLOW='\033[38;5;186m'
RED='\033[38;5;203m'
CYAN='\033[38;5;80m'

# Visual elements
CHECKMARK="${GREEN}✓${RESET}"
ARROW="${CYAN}→${RESET}"
WARNING="${YELLOW}⚠${RESET}"
ERROR="${RED}✗${RESET}"

# Print colored output
print_header() {
    echo -e "\n${BOLD}${BLUE}$1${RESET}"
}

print_step() {
    echo -e "${DIM}${ARROW}${RESET} $1"
}

print_success() {
    echo -e "${CHECKMARK} ${GREEN}$1${RESET}"
}

print_warning() {
    echo -e "${WARNING} ${YELLOW}$1${RESET}"
}

print_error() {
    echo -e "${ERROR} ${RED}$1${RESET}"
}

print_info() {
    echo -e "${DIM}  $1${RESET}"
}

# Execute command with optional output suppression
run_quiet() {
    if [ "$LOUD_MODE" = true ]; then
        "$@"
    else
        "$@" > /dev/null 2>&1
    fi
}

run_with_status() {
    local msg="$1"
    shift

    if [ "$LOUD_MODE" = true ]; then
        print_step "$msg"
        "$@"
    else
        echo -ne "${DIM}${ARROW}${RESET} $msg... "
        if "$@" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            return 1
        fi
    fi
}

# Progress spinner for long operations
show_progress() {
    local pid=$1
    local msg=$2
    local spin='-\|/'
    local i=0

    if [ "$LOUD_MODE" = true ]; then
        wait $pid
        return $?
    fi

    echo -ne "${DIM}${ARROW}${RESET} $msg... "
    while kill -0 $pid 2>/dev/null; do
        i=$(( (i+1) %4 ))
        echo -ne "\r${DIM}${ARROW}${RESET} $msg... ${spin:$i:1}"
        sleep 0.1
    done

    wait $pid
    local status=$?
    if [ $status -eq 0 ]; then
        echo -e "\r${DIM}${ARROW}${RESET} $msg... ${CHECKMARK}"
    else
        echo -e "\r${DIM}${ARROW}${RESET} $msg... ${ERROR}"
    fi
    return $status
}

# ============================================================================
# UNIFIED HELPER FUNCTIONS
# ============================================================================

# Check if something exists with consistent pattern
# Usage: check_exists TYPE TARGET [EXTRA]
# Types: file, dir, command, package, db, db_user, service_systemctl, service_brew
check_exists() {
    local type="$1"
    local target="$2"
    local extra="$3"

    case "$type" in
        file)
            [ -f "$target" ]
            ;;
        dir)
            [ -d "$target" ]
            ;;
        command)
            command -v "$target" &> /dev/null
            ;;
        package)
            venv/bin/pip3 show "$target" &> /dev/null
            ;;
        db)
            if [ "$OS" = "linux" ]; then
                sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw "$target"
            else
                psql -lqt | cut -d \| -f 1 | grep -qw "$target"
            fi
            ;;
        db_user)
            if [ "$OS" = "linux" ]; then
                sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$target'" | grep -q 1
            else
                psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$target'" 2>/dev/null | grep -q 1
            fi
            ;;
        service_systemctl)
            systemctl is-active --quiet "$target" 2>/dev/null
            ;;
        service_brew)
            brew services list 2>/dev/null | grep -q "${target}.*started"
            ;;
    esac
}

# Start service with idempotency check
# Usage: start_service SERVICE_NAME SERVICE_TYPE
# Types: systemctl, brew, background (for custom processes)
start_service() {
    local service_name="$1"
    local service_type="$2"

    case "$service_type" in
        systemctl)
            if check_exists service_systemctl "$service_name"; then
                print_info "$service_name already running"
                return 0
            fi
            run_with_status "Starting $service_name" \
                sudo systemctl start "$service_name"
            ;;
        brew)
            if check_exists service_brew "$service_name"; then
                print_info "$service_name already running"
                return 0
            fi
            run_with_status "Starting $service_name" \
                brew services start "$service_name"
            ;;
        background)
            print_error "Background service type requires custom implementation"
            return 1
            ;;
    esac
}

# Stop service with consistent pattern
# Usage: stop_service SERVICE_NAME SERVICE_TYPE [EXTRA]
# Types: systemctl, brew, pid_file (EXTRA=pid_file_path), port (EXTRA=port_number)
stop_service() {
    local service_name="$1"
    local service_type="$2"
    local extra="$3"

    case "$service_type" in
        systemctl)
            if ! check_exists service_systemctl "$service_name"; then
                return 0  # Already stopped
            fi
            run_with_status "Stopping $service_name" \
                sudo systemctl stop "$service_name"
            ;;
        brew)
            if ! check_exists service_brew "$service_name"; then
                return 0  # Already stopped
            fi
            run_with_status "Stopping $service_name" \
                brew services stop "$service_name"
            ;;
        pid_file)
            local pid_file="$extra"
            if [ ! -f "$pid_file" ]; then
                return 0  # PID file doesn't exist
            fi
            local pid=$(cat "$pid_file")
            if ! kill -0 "$pid" 2>/dev/null; then
                rm -f "$pid_file"  # Clean up stale PID file
                return 0
            fi
            kill "$pid" 2>/dev/null && rm -f "$pid_file"
            ;;
        port)
            local port="$extra"
            if command -v lsof &> /dev/null; then
                local pids=$(lsof -ti ":$port" 2>/dev/null)
                if [ -z "$pids" ]; then
                    return 0  # Nothing on port
                fi
                kill $pids 2>/dev/null
            fi
            ;;
    esac
}

# Write file only if content has changed
# Usage: write_file_if_changed FILEPATH CONTENT
write_file_if_changed() {
    local target_file="$1"
    local content="$2"

    if [ -f "$target_file" ]; then
        local existing_content=$(cat "$target_file")
        if [ "$existing_content" = "$content" ]; then
            return 1  # File unchanged
        fi
    fi

    echo "$content" > "$target_file"
    return 0
}

# Install Python package if not already installed
# Usage: install_python_package PACKAGE_NAME
install_python_package() {
    local package="$1"

    if check_exists package "$package"; then
        local version=$(venv/bin/pip3 show "$package" | grep Version | awk '{print $2}')
        echo -e "${CHECKMARK} ${DIM}$version (already installed)${RESET}"
        return 0
    fi

    if [ "$LOUD_MODE" = true ]; then
        print_step "Installing $package..."
        venv/bin/pip3 install "$package"
    else
        (venv/bin/pip3 install -q "$package") &
        show_progress $! "Installing $package"
    fi
}

# Vault helper: Check if Vault is initialized
vault_is_initialized() {
    check_exists file "/opt/vault/init-keys.txt"
}

# Vault helper: Check if Vault is sealed
vault_is_sealed() {
    # vault status returns:
    # - exit code 0 when unsealed
    # - exit code 2 when sealed
    # - exit code 1 on error
    vault status > /dev/null 2>&1
    local exit_code=$?

    if [ $exit_code -eq 2 ]; then
        return 0  # Sealed (true)
    elif [ $exit_code -eq 0 ]; then
        return 1  # Unsealed (false)
    else
        # Error - assume sealed to be safe
        return 0
    fi
}

# Vault helper: Extract credential from init-keys.txt
# Usage: vault_extract_credential "Unseal Key 1" or "Initial Root Token"
vault_extract_credential() {
    local cred_type="$1"

    # Debug output in loud mode (to stderr so it doesn't pollute command substitution)
    if [ "$LOUD_MODE" = true ]; then
        echo "" >&2
        echo "DEBUG: Contents of /opt/vault/init-keys.txt:" >&2
        cat /opt/vault/init-keys.txt >&2
        echo "" >&2
        echo "DEBUG: Attempting to extract: $cred_type" >&2
    fi

    grep "$cred_type" /opt/vault/init-keys.txt | awk '{print $NF}'
}

# Vault helper: Unseal vault if sealed
vault_unseal() {
    if ! vault_is_sealed; then
        return 0  # Already unsealed
    fi

    local unseal_key=$(vault_extract_credential "Unseal Key 1")
    if [ -z "$unseal_key" ]; then
        print_error "Cannot unseal: unseal key not found in init-keys.txt"
        return 1
    fi

    run_with_status "Unsealing Vault" \
        vault operator unseal "$unseal_key"
}

# Vault helper: Authenticate with root token
vault_authenticate() {
    if ! vault_is_initialized; then
        print_error "Cannot authenticate: Vault not initialized"
        return 1
    fi

    local root_token=$(vault_extract_credential "Initial Root Token")
    if [ -z "$root_token" ]; then
        print_error "Cannot authenticate: root token not found in init-keys.txt"
        return 1
    fi

    run_with_status "Authenticating with Vault" vault login "$root_token"
}

# Vault helper: Check if AppRole exists
vault_approle_exists() {
    vault read auth/approle/role/mira > /dev/null 2>&1
}

# Vault helper: Full initialization orchestration
vault_initialize() {
    if vault_is_initialized; then
        print_info "Vault already initialized - checking state"

        # Unseal if needed (checks sealed state first)
        vault_unseal || return 1

        # Authenticate with root token
        vault_authenticate || return 1

        # Ensure KV2 secrets engine is enabled
        if ! vault secrets list | grep -q "^secret/"; then
            run_with_status "Enabling KV2 secrets engine" \
                vault secrets enable -version=2 -path=secret kv
        fi

        # Ensure AppRole exists
        if ! vault_approle_exists; then
            print_info "AppRole not found - creating it"

            # Enable AppRole if not enabled
            vault auth enable approle 2>/dev/null || true

            # Create policy if needed
            if ! vault policy read mira-policy > /dev/null 2>&1; then
                cat > /tmp/mira-policy.hcl <<'EOF'
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/*" {
  capabilities = ["list", "read", "delete"]
}
EOF
                run_with_status "Writing policy to Vault" \
                    vault policy write mira-policy /tmp/mira-policy.hcl
            fi

            run_with_status "Creating AppRole" \
                vault write auth/approle/role/mira policies="mira-policy" token_ttl=1h token_max_ttl=4h
        fi

        # Ensure role-id and secret-id files exist
        if [ ! -f /opt/vault/role-id.txt ]; then
            vault read -field=role_id auth/approle/role/mira/role-id > /opt/vault/role-id.txt
        fi
        if [ ! -f /opt/vault/secret-id.txt ]; then
            vault write -field=secret_id -f auth/approle/role/mira/secret-id > /opt/vault/secret-id.txt
        fi

        return 0
    fi

    # Full initialization for new Vault
    echo -ne "${DIM}${ARROW}${RESET} Initializing Vault... "
    if vault operator init -key-shares=1 -key-threshold=1 > /opt/vault/init-keys.txt 2>&1; then
        echo -e "${CHECKMARK}"
        chmod 600 /opt/vault/init-keys.txt
    else
        echo -e "${ERROR}"
        print_error "Failed to initialize Vault"
        return 1
    fi

    vault_unseal || return 1
    vault_authenticate || return 1

    # Enable KV2 secrets engine
    run_with_status "Enabling KV2 secrets engine" \
        vault secrets enable -version=2 -path=secret kv

    # Enable AppRole authentication
    run_with_status "Enabling AppRole authentication" \
        vault auth enable approle

    # Create policy
    cat > /tmp/mira-policy.hcl <<'EOF'
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/*" {
  capabilities = ["list", "read", "delete"]
}
EOF

    run_with_status "Writing policy to Vault" \
        vault policy write mira-policy /tmp/mira-policy.hcl

    run_with_status "Creating AppRole" \
        vault write auth/approle/role/mira policies="mira-policy" token_ttl=1h token_max_ttl=4h

    # Extract credentials
    vault read -field=role_id auth/approle/role/mira/role-id > /opt/vault/role-id.txt
    vault write -field=secret_id -f auth/approle/role/mira/secret-id > /opt/vault/secret-id.txt
}

# Vault helper: Store secret only if it doesn't exist
# Usage: vault_put_if_not_exists SECRET_PATH KEY1=VALUE1 KEY2=VALUE2 ...
vault_put_if_not_exists() {
    local secret_path="$1"
    shift

    if vault kv get "$secret_path" &> /dev/null; then
        print_info "Secret already exists at $secret_path (preserving existing values)"
        return 0
    fi

    if ! run_with_status "Storing secret at $secret_path" vault kv put "$secret_path" "$@"; then
        print_error "Failed to store secret at $secret_path - deployment cannot continue"
        exit 1
    fi
}

# ============================================================================
# DEPLOYMENT START
# ============================================================================

# Initialize configuration state (using simple variables for Bash 3.x compatibility)
CONFIG_ANTHROPIC_KEY=""
CONFIG_ANTHROPIC_BATCH_KEY=""
CONFIG_PROVIDER_KEY=""
CONFIG_KAGI_KEY=""
CONFIG_DB_PASSWORD=""
CONFIG_INSTALL_PLAYWRIGHT=""
CONFIG_INSTALL_SYSTEMD=""
CONFIG_START_MIRA_NOW=""
CONFIG_OFFLINE_MODE=""
CONFIG_OLLAMA_MODEL=""
STATUS_ANTHROPIC=""
STATUS_ANTHROPIC_BATCH=""
STATUS_PROVIDER_KEY=""
STATUS_KAGI=""
STATUS_DB_PASSWORD=""
STATUS_PLAYWRIGHT=""
STATUS_SYSTEMD=""
STATUS_MIRA_SERVICE=""
CONFIG_PROVIDER_NAME=""
CONFIG_PROVIDER_ENDPOINT=""
CONFIG_PROVIDER_KEY_PREFIX=""
CONFIG_PROVIDER_MODEL=""
STATUS_PROVIDER=""

clear
echo -e "${BOLD}${CYAN}"
echo "╔════════════════════════════════════════╗"
echo "║   MIRA Deployment Script (main)        ║"
echo "╚════════════════════════════════════════╝"
echo -e "${RESET}"
[ "$LOUD_MODE" = true ] && print_info "Running in verbose mode (--loud)"
echo ""

print_header "Pre-flight Checks"

# Check available disk space (need at least 10GB)
echo -ne "${DIM}${ARROW}${RESET} Checking disk space... "
AVAILABLE_SPACE=$(df /opt 2>/dev/null | awk 'NR==2 {print $4}' || df / | awk 'NR==2 {print $4}')
REQUIRED_SPACE=10485760  # 10GB in KB
if [ "$AVAILABLE_SPACE" -lt "$REQUIRED_SPACE" ]; then
    echo -e "${ERROR}"
    print_error "Insufficient disk space. Need at least 10GB free, found $(($AVAILABLE_SPACE / 1024 / 1024))GB"
    exit 1
fi
echo -e "${CHECKMARK}"

# Check if installation already exists
if [ -d "/opt/mira/app" ]; then
    echo ""
    print_warning "Existing MIRA installation found at /opt/mira/app"
    read -p "$(echo -e ${YELLOW}This will OVERWRITE the existing installation. Continue? ${RESET})(y/n): " OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Yy](es)?$ ]]; then
        print_info "Installation cancelled."
        exit 0
    fi
    print_info "Proceeding with overwrite..."
    echo ""
fi

print_success "Pre-flight checks passed"

# Detect operating system (needed for port stop logic and later steps)
OS_TYPE=$(uname -s)
case "$OS_TYPE" in
    Linux*)
        OS="linux"
        # Detect Linux distribution family
        if [ -f /etc/redhat-release ] || [ -f /etc/fedora-release ]; then
            DISTRO="fedora"
        elif [ -f /etc/debian_version ]; then
            DISTRO="debian"
        else
            # Fall back to checking /etc/os-release
            if [ -f /etc/os-release ]; then
                . /etc/os-release
                case "$ID" in
                    fedora|rhel|centos|rocky|alma)
                        DISTRO="fedora"
                        ;;
                    debian|ubuntu|linuxmint|pop)
                        DISTRO="debian"
                        ;;
                    *)
                        # Check ID_LIKE for derivatives
                        case "$ID_LIKE" in
                            *fedora*|*rhel*)
                                DISTRO="fedora"
                                ;;
                            *debian*|*ubuntu*)
                                DISTRO="debian"
                                ;;
                            *)
                                DISTRO="unknown"
                                ;;
                        esac
                        ;;
                esac
            else
                DISTRO="unknown"
            fi
        fi
        ;;
    Darwin*)
        OS="macos"
        DISTRO=""
        ;;
    *)
        echo ""
        print_error "Unsupported operating system: $OS_TYPE"
        print_info "Supported: Linux (Debian/Ubuntu, Fedora/RHEL/CentOS) and macOS"
        print_info "For other platforms, see manual installation: docs/MANUAL_INSTALL.md"
        exit 1
        ;;
esac

print_header "Port Availability Check"

echo -ne "${DIM}${ARROW}${RESET} Checking ports 1993, 8200, 6379, 5432... "
PORTS_IN_USE=""
for PORT in 1993 8200 6379 5432; do
    if command -v lsof &> /dev/null; then
        if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
            PORTS_IN_USE="$PORTS_IN_USE $PORT"
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -an | grep -q "LISTEN.*:$PORT"; then
            PORTS_IN_USE="$PORTS_IN_USE $PORT"
        fi
    fi
done

if [ -n "$PORTS_IN_USE" ]; then
    echo -e "${WARNING}"
    print_warning "The following ports are already in use:$PORTS_IN_USE"
    print_info "MIRA requires: 1993 (app), 8200 (vault), 6379 (valkey), 5432 (postgresql)"
    read -p "$(echo -e ${YELLOW}Stop existing services and continue?${RESET}) (y/n): " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy](es)?$ ]]; then
        print_info "Installation cancelled. Free up the required ports and try again."
        exit 0
    fi
    echo ""

    # Stop services on occupied ports using unified stop_service function
    print_info "Stopping services on occupied ports..."
    for PORT in $PORTS_IN_USE; do
        case $PORT in
            8200)
                # Vault - canonical method per OS, fallback to port-based stop
                if [ "$OS" = "linux" ]; then
                    echo -ne "${DIM}${ARROW}${RESET} Stopping Vault (port 8200)... "
                    if check_exists service_systemctl vault; then
                        stop_service vault systemctl && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "Vault" port 8200 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                elif [ "$OS" = "macos" ]; then
                    echo -ne "${DIM}${ARROW}${RESET} Stopping Vault (port 8200)... "
                    if [ -f /opt/vault/vault.pid ]; then
                        stop_service "Vault" pid_file /opt/vault/vault.pid && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "Vault" port 8200 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                fi
                ;;
            6379)
                # Valkey - canonical method per OS
                echo -ne "${DIM}${ARROW}${RESET} Stopping Valkey (port 6379)... "
                if [ "$OS" = "linux" ]; then
                    if check_exists service_systemctl valkey; then
                        stop_service valkey systemctl && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "Valkey" port 6379 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                elif [ "$OS" = "macos" ]; then
                    if check_exists service_brew valkey; then
                        stop_service valkey brew && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "Valkey" port 6379 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                fi
                ;;
            5432)
                # PostgreSQL - canonical method per OS
                echo -ne "${DIM}${ARROW}${RESET} Stopping PostgreSQL (port 5432)... "
                if [ "$OS" = "linux" ]; then
                    # Fedora/RHEL uses postgresql-17 service name, Debian uses postgresql
                    if check_exists service_systemctl postgresql-17; then
                        stop_service postgresql-17 systemctl && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    elif check_exists service_systemctl postgresql; then
                        stop_service postgresql systemctl && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "PostgreSQL" port 5432 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                elif [ "$OS" = "macos" ]; then
                    if check_exists service_brew postgresql@17; then
                        stop_service postgresql@17 brew && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    else
                        stop_service "PostgreSQL" port 5432 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                    fi
                fi
                ;;
            1993)
                # MIRA - canonical method per OS
                echo -ne "${DIM}${ARROW}${RESET} Stopping MIRA (port 1993)... "
                if [ "$OS" = "linux" ] && check_exists service_systemctl mira; then
                    stop_service mira systemctl && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                else
                    stop_service "MIRA" port 1993 && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                fi
                ;;
            *)
                # Unknown service - use port-based stop
                echo -ne "${DIM}${ARROW}${RESET} Stopping process on port $PORT... "
                stop_service "Unknown" port $PORT && echo -e "${CHECKMARK}" || echo -e "${WARNING}"
                ;;
        esac
    done
    echo ""
else
    echo -e "${CHECKMARK}"
fi

print_success "Port check passed"

print_header "API Key Configuration"

# Offline mode option
echo -e "${BOLD}${BLUE}Run Mode${RESET}"
print_info "MIRA can run offline using local Ollama - no API keys needed."
print_info "To switch to online mode later, just add API keys to Vault."
read -p "$(echo -e ${CYAN}Run offline only?${RESET}) (y/n, default=n): " OFFLINE_MODE_INPUT
if [[ "$OFFLINE_MODE_INPUT" =~ ^[Yy](es)?$ ]]; then
    CONFIG_OFFLINE_MODE="yes"
    # Use placeholder keys so Vault validation passes - these won't actually work
    CONFIG_ANTHROPIC_KEY="OFFLINE_MODE_PLACEHOLDER"
    CONFIG_ANTHROPIC_BATCH_KEY="OFFLINE_MODE_PLACEHOLDER"
    CONFIG_PROVIDER_KEY="OFFLINE_MODE_PLACEHOLDER"
    CONFIG_KAGI_KEY=""
    STATUS_ANTHROPIC="${DIM}Offline mode${RESET}"
    STATUS_ANTHROPIC_BATCH="${DIM}Offline mode${RESET}"
    STATUS_PROVIDER_KEY="${DIM}Offline mode${RESET}"
    STATUS_KAGI="${DIM}Offline mode${RESET}"

    # Ask for model name
    read -p "$(echo -e ${CYAN}Ollama model to use${RESET}) (default: qwen3:1.7b): " OLLAMA_MODEL_INPUT
    if [ -z "$OLLAMA_MODEL_INPUT" ]; then
        CONFIG_OLLAMA_MODEL="qwen3:1.7b"
    else
        CONFIG_OLLAMA_MODEL="$OLLAMA_MODEL_INPUT"
    fi

    # Store model name for later config patching (after files are copied)
    CONFIG_PATCH_OLLAMA_MODEL="$CONFIG_OLLAMA_MODEL"
else
    CONFIG_OFFLINE_MODE="no"

    # Anthropic API Key (required for online mode)
    echo -e "${BOLD}${BLUE}1. Anthropic API Key${RESET} ${DIM}(REQUIRED - console.anthropic.com/settings/keys)${RESET}"
    while true; do
        read -p "$(echo -e ${CYAN}Enter key${RESET}) (or Enter to skip): " ANTHROPIC_KEY_INPUT
        if [ -z "$ANTHROPIC_KEY_INPUT" ]; then
            CONFIG_ANTHROPIC_KEY="PLACEHOLDER_SET_THIS_LATER"
            STATUS_ANTHROPIC="${WARNING} NOT SET - You must configure this before using MIRA"
            break
        fi
        # Basic validation - check if it looks like an Anthropic key
        if [[ $ANTHROPIC_KEY_INPUT =~ ^sk-ant- ]]; then
            CONFIG_ANTHROPIC_KEY="$ANTHROPIC_KEY_INPUT"
            STATUS_ANTHROPIC="${CHECKMARK} Configured"
            break
        else
            print_warning "This doesn't look like a valid Anthropic API key (should start with 'sk-ant-')"
            read -p "$(echo -e ${YELLOW}Continue anyway?${RESET}) (y=yes, n=exit, t=try again): " CONFIRM
            if [[ "$CONFIRM" =~ ^[Yy](es)?$ ]]; then
                CONFIG_ANTHROPIC_KEY="$ANTHROPIC_KEY_INPUT"
                STATUS_ANTHROPIC="${CHECKMARK} Configured (unvalidated)"
                break
            elif [[ "$CONFIRM" =~ ^[Tt](ry)?$ ]]; then
                continue
            else
                CONFIG_ANTHROPIC_KEY="PLACEHOLDER_SET_THIS_LATER"
                STATUS_ANTHROPIC="${WARNING} NOT SET"
                break
            fi
        fi
    done

    # Anthropic Batch API Key (optional - for background memory processing)
    echo -e "${BOLD}${BLUE}1b. Anthropic Batch API Key${RESET} ${DIM}(OPTIONAL - separate key for batch operations)${RESET}"
    echo -e "${DIM}    Leave blank to use the same key as above. Separate keys allow independent rate limits and cost tracking.${RESET}"
    while true; do
        read -p "$(echo -e ${CYAN}Enter batch key${RESET}) (or Enter to use main key): " ANTHROPIC_BATCH_KEY_INPUT
        if [ -z "$ANTHROPIC_BATCH_KEY_INPUT" ]; then
            # Use same key as main Anthropic key
            CONFIG_ANTHROPIC_BATCH_KEY="$CONFIG_ANTHROPIC_KEY"
            STATUS_ANTHROPIC_BATCH="${DIM}Using main Anthropic key${RESET}"
            break
        fi
        # Basic validation - check if it looks like an Anthropic key
        if [[ $ANTHROPIC_BATCH_KEY_INPUT =~ ^sk-ant- ]]; then
            CONFIG_ANTHROPIC_BATCH_KEY="$ANTHROPIC_BATCH_KEY_INPUT"
            STATUS_ANTHROPIC_BATCH="${CHECKMARK} Configured (separate key)"
            break
        else
            print_warning "This doesn't look like a valid Anthropic API key (should start with 'sk-ant-')"
            read -p "$(echo -e ${YELLOW}Continue anyway?${RESET}) (y=yes, n=use main key, t=try again): " CONFIRM
            if [[ "$CONFIRM" =~ ^[Yy](es)?$ ]]; then
                CONFIG_ANTHROPIC_BATCH_KEY="$ANTHROPIC_BATCH_KEY_INPUT"
                STATUS_ANTHROPIC_BATCH="${CHECKMARK} Configured (unvalidated)"
                break
            elif [[ "$CONFIRM" =~ ^[Tt](ry)?$ ]]; then
                continue
            else
                CONFIG_ANTHROPIC_BATCH_KEY="$CONFIG_ANTHROPIC_KEY"
                STATUS_ANTHROPIC_BATCH="${DIM}Using main Anthropic key${RESET}"
                break
            fi
        fi
    done

    # Generic Provider Selection (for fast inference - OpenAI-compatible)
    echo -e "${BOLD}${BLUE}2. Generic Provider${RESET} ${DIM}(for fast inference - OpenAI-compatible)${RESET}"
    echo -e "${DIM}   Select your preferred provider:${RESET}"
    echo "     1. Groq (default, recommended)"
    echo "     2. OpenRouter"
    echo "     3. Together AI"
    echo "     4. Fireworks AI"
    echo "     5. Cerebras"
    echo "     6. SambaNova"
    echo "     7. Other (custom endpoint)"
    read -p "$(echo -e ${CYAN}Select provider${RESET}) [1-7, default=1]: " PROVIDER_CHOICE

    # Set provider-specific values based on selection
    case "${PROVIDER_CHOICE:-1}" in
        1)
            CONFIG_PROVIDER_NAME="Groq"
            CONFIG_PROVIDER_ENDPOINT="https://api.groq.com/openai/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX="gsk_"
            ;;
        2)
            CONFIG_PROVIDER_NAME="OpenRouter"
            CONFIG_PROVIDER_ENDPOINT="https://openrouter.ai/api/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX="sk-or-"
            ;;
        3)
            CONFIG_PROVIDER_NAME="Together AI"
            CONFIG_PROVIDER_ENDPOINT="https://api.together.xyz/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX=""
            ;;
        4)
            CONFIG_PROVIDER_NAME="Fireworks AI"
            CONFIG_PROVIDER_ENDPOINT="https://api.fireworks.ai/inference/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX=""
            ;;
        5)
            CONFIG_PROVIDER_NAME="Cerebras"
            CONFIG_PROVIDER_ENDPOINT="https://api.cerebras.ai/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX=""
            ;;
        6)
            CONFIG_PROVIDER_NAME="SambaNova"
            CONFIG_PROVIDER_ENDPOINT="https://api.sambanova.ai/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX=""
            ;;
        7)
            CONFIG_PROVIDER_NAME="Custom"
            read -p "$(echo -e ${CYAN}Enter custom endpoint URL${RESET}): " CONFIG_PROVIDER_ENDPOINT
            CONFIG_PROVIDER_KEY_PREFIX=""
            ;;
        *)
            # Invalid selection - default to Groq
            CONFIG_PROVIDER_NAME="Groq"
            CONFIG_PROVIDER_ENDPOINT="https://api.groq.com/openai/v1/chat/completions"
            CONFIG_PROVIDER_KEY_PREFIX="gsk_"
            ;;
    esac

    STATUS_PROVIDER="${CHECKMARK} ${CONFIG_PROVIDER_NAME}"

    # For non-Groq providers, prompt for model name
    if [ "$CONFIG_PROVIDER_NAME" != "Groq" ]; then
        echo ""
        print_info "MIRA needs a model name compatible with ${CONFIG_PROVIDER_NAME}."
        print_info ""
        # Show provider-specific examples
        case "$CONFIG_PROVIDER_NAME" in
            "OpenRouter")
                print_info "OpenRouter free models (append :free for free tier):"
                print_info "  - meta-llama/llama-3.3-70b-instruct:free"
                print_info "  - qwen/qwen-2.5-72b-instruct:free"
                print_info "  - deepseek/deepseek-chat-v3-0324:free"
                print_info "  See: https://openrouter.ai/models?q=free"
                DEFAULT_MODEL="meta-llama/llama-3.3-70b-instruct:free"
                ;;
            "Together AI")
                print_info "Together AI models:"
                print_info "  - meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
                print_info "  - Qwen/Qwen2.5-72B-Instruct-Turbo"
                print_info "  See: https://docs.together.ai/docs/chat-models"
                DEFAULT_MODEL="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
                ;;
            "Fireworks AI")
                print_info "Fireworks AI models:"
                print_info "  - accounts/fireworks/models/llama-v3p1-70b-instruct"
                print_info "  - accounts/fireworks/models/qwen2p5-72b-instruct"
                print_info "  See: https://fireworks.ai/models"
                DEFAULT_MODEL="accounts/fireworks/models/llama-v3p1-70b-instruct"
                ;;
            "Cerebras")
                print_info "Cerebras models:"
                print_info "  - llama-3.3-70b"
                print_info "  See: https://cerebras.ai/inference"
                DEFAULT_MODEL="llama-3.3-70b"
                ;;
            "SambaNova")
                print_info "SambaNova models:"
                print_info "  - Meta-Llama-3.1-70B-Instruct"
                print_info "  See: https://community.sambanova.ai/docs"
                DEFAULT_MODEL="Meta-Llama-3.1-70B-Instruct"
                ;;
            *)
                print_info "Enter your provider's model name."
                DEFAULT_MODEL=""
                ;;
        esac
        echo ""
        if [ -n "$DEFAULT_MODEL" ]; then
            read -p "$(echo -e ${CYAN}Model name${RESET}) [default: ${DEFAULT_MODEL}]: " MODEL_INPUT
            CONFIG_PROVIDER_MODEL="${MODEL_INPUT:-$DEFAULT_MODEL}"
        else
            read -p "$(echo -e ${CYAN}Model name${RESET}): " CONFIG_PROVIDER_MODEL
        fi
        echo ""
    fi

    # Generic Provider API Key (required for online mode)
    echo -e "${BOLD}${BLUE}2b. ${CONFIG_PROVIDER_NAME} API Key${RESET} ${DIM}(REQUIRED)${RESET}"
    while true; do
        read -p "$(echo -e ${CYAN}Enter key${RESET}) (or Enter to skip): " GROQ_KEY_INPUT
        if [ -z "$GROQ_KEY_INPUT" ]; then
            CONFIG_PROVIDER_KEY="PLACEHOLDER_SET_THIS_LATER"
            STATUS_PROVIDER_KEY="${WARNING} NOT SET - You must configure this before using MIRA"
            break
        fi
        # Validate key prefix if provider has one
        if [ -n "$CONFIG_PROVIDER_KEY_PREFIX" ]; then
            if [[ $GROQ_KEY_INPUT =~ ^${CONFIG_PROVIDER_KEY_PREFIX} ]]; then
                CONFIG_PROVIDER_KEY="$GROQ_KEY_INPUT"
                STATUS_PROVIDER_KEY="${CHECKMARK} Configured"
                break
            else
                print_warning "This doesn't look like a valid ${CONFIG_PROVIDER_NAME} API key (should start with '${CONFIG_PROVIDER_KEY_PREFIX}')"
                read -p "$(echo -e ${YELLOW}Continue anyway?${RESET}) (y=yes, n=exit, t=try again): " CONFIRM
                if [[ "$CONFIRM" =~ ^[Yy](es)?$ ]]; then
                    CONFIG_PROVIDER_KEY="$GROQ_KEY_INPUT"
                    STATUS_PROVIDER_KEY="${CHECKMARK} Configured (unvalidated)"
                    break
                elif [[ "$CONFIRM" =~ ^[Tt](ry)?$ ]]; then
                    continue
                else
                    CONFIG_PROVIDER_KEY="PLACEHOLDER_SET_THIS_LATER"
                    STATUS_PROVIDER_KEY="${WARNING} NOT SET"
                    break
                fi
            fi
        else
            # No key prefix validation for this provider
            CONFIG_PROVIDER_KEY="$GROQ_KEY_INPUT"
            STATUS_PROVIDER_KEY="${CHECKMARK} Configured"
            break
        fi
    done

    # Kagi API Key (optional - for web search)
    echo -e "${BOLD}${BLUE}3. Kagi Search API Key${RESET} ${DIM}(OPTIONAL - kagi.com/settings?p=api)${RESET}"
    read -p "$(echo -e ${CYAN}Enter key${RESET}) (or Enter to skip): " KAGI_KEY_INPUT
    if [ -z "$KAGI_KEY_INPUT" ]; then
        CONFIG_KAGI_KEY=""
        STATUS_KAGI="${DIM}Skipped${RESET}"
    else
        CONFIG_KAGI_KEY="$KAGI_KEY_INPUT"
        STATUS_KAGI="${CHECKMARK} Configured"
    fi
fi

# Database Password (optional - defaults to changethisifdeployingpwd)
echo -e "${BOLD}${BLUE}4. Database Password${RESET} ${DIM}(OPTIONAL - default: changethisifdeployingpwd)${RESET}"
read -p "$(echo -e ${CYAN}Enter password${RESET}) (or Enter for default): " DB_PASSWORD_INPUT
if [ -z "$DB_PASSWORD_INPUT" ]; then
    CONFIG_DB_PASSWORD="changethisifdeployingpwd"
    STATUS_DB_PASSWORD="${DIM}Using default password${RESET}"
else
    CONFIG_DB_PASSWORD="$DB_PASSWORD_INPUT"
    STATUS_DB_PASSWORD="${CHECKMARK} Custom password set"
fi

# Playwright Browser Installation (optional)
echo -e "${BOLD}${BLUE}5. Playwright Browser${RESET} ${DIM}(OPTIONAL - for JS-heavy webpage extraction)${RESET}"
read -p "$(echo -e ${CYAN}Install Playwright?${RESET}) (y/n, default=y): " PLAYWRIGHT_INPUT
# Default to yes if user just presses Enter
if [ -z "$PLAYWRIGHT_INPUT" ]; then
    PLAYWRIGHT_INPUT="y"
fi
if [[ "$PLAYWRIGHT_INPUT" =~ ^[Yy](es)?$ ]]; then
    CONFIG_INSTALL_PLAYWRIGHT="yes"
    STATUS_PLAYWRIGHT="${CHECKMARK} Will be installed"
else
    CONFIG_INSTALL_PLAYWRIGHT="no"
    STATUS_PLAYWRIGHT="${YELLOW}Skipped${RESET}"
fi

# Systemd service option (Linux only)
echo -e "${BOLD}${BLUE}6. Systemd Service${RESET} ${DIM}(OPTIONAL - Linux only, auto-start on boot)${RESET}"
if [ "$OS" = "linux" ]; then
    read -p "$(echo -e ${CYAN}Install as systemd service?${RESET}) (y/n): " SYSTEMD_INPUT
    if [[ "$SYSTEMD_INPUT" =~ ^[Yy](es)?$ ]]; then
        CONFIG_INSTALL_SYSTEMD="yes"
        read -p "$(echo -e ${CYAN}Start MIRA now?${RESET}) (y/n): " START_NOW_INPUT
        if [[ "$START_NOW_INPUT" =~ ^[Yy](es)?$ ]]; then
            CONFIG_START_MIRA_NOW="yes"
            STATUS_SYSTEMD="${CHECKMARK} Will be installed and started"
        else
            CONFIG_START_MIRA_NOW="no"
            STATUS_SYSTEMD="${CHECKMARK} Will be installed (not started)"
        fi
    else
        CONFIG_INSTALL_SYSTEMD="no"
        CONFIG_START_MIRA_NOW="no"
        STATUS_SYSTEMD="${DIM}Skipped${RESET}"
    fi
elif [ "$OS" = "macos" ]; then
    CONFIG_INSTALL_SYSTEMD="no"
    CONFIG_START_MIRA_NOW="no"
    STATUS_SYSTEMD="${DIM}N/A (macOS)${RESET}"
fi

echo ""
echo -e "${BOLD}Configuration Summary:${RESET}"
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    echo -e "  Mode:            ${CYAN}Offline (Ollama: ${CONFIG_OLLAMA_MODEL})${RESET}"
else
    echo -e "  Anthropic:       ${STATUS_ANTHROPIC}"
    echo -e "  Anthropic Batch: ${STATUS_ANTHROPIC_BATCH}"
    echo -e "  Provider:        ${STATUS_PROVIDER}"
    echo -e "  Provider Key:    ${STATUS_PROVIDER_KEY}"
    if [ -n "$CONFIG_PROVIDER_MODEL" ]; then
        echo -e "  Provider Model:  ${CYAN}${CONFIG_PROVIDER_MODEL}${RESET}"
    fi
    echo -e "  Kagi:            ${STATUS_KAGI}"
fi
echo -e "  DB Password:     ${STATUS_DB_PASSWORD}"
echo -e "  Playwright:      ${STATUS_PLAYWRIGHT}"
echo -e "  Systemd Service: ${STATUS_SYSTEMD}"
echo ""

print_header "System Detection"

# Display detected operating system
echo -ne "${DIM}${ARROW}${RESET} Detecting operating system... "
case "$OS" in
    linux)
        case "$DISTRO" in
            debian)
                echo -e "${CHECKMARK} ${DIM}Linux (Debian/Ubuntu)${RESET}"
                ;;
            fedora)
                echo -e "${CHECKMARK} ${DIM}Linux (Fedora/RHEL)${RESET}"
                ;;
            *)
                echo -e "${ERROR}"
                print_error "Unsupported Linux distribution"
                print_info "Detected: $([ -f /etc/os-release ] && . /etc/os-release && echo "$PRETTY_NAME" || echo "Unknown")"
                print_info "Supported: Debian/Ubuntu, Fedora/RHEL/CentOS/Rocky/Alma"
                print_info "For other distros, see manual installation: docs/MANUAL_INSTALL.md"
                exit 1
                ;;
        esac
        ;;
    macos)
        echo -e "${CHECKMARK} ${DIM}macOS${RESET}"
        ;;
esac

# Check if running as root
echo -ne "${DIM}${ARROW}${RESET} Checking user privileges... "
if [ "$EUID" -eq 0 ]; then
   echo -e "${ERROR}"
   print_error "Please do not run this script as root."
   exit 1
fi
echo -e "${CHECKMARK}"

print_header "Beginning Installation"

print_info "This script requires sudo privileges for system package installation."
print_info "Please enter your password - the installation will then run unattended."
echo ""
sudo -v

# Keep sudo alive (Linux only)
if [ "$OS" = "linux" ]; then
    while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &
fi

echo ""
print_success "All configuration collected"
print_info "Installation will now proceed unattended (estimated 10-15 minutes)"
print_info "Progress will be displayed as each step completes"
[ "$LOUD_MODE" = false ] && print_info "Use --loud flag to see detailed output"
echo ""
sleep 1

echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${DIM}Some of these steps will take a long time. If the spinner is still going, it hasn't${RESET}"
echo -e "${DIM}error'd or timed out—everything is okay. It could take 15 minutes or more to complete.${RESET}"
echo -e "${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

print_header "Step 1: System Dependencies"

if [ "$OS" = "linux" ] && [ "$DISTRO" = "debian" ]; then
    # Debian/Ubuntu: Add PostgreSQL APT repository for PostgreSQL 17
    if [ ! -f /etc/apt/sources.list.d/pgdg.list ]; then
        run_with_status "Adding PostgreSQL APT repository" \
            bash -c 'sudo apt-get install -y ca-certificates wget > /dev/null 2>&1 && \
                     sudo install -d /usr/share/postgresql-common/pgdg && \
                     sudo wget -q -O /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
                     echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null'
    fi

    # Detect Python version to use (newest available, 3.12+ required)
    PYTHON_VER=$(python3 --version 2>&1 | sed -n 's/Python \([0-9]*\.[0-9]*\).*/\1/p')

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating package lists..."
        sudo apt-get update
        print_step "Installing system packages (Python ${PYTHON_VER})..."
        sudo apt-get install -y \
            build-essential \
            python${PYTHON_VER}-venv \
            python${PYTHON_VER}-dev \
            libpq-dev \
            postgresql-server-dev-17 \
            unzip \
            wget \
            curl \
            postgresql-17 \
            postgresql-contrib \
            postgresql-17-pgvector \
            valkey \
            libatk1.0-0t64 \
            libatk-bridge2.0-0t64 \
            libatspi2.0-0t64 \
            libxcomposite1
    else
        # Silent mode with progress indicator
        (sudo apt-get update > /dev/null 2>&1) &
        show_progress $! "Updating package lists"

        (sudo apt-get install -y \
            build-essential python${PYTHON_VER}-venv python${PYTHON_VER}-dev libpq-dev \
            postgresql-server-dev-17 unzip wget curl postgresql-17 \
            postgresql-contrib postgresql-17-pgvector valkey \
            libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 \
            libxcomposite1 > /dev/null 2>&1) &
        show_progress $! "Installing system packages (18 packages)"
    fi
elif [ "$OS" = "linux" ] && [ "$DISTRO" = "fedora" ]; then
    # Fedora/RHEL: Add PostgreSQL PGDG repository for PostgreSQL 17
    if ! rpm -q pgdg-fedora-repo-latest > /dev/null 2>&1 && ! rpm -q pgdg-redhat-repo-latest > /dev/null 2>&1; then
        if [ -f /etc/fedora-release ]; then
            run_with_status "Adding PostgreSQL PGDG repository" \
                sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/F-$(rpm -E %fedora)-x86_64/pgdg-fedora-repo-latest.noarch.rpm
        else
            # RHEL/CentOS/Rocky/Alma
            run_with_status "Adding PostgreSQL PGDG repository" \
                sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-$(rpm -E %rhel)-x86_64/pgdg-redhat-repo-latest.noarch.rpm
        fi
    fi

    # Disable built-in PostgreSQL module to avoid conflicts
    run_quiet sudo dnf -qy module disable postgresql || true

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating package lists..."
        sudo dnf makecache
        print_step "Installing system packages..."
        sudo dnf install -y \
            @development-tools \
            python3-devel \
            python3-pip \
            libpq-devel \
            postgresql17-server \
            postgresql17-contrib \
            postgresql17-devel \
            pgvector_17 \
            unzip \
            wget \
            curl \
            valkey \
            atk \
            at-spi2-atk \
            at-spi2-core \
            libXcomposite
    else
        # Silent mode with progress indicator
        (sudo dnf makecache > /dev/null 2>&1) &
        show_progress $! "Updating package lists"

        (sudo dnf install -y \
            @development-tools python3-devel python3-pip libpq-devel \
            postgresql17-server postgresql17-contrib postgresql17-devel pgvector_17 \
            unzip wget curl valkey \
            atk at-spi2-atk at-spi2-core libXcomposite > /dev/null 2>&1) &
        show_progress $! "Installing system packages (17 packages)"
    fi

    # Initialize PostgreSQL database cluster if not already done
    if [ ! -d /var/lib/pgsql/17/data/base ]; then
        run_with_status "Initializing PostgreSQL database cluster" \
            sudo /usr/pgsql-17/bin/postgresql-17-setup initdb
    fi

    # Configure pg_hba.conf for password authentication (Fedora defaults to ident)
    PG_HBA="/var/lib/pgsql/17/data/pg_hba.conf"
    if [ -f "$PG_HBA" ]; then
        # Check if already configured for scram-sha-256/md5
        if ! grep -q "^local.*all.*all.*scram-sha-256" "$PG_HBA" 2>/dev/null; then
            run_with_status "Configuring PostgreSQL authentication (scram-sha-256)" \
                bash -c "sudo sed -i 's/^local.*all.*all.*ident/local   all             all                                     scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^local.*all.*all.*peer/local   all             all                                     scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^host.*all.*all.*127.0.0.1.*ident/host    all             all             127.0.0.1\\/32            scram-sha-256/' $PG_HBA && \
                         sudo sed -i 's/^host.*all.*all.*::1.*ident/host    all             all             ::1\\/128                 scram-sha-256/' $PG_HBA"
        fi
    fi

    # Enable and start PostgreSQL service
    run_with_status "Enabling PostgreSQL service" \
        sudo systemctl enable postgresql-17
    run_with_status "Starting PostgreSQL service" \
        sudo systemctl start postgresql-17

    # Enable and start Valkey service
    run_with_status "Enabling Valkey service" \
        sudo systemctl enable valkey
    run_with_status "Starting Valkey service" \
        sudo systemctl start valkey
elif [ "$OS" = "macos" ]; then
    # macOS Homebrew package installation
    # Check if Homebrew is installed
    echo -ne "${DIM}${ARROW}${RESET} Checking for Homebrew... "
    if ! command -v brew &> /dev/null; then
        echo -e "${ERROR}"
        print_error "Homebrew is not installed. Please install Homebrew first:"
        print_info "/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
    echo -e "${CHECKMARK}"

    # Detect Python version to use (newest available, 3.12+ required)
    PYTHON_VER=$(python3 --version 2>&1 | sed -n 's/Python \([0-9]*\.[0-9]*\).*/\1/p')

    if [ "$LOUD_MODE" = true ]; then
        print_step "Updating Homebrew..."
        brew update
        print_step "Adding HashiCorp tap..."
        brew tap hashicorp/tap
        print_step "Installing dependencies via Homebrew (Python ${PYTHON_VER})..."
        brew install python@${PYTHON_VER} wget curl postgresql@17 pgvector valkey hashicorp/tap/vault
    else
        (brew update > /dev/null 2>&1) &
        show_progress $! "Updating Homebrew"

        (brew tap hashicorp/tap > /dev/null 2>&1) &
        show_progress $! "Adding HashiCorp tap"

        (brew install python@${PYTHON_VER} wget curl postgresql@17 pgvector valkey hashicorp/tap/vault > /dev/null 2>&1) &
        show_progress $! "Installing dependencies via Homebrew (7 packages)"
    fi

    print_info "Playwright will install its own browser dependencies"
fi

print_success "System dependencies installed"

# Ollama setup (only for offline mode)
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    print_header "Step 1b: Ollama Setup (Offline Mode)"

    # Install Ollama if not present
    echo -ne "${DIM}${ARROW}${RESET} Checking for Ollama... "
    if command -v ollama &> /dev/null; then
        echo -e "${CHECKMARK} ${DIM}(already installed)${RESET}"
        OLLAMA_INSTALLED=true
    else
        echo -e "${DIM}(not found)${RESET}"
        if [ "$LOUD_MODE" = true ]; then
            print_step "Installing Ollama..."
            if curl -fsSL https://ollama.com/install.sh | sh; then
                OLLAMA_INSTALLED=true
            else
                OLLAMA_INSTALLED=false
            fi
        else
            (curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1) &
            if show_progress $! "Installing Ollama"; then
                OLLAMA_INSTALLED=true
            else
                OLLAMA_INSTALLED=false
            fi
        fi
    fi

    if [ "$OLLAMA_INSTALLED" = true ]; then
        # Start Ollama server if not already running
        echo -ne "${DIM}${ARROW}${RESET} Checking Ollama server... "
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo -e "${CHECKMARK} ${DIM}(already running)${RESET}"
        else
            echo -e "${DIM}(starting)${RESET}"
            # Start server based on OS/init system
            if [ "$OS" = "linux" ] && systemctl is-enabled ollama &>/dev/null 2>&1; then
                run_with_status "Starting Ollama service" \
                    sudo systemctl start ollama
            else
                # Start in background for macOS or non-systemd Linux
                ollama serve > /dev/null 2>&1 &
                OLLAMA_PID=$!
                print_info "Started Ollama server (PID $OLLAMA_PID)"
            fi

            # Wait for server to be ready
            echo -ne "${DIM}${ARROW}${RESET} Waiting for Ollama server... "
            OLLAMA_READY=0
            for i in {1..30}; do
                if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
                    OLLAMA_READY=1
                    break
                fi
                sleep 1
            done

            if [ $OLLAMA_READY -eq 1 ]; then
                echo -e "${CHECKMARK} ${DIM}(ready after ${i}s)${RESET}"
            else
                echo -e "${ERROR}"
                print_warning "Ollama server did not start within 30 seconds"
                print_info "Model pull will be skipped - you can pull manually later:"
                print_info "  ollama serve &"
                print_info "  ollama pull ${CONFIG_OLLAMA_MODEL}"
            fi
        fi

        # Pull the model if server is ready
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            if [ "$LOUD_MODE" = true ]; then
                print_step "Pulling model ${CONFIG_OLLAMA_MODEL}..."
                if ollama pull "$CONFIG_OLLAMA_MODEL"; then
                    print_success "Model ${CONFIG_OLLAMA_MODEL} ready"
                else
                    print_warning "Could not pull model (network unavailable)"
                fi
            else
                (ollama pull "$CONFIG_OLLAMA_MODEL" > /dev/null 2>&1) &
                if show_progress $! "Pulling model ${CONFIG_OLLAMA_MODEL}"; then
                    print_success "Model ${CONFIG_OLLAMA_MODEL} ready"
                else
                    print_warning "Could not pull model (network unavailable)"
                    echo ""
                    print_info "For air-gapped installation, manually transfer the model:"
                    print_info "  1. On a connected machine: ollama pull ${CONFIG_OLLAMA_MODEL}"
                    print_info "  2. Export: ~/.ollama/models -> transfer to this machine"
                    print_info "  3. Or use: ollama create ${CONFIG_OLLAMA_MODEL} -f Modelfile"
                fi
            fi
        fi
    else
        print_warning "Could not install Ollama (network unavailable or blocked)"
        echo ""
        print_info "For air-gapped Ollama installation:"
        print_info "  1. Download Ollama binary from https://ollama.com/download"
        print_info "  2. Transfer and install manually"
        print_info "  3. Transfer model files to ~/.ollama/models"
        print_info "  4. Start Ollama: ollama serve"
    fi

    print_success "Ollama setup complete"
fi

print_header "Step 2: Python Verification"

echo -ne "${DIM}${ARROW}${RESET} Locating Python ${PYTHON_VER}+... "
if [ "$OS" = "linux" ]; then
    # Use the version detected in Step 1
    if ! command -v python${PYTHON_VER} &> /dev/null; then
        echo -e "${ERROR}"
        print_error "Python ${PYTHON_VER} not found after installation."
        exit 1
    fi
    PYTHON_CMD="python${PYTHON_VER}"
elif [ "$OS" = "macos" ]; then
    # Detect macOS Python version
    PYTHON_VER=$(python3 --version 2>&1 | sed -n 's/Python \([0-9]*\.[0-9]*\).*/\1/p')

    # Check common Homebrew locations
    if command -v python${PYTHON_VER} &> /dev/null; then
        PYTHON_CMD="python${PYTHON_VER}"
    elif [ -f "/opt/homebrew/opt/python@${PYTHON_VER}/bin/python${PYTHON_VER}" ]; then
        PYTHON_CMD="/opt/homebrew/opt/python@${PYTHON_VER}/bin/python${PYTHON_VER}"
    elif [ -f "/usr/local/opt/python@${PYTHON_VER}/bin/python${PYTHON_VER}" ]; then
        PYTHON_CMD="/usr/local/opt/python@${PYTHON_VER}/bin/python${PYTHON_VER}"
    else
        echo -e "${ERROR}"
        print_error "Python ${PYTHON_VER} not found. Check Homebrew installation."
        exit 1
    fi
fi

PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo -e "${CHECKMARK} ${DIM}$PYTHON_VERSION${RESET}"

print_header "Step 3: MIRA Download & Installation"

# Determine user/group for ownership
if [ "$OS" = "linux" ]; then
    MIRA_USER="$(whoami)"
    MIRA_GROUP="$(id -gn)"
elif [ "$OS" = "macos" ]; then
    MIRA_USER="$(whoami)"
    MIRA_GROUP="staff"
fi

# Download to /tmp to keep user's home directory clean
cd /tmp

# NOTE: Currently downloads from main branch for active development
# When ready for stable release, change to:
#   wget -q -O mira-X.XX.tar.gz https://github.com/taylorsatula/mira-OSS/archive/refs/tags/X.XX.tar.gz
#   tar -xzf mira-X.XX.tar.gz -C /tmp
#   sudo cp -r /tmp/mira-OSS-X.XX/* /opt/mira/app/
#   rm -f /tmp/mira-X.XX.tar.gz
#   rm -rf /tmp/mira-OSS-X.XX

run_with_status "Downloading MIRA from main branch" \
    wget -q -O mira-main.tar.gz https://github.com/taylorsatula/mira-OSS/archive/refs/heads/main.tar.gz

run_with_status "Creating /opt/mira/app directory" \
    sudo mkdir -p /opt/mira/app

run_with_status "Extracting archive" \
    tar -xzf mira-main.tar.gz -C /tmp

run_with_status "Copying files to /opt/mira/app" \
    sudo cp -r /tmp/mira-OSS-main/* /opt/mira/app/

run_with_status "Setting ownership to $MIRA_USER:$MIRA_GROUP" \
    sudo chown -R $MIRA_USER:$MIRA_GROUP /opt/mira

# Clean up immediately after copying
run_quiet rm -f /tmp/mira-main.tar.gz
run_quiet rm -rf /tmp/mira-OSS-main

print_success "MIRA installed to /opt/mira/app"

# Patch config if offline mode with custom model
if [ -n "$CONFIG_PATCH_OLLAMA_MODEL" ] && [ "$CONFIG_PATCH_OLLAMA_MODEL" != "qwen3:1.7b" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Patching config with model ${CONFIG_PATCH_OLLAMA_MODEL}... "
    if [ "$OS" = "macos" ]; then
        sed -i '' "s|default=\"qwen3:1.7b\"|default=\"${CONFIG_PATCH_OLLAMA_MODEL}\"|" /opt/mira/app/config/config.py
    else
        sed -i "s|default=\"qwen3:1.7b\"|default=\"${CONFIG_PATCH_OLLAMA_MODEL}\"|" /opt/mira/app/config/config.py
    fi
    echo -e "${CHECKMARK}"
fi

# Patch config for offline mode (all LLM endpoints use Ollama instead of Groq)
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Patching config for offline mode... "
    OLLAMA_MODEL="${CONFIG_OLLAMA_MODEL:-qwen3:1.7b}"
    if [ "$OS" = "macos" ]; then
        # Patch database schema - account_tiers for offline mode (endpoint, model, api_key)
        sed -i '' "s|https://api.groq.com/openai/v1/chat/completions|http://localhost:11434/v1/chat/completions|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i '' "s|'qwen/qwen3-32b'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i '' "s|'moonshotai/kimi-k2-instruct-0905'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i '' "s|, 'provider_key')|, NULL)|g" /opt/mira/app/deploy/mira_service_schema.sql
        # Patch database schema - internal_llm for offline mode
        sed -i '' "s|'openai/gpt-oss-20b'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
    else
        # Patch database schema - account_tiers for offline mode (endpoint, model, api_key)
        sed -i "s|https://api.groq.com/openai/v1/chat/completions|http://localhost:11434/v1/chat/completions|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i "s|'qwen/qwen3-32b'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i "s|'moonshotai/kimi-k2-instruct-0905'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        sed -i "s|, 'provider_key')|, NULL)|g" /opt/mira/app/deploy/mira_service_schema.sql
        # Patch database schema - internal_llm for offline mode
        sed -i "s|'openai/gpt-oss-20b'|'${OLLAMA_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
    fi
    echo -e "${CHECKMARK}"

    # Reminder: tools have hardcoded LLM configs
    echo ""
    echo -e "${DIM}NOTE: Tools (web_tool, getcontext_tool) use hardcoded LLM configs.${RESET}"
    echo -e "${DIM}For offline providers, edit the tool config classes directly:${RESET}"
    echo -e "${DIM}  - tools/implementations/web_tool.py (WebToolConfig)${RESET}"
    echo -e "${DIM}  - tools/implementations/getcontext_tool.py (GetContextToolConfig)${RESET}"
    echo ""
fi

# Patch provider endpoint and model if not Groq (after files are copied, before database is created)
if [ "$CONFIG_PROVIDER_NAME" != "Groq" ] && [ -n "$CONFIG_PROVIDER_ENDPOINT" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Patching provider endpoint (${CONFIG_PROVIDER_NAME})... "
    if [ "$OS" = "macos" ]; then
        # Patch database schema - account_tiers and internal_llm endpoints
        sed -i '' "s|https://api.groq.com/openai/v1/chat/completions|${CONFIG_PROVIDER_ENDPOINT}|g" /opt/mira/app/deploy/mira_service_schema.sql
    else
        # Patch database schema - account_tiers and internal_llm endpoints
        sed -i "s|https://api.groq.com/openai/v1/chat/completions|${CONFIG_PROVIDER_ENDPOINT}|g" /opt/mira/app/deploy/mira_service_schema.sql
    fi
    echo -e "${CHECKMARK}"

    # Patch model names if user specified a model
    if [ -n "$CONFIG_PROVIDER_MODEL" ]; then
        echo -ne "${DIM}${ARROW}${RESET} Patching model names (${CONFIG_PROVIDER_MODEL})... "
        if [ "$OS" = "macos" ]; then
            # Patch database schema - account_tiers models
            sed -i '' "s|'qwen/qwen3-32b'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
            sed -i '' "s|'moonshotai/kimi-k2-instruct-0905'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
            # Patch database schema - internal_llm models (execution and analysis only)
            sed -i '' "s|'openai/gpt-oss-20b'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        else
            # Patch database schema - account_tiers models
            sed -i "s|'qwen/qwen3-32b'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
            sed -i "s|'moonshotai/kimi-k2-instruct-0905'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
            # Patch database schema - internal_llm models (execution and analysis only)
            sed -i "s|'openai/gpt-oss-20b'|'${CONFIG_PROVIDER_MODEL}'|g" /opt/mira/app/deploy/mira_service_schema.sql
        fi
        echo -e "${CHECKMARK}"
    fi

    # Reminder: tools have hardcoded LLM configs
    echo ""
    echo -e "${DIM}NOTE: Tools (web_tool, getcontext_tool) use hardcoded LLM configs.${RESET}"
    echo -e "${DIM}For custom providers, edit the tool config classes directly:${RESET}"
    echo -e "${DIM}  - tools/implementations/web_tool.py (WebToolConfig)${RESET}"
    echo -e "${DIM}  - tools/implementations/getcontext_tool.py (GetContextToolConfig)${RESET}"
    echo ""
fi

print_header "Step 4: Python Environment Setup"

cd /opt/mira/app

# Check if venv already exists
echo -ne "${DIM}${ARROW}${RESET} Checking for existing virtual environment... "
if [ -f venv/bin/python3 ]; then
    VENV_PYTHON_VERSION=$(venv/bin/python3 --version 2>&1 | awk '{print $2}')
    echo -e "${CHECKMARK} ${DIM}$VENV_PYTHON_VERSION (existing)${RESET}"
    print_info "Reusing existing virtual environment"
else
    echo -e "${DIM}(not found)${RESET}"
    run_with_status "Creating virtual environment" \
        $PYTHON_CMD -m venv venv

    run_with_status "Initializing pip" \
        venv/bin/python3 -m ensurepip
fi

echo -ne "${DIM}${ARROW}${RESET} Checking PyTorch installation... "
if check_exists package torch; then
    TORCH_VERSION=$(venv/bin/pip3 show torch | grep Version | awk '{print $2}')
    echo -e "${CHECKMARK} ${DIM}$TORCH_VERSION (existing)${RESET}"
    print_info "Note: If you have CUDA-enabled PyTorch, it will be preserved"
else
    echo -e "${DIM}(not installed yet)${RESET}"
    if [ "$LOUD_MODE" = true ]; then
        print_step "Installing PyTorch CPU-only version..."
        venv/bin/pip3 install torch --index-url https://download.pytorch.org/whl/cpu
    else
        (venv/bin/pip3 install -q torch --index-url https://download.pytorch.org/whl/cpu) &
        show_progress $! "Installing PyTorch CPU-only"
    fi
fi

print_header "Step 5: Python Dependencies"

# Count packages in requirements.txt
PACKAGE_COUNT=$(grep -c '^[^#]' requirements.txt 2>/dev/null || echo "many")
echo -e "${DIM}This is the one that is going to take a while (~${PACKAGE_COUNT} packages)${RESET}"
echo ""

if [ "$LOUD_MODE" = true ]; then
    print_step "Installing from requirements.txt..."
    venv/bin/pip3 install -r requirements.txt
else
    (venv/bin/pip3 install -q -r requirements.txt) &
    show_progress $! "Installing Python packages from requirements.txt"
    if [ $? -ne 0 ]; then
        print_error "Failed to install Python packages from requirements.txt"
        print_info "Run with --loud flag to see detailed error output"
        exit 1
    fi
fi

# Install sentence-transformers separately to ensure proper dependency resolution
# (torch, transformers, tokenizers must be installed first from requirements.txt)
echo -ne "${DIM}${ARROW}${RESET} Checking sentence-transformers... "
if ! check_exists package sentence-transformers; then
    echo ""
    install_python_package sentence-transformers
    if [ $? -ne 0 ]; then
        print_error "Failed to install sentence-transformers"
        print_info "Run with --loud flag to see detailed error output"
        exit 1
    fi
else
    install_python_package sentence-transformers  # This will show version if already installed
fi

echo -ne "${DIM}${ARROW}${RESET} Checking spaCy language model... "
if venv/bin/python3 -c "import spacy.util; exit(0 if spacy.util.is_package('en_core_web_lg') else 1)" 2>/dev/null; then
    echo -e "${CHECKMARK} ${DIM}(already installed)${RESET}"
else
    echo -e "${DIM}(not found)${RESET}"
    if [ "$LOUD_MODE" = true ]; then
        print_step "Installing spaCy language model..."
        venv/bin/python3 -m spacy download en_core_web_lg
    else
        (venv/bin/python3 -m spacy download en_core_web_lg > /dev/null 2>&1) &
        show_progress $! "Installing spaCy language model"
    fi
fi

print_success "Python dependencies installed"

print_header "Step 6: Embedding Model Download"

# Download MongoDB leaf embedding model (768d asymmetric retrieval)
echo -ne "${DIM}${ARROW}${RESET} Checking embedding model cache... "
MODEL_CACHED=$(venv/bin/python3 << 'EOF'
from pathlib import Path

cache_dir = Path.home() / ".cache" / "huggingface" / "hub"

def check_model_cached(model_substring):
    """Check if a model is fully cached by looking for model directories and required files"""
    if not cache_dir.exists():
        return False

    model_dirs = [d for d in cache_dir.iterdir() if d.is_dir() and model_substring in d.name]

    for model_dir in model_dirs:
        snapshots_dir = model_dir / "snapshots"
        if snapshots_dir.exists():
            for snapshot in snapshots_dir.iterdir():
                if snapshot.is_dir():
                    has_config = (snapshot / "config.json").exists()
                    has_model = (snapshot / "pytorch_model.bin").exists() or (snapshot / "model.safetensors").exists()
                    if has_config and has_model:
                        return True
    return False

if check_model_cached("mdbr-leaf-ir-asym"):
    print("cached")
else:
    print("not_cached")
EOF
)

if [ "$MODEL_CACHED" = "cached" ]; then
    echo -e "${CHECKMARK} ${DIM}(MongoDB/mdbr-leaf-ir-asym already cached)${RESET}"
    print_info "To re-download: rm -rf ~/.cache/huggingface/hub/*mdbr-leaf*"
else
    echo -e "${DIM}(not found)${RESET}"
    if [ "$LOUD_MODE" = true ]; then
        print_step "Downloading MongoDB/mdbr-leaf-ir-asym embedding model..."
        venv/bin/python3 << 'EOF'
from sentence_transformers import SentenceTransformer
print("→ Loading/downloading MongoDB/mdbr-leaf-ir-asym (768d)...")
SentenceTransformer("MongoDB/mdbr-leaf-ir-asym")
print("✓ mdbr-leaf-ir-asym ready")
EOF
    else
        (venv/bin/python3 << 'EOF'
from sentence_transformers import SentenceTransformer
SentenceTransformer("MongoDB/mdbr-leaf-ir-asym")
EOF
) &
        show_progress $! "Downloading MongoDB/mdbr-leaf-ir-asym embedding model"
    fi
fi

print_success "Embedding model ready"

print_header "Step 7: Playwright Browser Setup"

if [ "${CONFIG_INSTALL_PLAYWRIGHT}" = "yes" ]; then
    # Check if Playwright Chromium is already installed
    PLAYWRIGHT_CACHE="$HOME/.cache/ms-playwright"
    echo -ne "${DIM}${ARROW}${RESET} Checking Playwright cache... "
    if [ -d "$PLAYWRIGHT_CACHE" ] && ls "$PLAYWRIGHT_CACHE"/chromium-* >/dev/null 2>&1; then
        echo -e "${CHECKMARK} ${DIM}(already installed)${RESET}"
        print_info "To update browsers: venv/bin/playwright install chromium"
    else
        echo -e "${DIM}(not found)${RESET}"
        if [ "$LOUD_MODE" = true ]; then
            print_step "Installing Playwright Chromium browser..."
            venv/bin/playwright install chromium
        else
            (venv/bin/playwright install chromium > /dev/null 2>&1) &
            show_progress $! "Installing Playwright Chromium"
        fi
    fi

    # System dependencies - optional, may fail on newer Ubuntu
    if [ "$OS" = "linux" ]; then
        echo -ne "${DIM}${ARROW}${RESET} Installing Playwright system dependencies... "
        if sudo venv/bin/playwright install-deps > /tmp/playwright-deps.log 2>&1; then
            echo -e "${CHECKMARK}"
            rm -f /tmp/playwright-deps.log
        else
            echo -e "${WARNING}"
            print_warning "Some system dependencies failed to install"

            # Extract specific failed packages if possible
            FAILED_PACKAGES=$(grep "Unable to locate package" /tmp/playwright-deps.log 2>/dev/null | sed 's/.*Unable to locate package //' | head -3 | tr '\n' ' ')
            if [ -n "$FAILED_PACKAGES" ]; then
                print_info "Missing packages: $FAILED_PACKAGES"
            fi

            print_info "This is common on Ubuntu 24.04+ due to package name changes"
            print_info "Playwright should still work in headless mode for most sites"
            print_info "Full log saved to: /tmp/playwright-deps.log"
        fi
    elif [ "$OS" = "macos" ]; then
        print_info "Playwright browser dependencies are bundled on macOS"
    fi

    print_success "Playwright configured"
else
    print_info "Playwright installation skipped (user opted out)"
    print_info "Note: Advanced webpage extraction will not be available"
    print_info "Basic HTTP requests and web search will still work"
    print_success "Playwright setup skipped"
fi

print_header "Step 8: HashiCorp Vault Setup"

if [ "$OS" = "linux" ]; then
    # Detect architecture
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)
            VAULT_ARCH="amd64"
            ;;
        aarch64|arm64)
            VAULT_ARCH="arm64"
            ;;
        *)
            print_error "Unsupported architecture: $ARCH"
            exit 1
            ;;
    esac

    cd /tmp
    run_with_status "Downloading Vault 1.18.3 (${VAULT_ARCH})" \
        wget -q https://releases.hashicorp.com/vault/1.18.3/vault_1.18.3_linux_${VAULT_ARCH}.zip

    run_with_status "Extracting Vault binary" \
        unzip -o vault_1.18.3_linux_${VAULT_ARCH}.zip

    run_with_status "Installing to /usr/local/bin" \
        sudo mv vault /usr/local/bin/

    run_quiet sudo chmod +x /usr/local/bin/vault

    # Set SELinux context for vault binary on Fedora/RHEL (if SELinux is enabled)
    if [ "$DISTRO" = "fedora" ] && command -v getenforce &> /dev/null; then
        if [ "$(getenforce)" != "Disabled" ]; then
            run_with_status "Setting SELinux context for Vault binary" \
                sudo chcon -t bin_t /usr/local/bin/vault
        fi
    fi
elif [ "$OS" = "macos" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Verifying Vault installation... "
    if ! command -v vault &> /dev/null; then
        echo -e "${ERROR}"
        print_error "Vault installation failed. Please install manually: brew tap hashicorp/tap && brew install hashicorp/tap/vault"
        exit 1
    fi
    echo -e "${CHECKMARK}"
fi

run_with_status "Creating Vault directories" \
    sudo mkdir -p /opt/vault/data /opt/vault/config /opt/vault/logs

run_with_status "Setting Vault directory ownership" \
    sudo chown -R $MIRA_USER:$MIRA_GROUP /opt/vault

# Set SELinux context for /opt/vault on Fedora/RHEL (if SELinux is enabled)
if [ "$OS" = "linux" ] && [ "$DISTRO" = "fedora" ] && command -v getenforce &> /dev/null; then
    if [ "$(getenforce)" != "Disabled" ]; then
        run_with_status "Setting SELinux context for Vault directories" \
            sudo chcon -R -t var_lib_t /opt/vault
    fi
fi

echo -ne "${DIM}${ARROW}${RESET} Writing Vault configuration... "
cat > /opt/vault/config/vault.hcl <<'EOF'
storage "file" {
  path = "/opt/vault/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = 1
}

api_addr = "http://127.0.0.1:8200"
cluster_addr = "https://127.0.0.1:8201"
ui = true

log_level = "Info"
EOF
echo -e "${CHECKMARK}"

print_header "Step 9: Vault Service Configuration"

if [ "$OS" = "linux" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Creating systemd service... "
    sudo tee /etc/systemd/system/vault.service > /dev/null <<EOF
[Unit]
Description=HashiCorp Vault
Documentation=https://www.vaultproject.io/docs/
Requires=network-online.target
After=network-online.target
ConditionFileNotEmpty=/opt/vault/config/vault.hcl

[Service]
Type=notify
User=$MIRA_USER
Group=$MIRA_GROUP
ProtectSystem=full
ProtectHome=no
PrivateTmp=yes
ExecStart=/usr/local/bin/vault server -config=/opt/vault/config/vault.hcl
ExecReload=/bin/kill --signal HUP \$MAINPID
KillMode=process
KillSignal=SIGINT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=65536
LimitMEMLOCK=infinity

[Install]
WantedBy=multi-user.target
EOF
    echo -e "${CHECKMARK}"

    run_quiet sudo systemctl daemon-reload
    run_with_status "Enabling Vault service" \
        sudo systemctl enable vault.service

    start_service vault.service systemctl
    sleep 2
elif [ "$OS" = "macos" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Starting Vault service... "
    # Start Vault in the background
    vault server -config=/opt/vault/config/vault.hcl > /opt/vault/logs/vault.log 2>&1 &
    VAULT_PID=$!
    echo $VAULT_PID > /opt/vault/vault.pid
    sleep 2

    # Verify Vault started
    if ! kill -0 $VAULT_PID 2>/dev/null; then
        echo -e "${ERROR}"
        print_error "Vault failed to start. Check /opt/vault/logs/vault.log for details."
        exit 1
    fi
    echo -e "${CHECKMARK} ${DIM}PID $VAULT_PID${RESET}"
fi

print_success "Vault service configured and running"

# Wait for Vault to be ready and check initialization state
echo -ne "${DIM}${ARROW}${RESET} Waiting for Vault to be ready... "
export VAULT_ADDR='http://127.0.0.1:8200'
VAULT_READY=0
for i in {1..30}; do
    if curl -s http://127.0.0.1:8200/v1/sys/health > /dev/null 2>&1; then
        VAULT_READY=1
        break
    fi
    sleep 1
done

if [ $VAULT_READY -eq 0 ]; then
    echo -e "${ERROR}"
    print_error "Vault did not become ready within 30 seconds"
    print_info "Check Vault logs: /opt/vault/logs/vault.log"
    exit 1
fi
echo -e "${CHECKMARK} ${DIM}(ready after ${i}s)${RESET}"

print_header "Step 10: Vault Initialization"

# Use unified vault_initialize function (handles check, unseal, auth, policy, AppRole)
vault_initialize
print_success "Vault fully configured"

print_header "Step 11: Auto-Unseal Configuration"

echo -ne "${DIM}${ARROW}${RESET} Creating unseal script... "
cat > /opt/vault/unseal.sh <<'EOF'
#!/bin/bash
export VAULT_ADDR='http://127.0.0.1:8200'
sleep 5
UNSEAL_KEY=$(grep 'Unseal Key 1:' /opt/vault/init-keys.txt | awk '{print $NF}')
vault operator unseal "$UNSEAL_KEY"
EOF
echo -e "${CHECKMARK}"

run_quiet chmod +x /opt/vault/unseal.sh

if [ "$OS" = "linux" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Creating auto-unseal systemd service... "
    sudo tee /etc/systemd/system/vault-unseal.service > /dev/null <<'EOF'
[Unit]
Description=Vault Auto-Unseal
After=vault.service
Requires=vault.service

[Service]
Type=oneshot
ExecStart=/opt/vault/unseal.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    echo -e "${CHECKMARK}"

    run_quiet sudo systemctl daemon-reload
    run_with_status "Enabling auto-unseal service" \
        sudo systemctl enable vault-unseal.service
elif [ "$OS" = "macos" ]; then
    print_info "On macOS, manually unseal Vault after restart using: /opt/vault/unseal.sh"
fi

print_success "Auto-unseal configured"

if [ "$OS" = "macos" ]; then
    print_header "Step 12: Starting Services"

    start_service valkey brew
    start_service postgresql@17 brew

    sleep 2
fi

# Wait for PostgreSQL to be ready to accept connections
echo -ne "${DIM}${ARROW}${RESET} Waiting for PostgreSQL to be ready... "
PG_READY=0
for i in {1..30}; do
    if [ "$OS" = "linux" ]; then
        # On Linux, check with pg_isready (Fedora PGDG uses /usr/pgsql-17/bin/)
        if sudo -u postgres pg_isready > /dev/null 2>&1 || \
           sudo -u postgres /usr/pgsql-17/bin/pg_isready > /dev/null 2>&1; then
            PG_READY=1
            break
        fi
    elif [ "$OS" = "macos" ]; then
        # On macOS, check with pg_isready as current user
        # Homebrew PostgreSQL 17 uses versioned command name
        if pg_isready-17 > /dev/null 2>&1; then
            PG_READY=1
            break
        fi
    fi
    sleep 1
done

if [ $PG_READY -eq 0 ]; then
    echo -e "${ERROR}"
    print_error "PostgreSQL did not become ready within 30 seconds"
    if [ "$OS" = "linux" ]; then
        if [ "$DISTRO" = "fedora" ]; then
            print_info "Check status: systemctl status postgresql-17"
            print_info "Check logs: journalctl -u postgresql-17 -n 50"
        else
            print_info "Check status: systemctl status postgresql"
            print_info "Check logs: journalctl -u postgresql -n 50"
        fi
    elif [ "$OS" = "macos" ]; then
        print_info "Check status: brew services list | grep postgresql"
        print_info "Check logs: brew services info postgresql@17"
    fi
    exit 1
fi
echo -e "${CHECKMARK} ${DIM}(ready after ${i}s)${RESET}"

print_header "Step 13: PostgreSQL Configuration"

# Run schema file - single source of truth for database structure
# Schema file creates: roles, database, extensions, tables, indexes, RLS policies
echo -ne "${DIM}${ARROW}${RESET} Running database schema (roles, tables, indexes, RLS)... "
SCHEMA_FILE="/opt/mira/app/deploy/mira_service_schema.sql"
if [ -f "$SCHEMA_FILE" ]; then
    if [ "$OS" = "linux" ]; then
        # Run as postgres superuser; schema handles CREATE DATABASE and \c
        if sudo -u postgres psql -f "$SCHEMA_FILE" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to run schema file"
            exit 1
        fi
    elif [ "$OS" = "macos" ]; then
        if psql postgres -f "$SCHEMA_FILE" > /dev/null 2>&1; then
            echo -e "${CHECKMARK}"
        else
            echo -e "${ERROR}"
            print_error "Failed to run schema file"
            exit 1
        fi
    fi
else
    echo -e "${ERROR}"
    print_error "Schema file not found: $SCHEMA_FILE"
    exit 1
fi

# Update PostgreSQL passwords if custom password was set
if [ "$CONFIG_DB_PASSWORD" != "changethisifdeployingpwd" ]; then
    echo -ne "${DIM}${ARROW}${RESET} Updating database passwords... "
    if [ "$OS" = "linux" ]; then
        sudo -u postgres psql -c "ALTER USER mira_admin WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1 && \
        sudo -u postgres psql -c "ALTER USER mira_dbuser WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1
    elif [ "$OS" = "macos" ]; then
        psql postgres -c "ALTER USER mira_admin WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1 && \
        psql postgres -c "ALTER USER mira_dbuser WITH PASSWORD '${CONFIG_DB_PASSWORD}';" > /dev/null 2>&1
    fi
    if [ $? -eq 0 ]; then
        echo -e "${CHECKMARK}"
    else
        echo -e "${ERROR}"
        print_warning "Failed to update passwords - you may need to update manually"
    fi
fi

print_success "PostgreSQL configured"

print_header "Step 14: Vault Credential Storage"

# Build api_keys arguments
# Note: mira_api token is generated by the server on first startup via ensure_single_user()
# anthropic_batch_key is for Batch API operations (memory extraction) - may be same as main key
API_KEYS_ARGS="anthropic_key=\"${CONFIG_ANTHROPIC_KEY}\" anthropic_batch_key=\"${CONFIG_ANTHROPIC_BATCH_KEY}\" provider_key=\"${CONFIG_PROVIDER_KEY}\""
if [ -n "$CONFIG_KAGI_KEY" ]; then
    API_KEYS_ARGS="$API_KEYS_ARGS kagi_api_key=\"${CONFIG_KAGI_KEY}\""
fi
eval vault_put_if_not_exists secret/mira/api_keys $API_KEYS_ARGS

vault_put_if_not_exists secret/mira/database \
    admin_url="postgresql://mira_admin:${CONFIG_DB_PASSWORD}@localhost:5432/mira_service" \
    password="${CONFIG_DB_PASSWORD}" \
    username="mira_dbuser" \
    service_url="postgresql://mira_dbuser:${CONFIG_DB_PASSWORD}@localhost:5432/mira_service"

vault_put_if_not_exists secret/mira/services \
    app_url="http://localhost:1993" \
    valkey_url="valkey://localhost:6379"

print_success "All credentials configured in Vault"

print_header "Step 15: MIRA CLI Setup"

echo -ne "${DIM}${ARROW}${RESET} Creating mira wrapper script... "

# Create mira wrapper script that sets Vault environment variables
cat > /opt/mira/mira.sh <<'WRAPPER_EOF'
#!/bin/bash
# MIRA CLI wrapper - sets Vault environment variables for talkto_mira.py

# Save original directory
ORIGINAL_DIR="$(pwd)"

# Set Vault address
export VAULT_ADDR='http://127.0.0.1:8200'

# Check if Vault is running and accessible
if ! curl -s http://127.0.0.1:8200/v1/sys/health > /dev/null 2>&1; then
    echo "Error: Vault is not running at $VAULT_ADDR"
    echo "Start Vault first:"
    echo "  Linux: sudo systemctl start vault"
    echo "  macOS: vault server -config=/opt/vault/config/vault.hcl &"
    exit 1
fi

# Check if Vault is sealed and auto-unseal if needed
# vault status exit codes: 0=unsealed, 2=sealed, 1=error
vault status > /dev/null 2>&1
VAULT_STATUS=$?

if [ $VAULT_STATUS -eq 2 ]; then
    echo "Vault is sealed. Attempting to unseal..."
    if [ -f /opt/vault/init-keys.txt ]; then
        UNSEAL_KEY=$(grep 'Unseal Key 1:' /opt/vault/init-keys.txt | awk '{print $NF}')
        if [ -n "$UNSEAL_KEY" ]; then
            if vault operator unseal "$UNSEAL_KEY" > /dev/null 2>&1; then
                echo "Vault unsealed successfully."
            else
                echo "Error: Failed to unseal Vault"
                exit 1
            fi
        else
            echo "Error: Could not extract unseal key from /opt/vault/init-keys.txt"
            exit 1
        fi
    else
        echo "Error: Vault init-keys.txt not found at /opt/vault/init-keys.txt"
        echo "Run /opt/vault/unseal.sh manually or check Vault configuration."
        exit 1
    fi
elif [ $VAULT_STATUS -eq 1 ]; then
    echo "Error: Could not determine Vault status"
    exit 1
fi

# Set Vault credentials (files contain just the raw value)
export VAULT_ROLE_ID=$(cat /opt/vault/role-id.txt)
export VAULT_SECRET_ID=$(cat /opt/vault/secret-id.txt)

# Change to MIRA app directory
cd /opt/mira/app

# Launch MIRA CLI
/opt/mira/app/venv/bin/python3 /opt/mira/app/talkto_mira.py "$@"

# Return to original directory
cd "$ORIGINAL_DIR"
WRAPPER_EOF
echo -e "${CHECKMARK}"

run_quiet chmod +x /opt/mira/mira.sh

# Add alias to shell RC
if [ "$OS" = "linux" ]; then
    SHELL_RC="$HOME/.bashrc"
elif [ "$OS" = "macos" ]; then
    # macOS typically uses zsh
    if [ -n "$ZSH_VERSION" ] || [ "$SHELL" = "/bin/zsh" ]; then
        SHELL_RC="$HOME/.zshrc"
    else
        SHELL_RC="$HOME/.bash_profile"
    fi
fi

echo -ne "${DIM}${ARROW}${RESET} Adding 'mira' alias to $SHELL_RC... "
if ! grep -q "alias mira=" "$SHELL_RC" 2>/dev/null; then
    echo "alias mira='/opt/mira/mira.sh'" >> "$SHELL_RC"
    echo -e "${CHECKMARK}"
else
    echo -e "${DIM}(already exists)${RESET}"
fi

print_success "MIRA CLI configured"

# Systemd service installation (Linux only, if user opted in)
if [ "${CONFIG_INSTALL_SYSTEMD}" = "yes" ] && [ "$OS" = "linux" ]; then
    print_header "Step 16: Systemd Service Configuration"

    # Extract Vault credentials from files
    echo -ne "${DIM}${ARROW}${RESET} Reading Vault credentials... "
    VAULT_ROLE_ID=$(cat /opt/vault/role-id.txt)
    VAULT_SECRET_ID=$(cat /opt/vault/secret-id.txt)

    if [ -z "$VAULT_ROLE_ID" ] || [ -z "$VAULT_SECRET_ID" ]; then
        echo -e "${ERROR}"
        print_error "Failed to read Vault credentials from /opt/vault/"
        print_info "Skipping systemd service creation"
        CONFIG_INSTALL_SYSTEMD="failed"
        STATUS_MIRA_SERVICE="${ERROR} Configuration failed"
    else
        echo -e "${CHECKMARK}"

        # Create systemd service file
        echo -ne "${DIM}${ARROW}${RESET} Creating systemd service file... "

        # Set correct PostgreSQL service name based on distro
        if [ "$DISTRO" = "fedora" ]; then
            PG_SERVICE="postgresql-17.service"
        else
            PG_SERVICE="postgresql.service"
        fi

        sudo tee /etc/systemd/system/mira.service > /dev/null <<EOF
[Unit]
Description=MIRA - AI Assistant with Persistent Memory
Documentation=https://github.com/taylorsatula/mira-OSS
Requires=vault.service ${PG_SERVICE} valkey.service
After=vault.service ${PG_SERVICE} valkey.service vault-unseal.service
ConditionPathExists=/opt/mira/app/main.py

[Service]
Type=simple
User=$MIRA_USER
Group=$MIRA_GROUP
WorkingDirectory=/opt/mira/app
Environment="VAULT_ADDR=http://127.0.0.1:8200"
Environment="VAULT_ROLE_ID=$VAULT_ROLE_ID"
Environment="VAULT_SECRET_ID=$VAULT_SECRET_ID"
ExecStart=/opt/mira/app/venv/bin/python3 /opt/mira/app/main.py
Restart=on-failure
RestartSec=10
TimeoutStartSec=60
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mira

[Install]
WantedBy=multi-user.target
EOF
        echo -e "${CHECKMARK}"

        # Reload systemd and enable service
        run_quiet sudo systemctl daemon-reload

        run_with_status "Enabling MIRA service for auto-start on boot" \
            sudo systemctl enable mira.service

        print_success "Systemd service configured"
        print_info "Service will auto-start on system boot"

        # Start service if user chose to during configuration
        if [ "${CONFIG_START_MIRA_NOW}" = "yes" ]; then
            echo ""
            start_service mira.service systemctl

            # Give service a moment to start
            sleep 2

            # Check if service started successfully
            if sudo systemctl is-active --quiet mira.service; then
                print_success "MIRA service is running"
                print_info "View logs: journalctl -u mira -f"
                STATUS_MIRA_SERVICE="${CHECKMARK} Running"
            else
                print_warning "MIRA service may have failed to start"
                print_info "Check status: systemctl status mira"
                print_info "View logs: journalctl -u mira -n 50"
                STATUS_MIRA_SERVICE="${ERROR} Start failed"
            fi
        else
            print_info "To start later: sudo systemctl start mira"
            print_info "To view logs: journalctl -u mira -f"
            STATUS_MIRA_SERVICE="${DIM}Not started${RESET}"
        fi
    fi
elif [ "${CONFIG_INSTALL_SYSTEMD}" = "no" ]; then
    print_header "Step 16: Systemd Service Configuration"
    print_info "Skipping systemd service installation (user opted out)"
fi

print_header "Step 17: Cleanup"

if [ "$LOUD_MODE" = true ]; then
    print_step "Flushing pip cache..."
    venv/bin/pip3 cache purge 2>/dev/null || print_info "pip cache purge skipped (cache may be empty)"
else
    run_with_status "Flushing pip cache" \
        venv/bin/pip3 cache purge 2>/dev/null || true
fi

# Remove temporary files silently
run_quiet rm -f /tmp/mira-policy.hcl

if [ "$OS" = "linux" ]; then
    run_quiet rm -f /tmp/vault_1.18.3_linux_*.zip
    run_quiet rm -f /tmp/vault
fi

# Rename deploy script to archive it
echo -ne "${DIM}${ARROW}${RESET} Archiving deploy script... "
SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
SCRIPT_NAME="$(basename "$SCRIPT_PATH")"
SCRIPT_ARCHIVED=""

# Only rename if it's actually a file (not piped from curl)
if [ -f "$SCRIPT_PATH" ] && [ "$SCRIPT_NAME" = "deploy.sh" ]; then
    TIMESTAMP=$(date +%m%d%Y)
    NEW_NAME="deploy-lastrun-${TIMESTAMP}.sh"
    NEW_PATH="$SCRIPT_DIR/$NEW_NAME"

    # If archived version already exists, add a counter
    COUNTER=1
    while [ -f "$NEW_PATH" ]; do
        NEW_NAME="deploy-lastrun-${TIMESTAMP}-${COUNTER}.sh"
        NEW_PATH="$SCRIPT_DIR/$NEW_NAME"
        COUNTER=$((COUNTER + 1))
    done

    mv "$SCRIPT_PATH" "$NEW_PATH"
    SCRIPT_ARCHIVED="$NEW_NAME"
    echo -e "${CHECKMARK} ${DIM}$NEW_NAME${RESET}"
else
    echo -e "${DIM}(skipped - not a file)${RESET}"
fi

print_success "Cleanup complete"

echo ""
echo ""
echo -e "${BOLD}${CYAN}"
echo "╔════════════════════════════════════════╗"
echo "║       Deployment Complete! 🎉          ║"
echo "╚════════════════════════════════════════╝"
echo -e "${RESET}"
echo ""

print_success "MIRA installed to: /opt/mira/app"
print_success "All temporary files cleaned up"
if [ -n "$SCRIPT_ARCHIVED" ]; then
    print_success "Deploy script archived as: $SCRIPT_ARCHIVED"
fi

echo ""
echo -e "${BOLD}${BLUE}Important Files${RESET} ${DIM}(/opt/vault/)${RESET}"
print_info "init-keys.txt (Vault unseal key and root token)"
print_info "role-id.txt (AppRole role ID)"
print_info "secret-id.txt (AppRole secret ID)"
if [ "$OS" = "macos" ]; then
    print_info "vault.pid (Vault process ID)"
fi

echo ""
if [ "$CONFIG_OFFLINE_MODE" = "yes" ]; then
    echo -e "${BOLD}${BLUE}Offline Mode Configuration${RESET}"
    echo -e "  Mode:   ${CYAN}Offline (local Ollama)${RESET}"
    echo -e "  Model:  ${CONFIG_OLLAMA_MODEL}"
    echo ""
    print_info "Ensure Ollama is running: ollama serve"
    print_info "To switch to online mode, add API keys to Vault"
else
    echo -e "${BOLD}${BLUE}API Key Configuration${RESET}"
    echo -e "  Anthropic:       ${STATUS_ANTHROPIC}"
    echo -e "  Anthropic Batch: ${STATUS_ANTHROPIC_BATCH}"
    echo -e "  Provider:        ${STATUS_PROVIDER}"
    echo -e "  Provider Key:    ${STATUS_PROVIDER_KEY}"
    if [ -n "$CONFIG_PROVIDER_MODEL" ]; then
        echo -e "  Provider Model:  ${CYAN}${CONFIG_PROVIDER_MODEL}${RESET}"
    fi
    echo -e "  Kagi:            ${STATUS_KAGI}"

    if [ "${CONFIG_ANTHROPIC_KEY}" = "PLACEHOLDER_SET_THIS_LATER" ] || [ "${CONFIG_PROVIDER_KEY}" = "PLACEHOLDER_SET_THIS_LATER" ]; then
        echo ""
        print_warning "Required API keys not configured!"
        print_info "MIRA will not work until you set both API keys."
        print_info "To configure later, use Vault CLI:"
        echo -e "${DIM}    export VAULT_ADDR='http://127.0.0.1:8200'${RESET}"
        echo -e "${DIM}    vault login <root-token-from-init-keys.txt>${RESET}"
        echo -e "${DIM}    vault kv put secret/mira/api_keys \\\\${RESET}"
        echo -e "${DIM}      anthropic_key=\"sk-ant-your-key\" \\\\${RESET}"
        echo -e "${DIM}      anthropic_batch_key=\"sk-ant-your-key\" \\\\${RESET}"
        echo -e "${DIM}      provider_key=\"your-provider-api-key\" \\\\${RESET}"
        echo -e "${DIM}      kagi_api_key=\"your-kagi-key\"${RESET}"
    fi
fi

echo ""
echo -e "${BOLD}${BLUE}Services Running${RESET}"
if [ "$OS" = "linux" ]; then
    print_info "Valkey: localhost:6379"
    print_info "Vault: http://localhost:8200 (systemd service)"
    print_info "PostgreSQL: localhost:5432 (systemd service)"
    if [ "${CONFIG_INSTALL_SYSTEMD}" = "yes" ]; then
        print_info "MIRA: http://localhost:1993 (systemd service - ${STATUS_MIRA_SERVICE})"
    fi
elif [ "$OS" = "macos" ]; then
    print_info "Valkey: localhost:6379 (brew services)"
    print_info "Vault: http://localhost:8200 (background process)"
    print_info "PostgreSQL: localhost:5432 (brew services)"
fi

echo ""
echo -e "${BOLD}${GREEN}Next Steps${RESET}"
if [ "${CONFIG_INSTALL_SYSTEMD}" = "yes" ] && [ "$OS" = "linux" ]; then
    if [[ "${STATUS_MIRA_SERVICE}" == *"Running"* ]]; then
        echo -e "  ${CYAN}→${RESET} MIRA is running at: ${BOLD}http://localhost:1993${RESET}"
        echo -e "  ${CYAN}→${RESET} Check status: ${BOLD}systemctl status mira${RESET}"
        echo -e "  ${CYAN}→${RESET} View logs: ${BOLD}journalctl -u mira -f${RESET}"
        echo -e "  ${CYAN}→${RESET} Stop MIRA: ${BOLD}sudo systemctl stop mira${RESET}"
    elif [[ "${STATUS_MIRA_SERVICE}" == *"failed"* ]]; then
        echo -e "  ${CYAN}→${RESET} Check logs: ${BOLD}journalctl -u mira -n 50${RESET}"
        echo -e "  ${CYAN}→${RESET} Check status: ${BOLD}systemctl status mira${RESET}"
        echo -e "  ${CYAN}→${RESET} Try starting: ${BOLD}sudo systemctl start mira${RESET}"
    else
        echo -e "  ${CYAN}→${RESET} Start MIRA: ${BOLD}sudo systemctl start mira${RESET}"
        echo -e "  ${CYAN}→${RESET} Check status: ${BOLD}systemctl status mira${RESET}"
        echo -e "  ${CYAN}→${RESET} View logs: ${BOLD}journalctl -u mira -f${RESET}"
    fi
    echo ""
    print_info "MIRA will auto-start on system boot (systemd enabled)"
elif [ "$OS" = "linux" ]; then
    echo -e "  ${CYAN}→${RESET} Run: ${BOLD}source ~/.bashrc && mira${RESET}"
elif [ "$OS" = "macos" ]; then
    echo -e "  ${CYAN}→${RESET} Run: ${BOLD}source $SHELL_RC && mira${RESET}"
fi

echo ""
print_warning "IMPORTANT: Secure /opt/vault/ - it contains sensitive credentials!"

if [ "$OS" = "macos" ]; then
    echo ""
    echo -e "${BOLD}${YELLOW}macOS Notes${RESET}"
    print_info "Vault is running as a background process"
    print_info "To stop: kill \$(cat /opt/vault/vault.pid)"
    print_info "After system restart, manually start Vault and unseal:"
    echo -e "${DIM}    /opt/vault/unseal.sh${RESET}"
    print_info "PostgreSQL and Valkey are managed by brew services"
fi

# Prompt to launch MIRA CLI immediately
echo ""
echo -e "${BOLD}${CYAN}Launch MIRA CLI Now?${RESET}"
print_info "MIRA CLI will auto-start the API server and open an interactive chat."
echo ""
read -p "$(echo -e ${CYAN}Start MIRA CLI now?${RESET}) (yes/no): " LAUNCH_MIRA
if [[ "$LAUNCH_MIRA" =~ ^[Yy](es)?$ ]]; then
    echo ""
    print_success "Launching MIRA CLI..."
    echo ""
    # Set up Vault environment and launch
    export VAULT_ADDR='http://127.0.0.1:8200'
    export VAULT_ROLE_ID=$(cat /opt/vault/role-id.txt)
    export VAULT_SECRET_ID=$(cat /opt/vault/secret-id.txt)
    cd /opt/mira/app
    exec venv/bin/python3 talkto_mira.py
fi

echo ""
