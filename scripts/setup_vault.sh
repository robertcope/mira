#!/bin/bash
set -e

# MIRA Vault Setup Script
# Initializes HashiCorp Vault for MIRA single-user mode with persistent file storage
#
# This script sets up Vault with:
# - File-based storage (persistent, survives restarts)
# - Single key share (simplified key management)
# - AppRole authentication for MIRA
# - Required API keys and default credentials
#
# Note: This is NOT Vault dev mode - data persists to ./vault_data

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VAULT_CONFIG_FILE="${PROJECT_ROOT}/config/vault.hcl"
VAULT_KEYS_FILE="${PROJECT_ROOT}/.vault_keys"
VAULT_DATA_DIR="${PROJECT_ROOT}/vault_data"

# Set Vault address (HTTP not HTTPS since TLS is disabled in config)
export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

# Check if Vault is installed
check_vault_installed() {
    if ! command -v vault &> /dev/null; then
        log_error "Vault is not installed"
        echo ""
        echo "Please install HashiCorp Vault:"
        echo "  macOS:   brew install vault"
        echo "  Linux:   https://developer.hashicorp.com/vault/downloads"
        echo ""
        exit 1
    fi
    log_success "Vault binary found: $(vault version | head -n1)"
}

# Check if Vault server is running
check_vault_running() {
    if vault status &> /dev/null 2>&1; then
        log_success "Vault server is running at ${VAULT_ADDR}"
        return 0
    fi

    log_warning "Vault server is not running"

    # Try to start it
    if [ ! -f "$VAULT_CONFIG_FILE" ]; then
        log_error "Vault config file not found: ${VAULT_CONFIG_FILE}"
        exit 1
    fi

    log_info "Starting Vault server with persistent file storage..."
    log_info "Config: ${VAULT_CONFIG_FILE}"

    # Create vault_data directory if it doesn't exist
    mkdir -p "${VAULT_DATA_DIR}"

    # Try to start Vault
    vault server -config="${VAULT_CONFIG_FILE}" > "${PROJECT_ROOT}/.vault_server.log" 2>&1 &
    VAULT_PID=$!

    # Wait for Vault to start
    sleep 3

    if vault status &> /dev/null 2>&1; then
        log_success "Vault server started (PID: ${VAULT_PID})"
        log_success "Data will be persisted to: ${VAULT_DATA_DIR}"
    else
        log_error "Failed to start Vault server"
        echo ""
        log_info "Last 10 lines of vault log:"
        tail -n 10 "${PROJECT_ROOT}/.vault_server.log"
        echo ""
        log_info "Common issues:"
        echo "  • Port 8200 already in use (check: lsof -i :8200)"
        echo "  • Permissions on vault_data directory"
        echo "  • Check full log: ${PROJECT_ROOT}/.vault_server.log"
        exit 1
    fi
}

# Initialize Vault if needed
initialize_vault() {
    log_info "Checking Vault initialization status..."

    if vault status 2>&1 | grep -q "Initialized.*true"; then
        log_success "Vault is already initialized"
        return 0
    fi

    log_info "Initializing Vault with 1 key share (simplified key management)..."
    log_info "Note: Production deployments should use 3-5 key shares"

    INIT_OUTPUT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json)

    UNSEAL_KEY=$(echo "$INIT_OUTPUT" | jq -r '.unseal_keys_b64[0]')
    ROOT_TOKEN=$(echo "$INIT_OUTPUT" | jq -r '.root_token')

    log_success "Vault initialized with persistent file storage"

    # Save credentials
    cat > "$VAULT_KEYS_FILE" <<EOF
#!/bin/bash
# Vault configuration - KEEP THIS FILE SECURE AND DO NOT COMMIT TO GIT

export VAULT_ADDR='${VAULT_ADDR}'
export VAULT_TOKEN='${ROOT_TOKEN}'
export VAULT_UNSEAL_KEY='${UNSEAL_KEY}'

# AppRole credentials will be added after setup
EOF

    chmod 600 "$VAULT_KEYS_FILE"
    log_success "Credentials saved to ${VAULT_KEYS_FILE}"

    # Export for current session
    export VAULT_TOKEN="${ROOT_TOKEN}"
    VAULT_UNSEAL_KEY_GLOBAL="${UNSEAL_KEY}"
}

