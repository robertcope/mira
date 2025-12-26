# deploy/python.sh
# Python verification, MIRA download, venv setup, dependencies, embedding model, Playwright
# Source this file - do not execute directly
#
# Requires: lib/output.sh and lib/services.sh sourced first
# Requires: OS, PYTHON_VER, CONFIG_*, LOUD_MODE variables set
#
# Sets: PYTHON_CMD, MIRA_USER, MIRA_GROUP

# Validate required variables
: "${OS:?Error: OS must be set}"
: "${PYTHON_VER:?Error: PYTHON_VER must be set (run dependencies.sh first)}"

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
