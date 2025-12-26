# deploy/vault.sh
# HashiCorp Vault binary installation, service configuration, and initialization
# Source this file - do not execute directly
#
# Requires: lib/output.sh, lib/services.sh, lib/vault.sh sourced first
# Requires: OS, DISTRO, MIRA_USER, MIRA_GROUP, LOUD_MODE variables set
#
# Sets: VAULT_ADDR (exported)

# Validate required variables
: "${OS:?Error: OS must be set}"
: "${MIRA_USER:?Error: MIRA_USER must be set (run python.sh first)}"
: "${MIRA_GROUP:?Error: MIRA_GROUP must be set (run python.sh first)}"

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
