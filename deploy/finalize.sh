# deploy/finalize.sh
# MIRA CLI setup, systemd service, cleanup, and success message
# Source this file - do not execute directly
#
# Requires: lib/output.sh and lib/services.sh sourced first
# Requires: OS, DISTRO, MIRA_USER, MIRA_GROUP, CONFIG_*, STATUS_*, LOUD_MODE variables set

# Validate required variables
: "${OS:?Error: OS must be set}"
: "${MIRA_USER:?Error: MIRA_USER must be set}"

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
        echo -e "${DIM}    vault kv put secret/mira/api_keys \\${RESET}"
        echo -e "${DIM}      anthropic_key=\"sk-ant-your-key\" \\${RESET}"
        echo -e "${DIM}      anthropic_batch_key=\"sk-ant-your-key\" \\${RESET}"
        echo -e "${DIM}      provider_key=\"your-provider-api-key\" \\${RESET}"
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