# Unseal Vault if sealed
unseal_vault() {
    log_info "Checking Vault seal status..."

    if vault status 2>&1 | grep -q "Sealed.*false"; then
        log_success "Vault is already unsealed"
        return 0
    fi

    if [ -z "$VAULT_UNSEAL_KEY_GLOBAL" ]; then
        if [ -f "$VAULT_KEYS_FILE" ]; then
            source "$VAULT_KEYS_FILE"
            VAULT_UNSEAL_KEY_GLOBAL="$VAULT_UNSEAL_KEY"
        fi
    fi

    if [ -z "$VAULT_UNSEAL_KEY_GLOBAL" ]; then
        log_error "Unseal key not found. Cannot unseal Vault automatically."
        echo "Please unseal manually: vault operator unseal"
        exit 1
    fi

    vault operator unseal "$VAULT_UNSEAL_KEY_GLOBAL" > /dev/null
    log_success "Vault unsealed successfully"
}

# Authenticate with root token
authenticate_vault() {
    log_info "Authenticating with Vault..."

    if [ -z "$VAULT_TOKEN" ]; then
        if [ -f "$VAULT_KEYS_FILE" ]; then
            source "$VAULT_KEYS_FILE"
        fi
    fi

    if [ -z "$VAULT_TOKEN" ]; then
        log_error "Vault token not found in ${VAULT_KEYS_FILE}"
        exit 1
    fi

    if ! vault token lookup &> /dev/null; then
        log_error "Failed to authenticate with Vault"
        exit 1
    fi

    log_success "Authenticated successfully"
}

# Enable KV v2 secrets engine
enable_secrets_engine() {
    log_info "Checking secrets engine..."

    if vault secrets list | grep -q "^secret/"; then
        log_success "KV v2 secrets engine already enabled at secret/"
        return 0
    fi

    vault secrets enable -path=secret -version=2 kv
    log_success "KV v2 secrets engine enabled at secret/"
}

# Create MIRA policy for AppRole
create_mira_policy() {
    log_info "Creating MIRA AppRole policy..."

    vault policy write mira - <<'EOF'
# Policy for MIRA application to access secrets

# Allow read access to all MIRA secrets
path "secret/data/mira/*" {
  capabilities = ["read", "list"]
}

path "secret/metadata/mira/*" {
  capabilities = ["read", "list"]
}

# Allow renewing tokens
path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF

    log_success "MIRA policy created"
}

# Enable and configure AppRole authentication
setup_approle() {
    log_info "Setting up AppRole authentication..."

    # Enable AppRole if not already enabled
    if ! vault auth list | grep -q "^approle/"; then
        vault auth enable approle
        log_success "AppRole authentication enabled"
    else
        log_success "AppRole already enabled"
    fi

    # Create MIRA role (matching current configuration)
    vault write auth/approle/role/mira \
        token_policies="mira" \
        token_ttl=1h \
        token_max_ttl=4h \
        secret_id_ttl=0 \
        secret_id_num_uses=0 \
        > /dev/null

    log_success "MIRA AppRole configured"

    # Get role ID
    ROLE_ID=$(vault read -field=role_id auth/approle/role/mira/role-id)

    # Generate secret ID
    SECRET_ID=$(vault write -field=secret_id -f auth/approle/role/mira/secret-id)

    # Update .vault_keys file with AppRole credentials
    if [ -f "$VAULT_KEYS_FILE" ]; then
        # Remove old AppRole lines if they exist
        sed -i.bak '/VAULT_ROLE_ID/d; /VAULT_SECRET_ID/d' "$VAULT_KEYS_FILE"
        rm -f "${VAULT_KEYS_FILE}.bak"
    fi

    cat >> "$VAULT_KEYS_FILE" <<EOF

# AppRole credentials for MIRA
export VAULT_ROLE_ID='${ROLE_ID}'
export VAULT_SECRET_ID='${SECRET_ID}'
EOF

    log_success "AppRole credentials saved to ${VAULT_KEYS_FILE}"
}

# Prompt for API keys
prompt_for_secrets() {
    echo ""
    echo "=================================================="
    echo "  MIRA API Configuration"
    echo "=================================================="
    echo ""
    log_info "Please provide your API keys for MIRA."
    echo ""

    # Anthropic API Key (required)
    while [ -z "$ANTHROPIC_KEY" ]; do
        read -p "Anthropic API Key (required): " ANTHROPIC_KEY
        if [ -z "$ANTHROPIC_KEY" ]; then
            log_warning "Anthropic API key is required for MIRA to function"
        fi
    done

    # OpenRouter API Key (required)
    while [ -z "$OPENROUTER_KEY" ]; do
        read -p "OpenRouter API Key (required): " OPENROUTER_KEY
        if [ -z "$OPENROUTER_KEY" ]; then
            log_warning "OpenRouter API key is required"
        fi
    done

    # Kagi API Key (optional)
    read -p "Kagi API Key (optional, press Enter to skip): " KAGI_KEY

    # Letta API Key (optional)
    read -p "Letta API Key (optional, press Enter to skip): " LETTA_KEY

    echo ""
    log_info "Using default configuration for database and Valkey..."
}

