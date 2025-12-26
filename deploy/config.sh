# deploy/config.sh
# Interactive configuration gathering for MIRA deployment
# Source this file - do not execute directly
#
# Requires: lib/output.sh and lib/services.sh sourced first
# Requires: LOUD_MODE variable set
#
# Sets: CONFIG_*, STATUS_*, OS, DISTRO

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