# Populate Vault with secrets
populate_secrets() {
    log_info "Populating Vault with secrets..."

    # API Keys
    log_info "Writing API keys..."
    vault kv put secret/mira/api_keys \
        anthropic_key="${ANTHROPIC_KEY}" \
        provider_key="${PROVIDER_KEY:-${OPENROUTER_KEY:-}}" \
        kagi_api_key="${KAGI_KEY}" \
        letta_key="${LETTA_KEY:-}" \
        > /dev/null

    # Database (use defaults)
    log_info "Writing database credentials..."
    vault kv put secret/mira/database \
        service_url="postgresql://mira_admin:mira_password@localhost:5432/mira_service" \
        username="mira_admin" \
        password="mira_password" \
        > /dev/null

    # Services (use defaults)
    log_info "Writing service configuration..."
    vault kv put secret/mira/services \
        valkey_url="redis://localhost:6379/0" \
        valkey_password="" \
        > /dev/null

    # Auth - generate encryption key
    log_info "Generating encryption keys..."
    CREDENTIAL_ENCRYPTION_KEY=$(openssl rand -base64 32)
    vault kv put secret/mira/auth \
        credential_encryption_key="${CREDENTIAL_ENCRYPTION_KEY}" \
        > /dev/null

    log_success "All secrets populated successfully"
}

# Test Vault access with AppRole
test_vault_access() {
    log_info "Testing Vault access with AppRole..."

    source "$VAULT_KEYS_FILE"

    # Authenticate with AppRole
    APPROLE_TOKEN=$(vault write -field=token auth/approle/login \
        role_id="$VAULT_ROLE_ID" \
        secret_id="$VAULT_SECRET_ID")

    # Test reading a secret
    VAULT_TOKEN="$APPROLE_TOKEN" vault kv get -field=anthropic_key secret/mira/api_keys > /dev/null

    log_success "AppRole authentication and secret access verified"
}

# Print next steps
print_next_steps() {
    echo ""
    echo "=================================================="
    echo "  Vault Setup Complete!"
    echo "=================================================="
    echo ""
    log_success "Vault is configured and ready for MIRA"
    echo ""
    echo "Important files:"
    echo "  ${GREEN}✓${NC} Credentials: ${VAULT_KEYS_FILE}"
    echo "  ${GREEN}✓${NC} Config:      ${VAULT_CONFIG_FILE}"
    echo "  ${GREEN}✓${NC} Data:        ${VAULT_DATA_DIR}"
    echo ""
    log_warning "CRITICAL SECURITY NOTES:"
    echo "  1. ${VAULT_KEYS_FILE} contains sensitive credentials"
    echo "  2. Never commit .vault_keys to version control"
    echo "  3. Back up this file securely"
    echo "  4. Vault data persists in ${VAULT_DATA_DIR}"
    echo "  5. For production, use multi-key shares and HA setup"
    echo ""
    echo "Default credentials configured:"
    echo "  Database: postgresql://mira_admin:mira_password@localhost:5432/mira_service"
    echo "  Valkey:   redis://localhost:6379/0"
    echo ""
    log_info "To change these defaults, update secrets in Vault:"
    echo "  vault kv put secret/mira/database username=... password=..."
    echo ""
    echo "Next steps:"
    echo "  1. Initialize the database: bash deploy/deploy_database.sh"
    echo "  2. Start MIRA: python main.py"
    echo ""
    echo "Vault management:"
    echo "  • Data location: ${VAULT_DATA_DIR} (persistent)"
    echo "  • If Vault stops, restart: vault server -config=${VAULT_CONFIG_FILE} &"
    echo "  • View secrets: vault kv get secret/mira/api_keys"
    echo ""
}

# Main execution
main() {
    echo "=================================================="
    echo "  MIRA Vault Setup Script"
    echo "=================================================="
    echo ""

    check_vault_installed
    check_vault_running
    initialize_vault
    unseal_vault
    authenticate_vault
    enable_secrets_engine
    create_mira_policy
    setup_approle
    prompt_for_secrets
    populate_secrets
    test_vault_access
    print_next_steps
}

# Run main function
main "$@"
