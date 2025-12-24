// MIRA Web Interface - WebSocket client

class MIRAClient {
    constructor() {
        this.ws = null;
        this.connected = false;
        this.messageHistory = [];
        this.apiKey = null;
        this.currentTier = 'balanced';
        this.enabledDocs = [];

        this.messagesContainer = document.getElementById('messagesContainer');
        this.messageInput = document.getElementById('messageInput');
        this.sendButton = document.getElementById('sendButton');
        this.thinkingIndicator = document.getElementById('thinkingIndicator');
        this.statusBar = document.getElementById('statusBar');
        this.tierIndicator = document.getElementById('tierIndicator');
        this.domaindocsSpan = document.getElementById('domaindocs');
        this.splashScreen = document.getElementById('splashScreen');
        this.mainContainer = document.getElementById('mainContainer');

        this.init();
    }

    async init() {
        // Show splash animation
        //this.showSplash();

        console.log('Initializing MIRA client...');

        // Fetch API key from server
        try {
            console.log('Checking server health...');
            const response = await fetch('/v0/api/health');
            const data = await response.json();
            console.log('Server health check:', data);

            // In single-user mode, we'll use a simple auth approach
            // For now, we'll retrieve the API key from the server via a dedicated endpoint
            console.log('Fetching API key...');
            this.apiKey = await this.fetchApiKey();

            if (!this.apiKey) {
                console.error('No API key available');
                this.showInitError('Failed to retrieve API key. Check console for details.');
                return;
            }

            console.log('API key obtained successfully');

            // Wait for animation to complete (minimum 2 seconds)
            await this.sleep(2000);

            // Hide splash and show main interface
            console.log('Hiding splash screen...');
            this.hideSplash();

            // Connect WebSocket
            console.log('Connecting WebSocket...');
            this.connect();

            // Setup event listeners
            this.setupEventListeners();

            // Load initial preferences
            console.log('Loading preferences...');
            await this.loadPreferences();

            console.log('Initialization complete!');

        } catch (error) {
            console.error('Initialization error:', error);
            this.showInitError('Failed to initialize MIRA: ' + error.message);
        }
    }

    async fetchApiKey() {
        // For single-user OSS mode, we'll add a simple endpoint to retrieve the API key
        // This is stored in app.state.api_key during startup
        try {
            const response = await fetch('/v0/api/auth/key');
            if (!response.ok) {
                console.error('API key fetch failed:', response.status, response.statusText);

                // Fallback: try to get from localStorage for development
                const stored = localStorage.getItem('mira_api_key');
                if (stored) {
                    console.log('Using API key from localStorage');
                    return stored;
                }

                // Prompt user for API key
                const key = prompt('Enter your MIRA API key (run: python talkto_mira.py --show-key):');
                if (key) {
                    localStorage.setItem('mira_api_key', key);
                    return key;
                }
                return null;
            }
            const data = await response.json();
            console.log('API key response:', data);

            // Handle response envelope: {success: true, data: {api_key: "..."}}
            if (data.success && data.data && data.data.api_key) {
                return data.data.api_key;
            } else if (data.api_key) {
                // Direct format (shouldn't happen but handle it)
                return data.api_key;
            } else {
                console.error('Unexpected API key response format:', data);
                return null;
            }
        } catch (error) {
            console.error('Failed to fetch API key:', error);

            // Fallback: try localStorage or prompt
            const stored = localStorage.getItem('mira_api_key');
            if (stored) {
                console.log('Using API key from localStorage after error');
                return stored;
            }

            const key = prompt('Enter your MIRA API key (run: python talkto_mira.py --show-key):');
            if (key) {
                localStorage.setItem('mira_api_key', key);
                return key;
            }
            return null;
        }
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/v0/ws/chat`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connected = true;

            // Authenticate with Bearer token
            this.ws.send(JSON.stringify({
                type: 'auth',
                token: this.apiKey
            }));
        };

        this.ws.onmessage = (event) => {
            console.log('Raw WebSocket message:', event.data);
            const data = JSON.parse(event.data);
            console.log('Parsed data:', data);
            this.handleMessage(data);
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showError('Connection error');
        };

        this.ws.onclose = () => {
            console.log('WebSocket closed');
            this.connected = false;
            this.showError('Connection closed. Refresh to reconnect.');
        };
    }

    handleMessage(data) {
        switch (data.type) {
            case 'auth_success':
                console.log('Authenticated successfully');
                break;

            case 'auth_failure':
                this.showError('Authentication failed. Check your API key.');
                break;

            case 'text':
                // Append text chunk to current assistant message
                console.log('Text chunk received:', data.content, 'Type:', typeof data.content);
                if (typeof data.content === 'string') {
                    this.appendToLastMessage(data.content);
                } else {
                    console.error('Unexpected content type - expected string, got:', data.content);
                    this.appendToLastMessage(JSON.stringify(data.content));
                }
                break;

            case 'complete':
                // Hide thinking indicator
                this.hideThinking();

                // Enable input
                this.enableInput();

                // Log metadata
                console.log('Response complete:', data.metadata);
                break;

            case 'error':
                this.showError(data.message);
                this.hideThinking();
                this.enableInput();
                break;

            case 'pong':
                // Keepalive response
                break;

            default:
                console.warn('Unknown message type:', data.type);
        }
    }

    setupEventListeners() {
        // Send button
        this.sendButton.addEventListener('click', () => this.sendMessage());

        // Enter key (without Shift) sends message
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Keepalive ping every 30 seconds
        setInterval(() => {
            if (this.connected) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    }

    sendMessage() {
        const content = this.messageInput.value.trim();

        if (!content) return;
        if (!this.connected) {
            this.showError('Not connected to server');
            return;
        }

        // Check for slash commands
        if (content.startsWith('/')) {
            this.handleSlashCommand(content);
            this.messageInput.value = '';
            return;
        }

        // Add user message to UI
        this.addMessage('user', content);

        // Clear input
        this.messageInput.value = '';

        // Disable input while processing
        this.disableInput();

        // Show thinking indicator
        this.showThinking();

        // Create new assistant message container
        this.addMessage('assistant', '');

        // Send via WebSocket
        this.ws.send(JSON.stringify({
            type: 'message',
            content: content
        }));
    }

    handleSlashCommand(command) {
        const parts = command.slice(1).split(/\s+/);
        const cmd = parts[0].toLowerCase();
        const args = parts.slice(1);

        switch (cmd) {
            case 'help':
                this.showSystemMessage('/tier [fast|balanced|nuanced]\n/clear\nquit, exit, bye');
                break;

            case 'tier':
                if (args.length > 0) {
                    const tier = args[0].toLowerCase();
                    if (['fast', 'balanced', 'nuanced'].includes(tier)) {
                        this.setTier(tier);
                    } else {
                        this.showError('Invalid tier. Options: fast, balanced, nuanced');
                    }
                } else {
                    this.showSystemMessage(`Current tier: ${this.currentTier}\n\nOptions:\n  fast: Qwen3 • Fast\n  balanced: Kimi K2 • Balanced\n  nuanced: Opus • Nuanced`);
                }
                break;

            case 'clear':
                this.clearMessages();
                break;

            case 'quit':
            case 'exit':
            case 'bye':
                this.showSystemMessage('Goodbye! Close this tab to exit.');
                break;

            default:
                this.showError(`Unknown command: /${cmd}`);
        }
    }

    async setTier(tier) {
        try {
            const response = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'continuum',
                    action: 'set_llm_tier',
                    data: { tier }
                })
            });

            const data = await response.json();
            if (data.success) {
                this.currentTier = tier;
                this.updateStatusBar();
                this.showSystemMessage(`Tier set to: ${tier}`);
            } else {
                this.showError('Failed to set tier');
            }
        } catch (error) {
            console.error('Set tier error:', error);
            this.showError('Failed to set tier');
        }
    }

    async loadPreferences() {
        try {
            // Get current tier
            const tierResp = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'continuum',
                    action: 'get_llm_tier'
                })
            });

            const tierData = await tierResp.json();
            if (tierData.success) {
                this.currentTier = tierData.tier || 'balanced';
            }

            // Get enabled domaindocs
            const docsResp = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'domain_knowledge',
                    action: 'list'
                })
            });

            const docsData = await docsResp.json();
            if (docsData.success) {
                const docs = docsData.data?.domaindocs || [];
                this.enabledDocs = docs.filter(d => d.enabled).map(d => d.label);
            }

            this.updateStatusBar();

        } catch (error) {
            console.error('Failed to load preferences:', error);
        }
    }

    updateStatusBar() {
        const tierDescriptions = {
            fast: 'Qwen3 • Fast',
            balanced: 'Kimi K2 • Balanced',
            nuanced: 'Opus • Nuanced'
        };

        this.tierIndicator.textContent = tierDescriptions[this.currentTier] || this.currentTier;

        if (this.enabledDocs.length > 0) {
            this.domaindocsSpan.textContent = ' | ' + this.enabledDocs.join(' | ');
        } else {
            this.domaindocsSpan.textContent = '';
        }
    }

    addMessage(role, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message message-${role}`;
        messageDiv.textContent = content;

        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();

        return messageDiv;
    }

    appendToLastMessage(content) {
        const messages = this.messagesContainer.querySelectorAll('.message-assistant');
        if (messages.length > 0) {
            const lastMessage = messages[messages.length - 1];
            lastMessage.textContent += content;
            this.scrollToBottom();
        }
    }

    showSystemMessage(content) {
        this.addMessage('assistant', content);
    }

    showError(message) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message message-error';
        messageDiv.textContent = `Error: ${message}`;

        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();
    }

    showInitError(message) {
        // Show error on splash screen during initialization
        const animation = document.getElementById('asciiAnimation');
        animation.style.color = '#f44336'; // Red
        animation.textContent = `ERROR: ${message}\n\nCheck browser console (F12) for details.`;
        animation.style.whiteSpace = 'pre-wrap';
        animation.style.maxWidth = '80%';
        animation.style.textAlign = 'center';
    }

    clearMessages() {
        this.messagesContainer.innerHTML = '';
    }

    showThinking() {
        this.thinkingIndicator.style.display = 'block';
    }

    hideThinking() {
        this.thinkingIndicator.style.display = 'none';
    }

    disableInput() {
        this.messageInput.disabled = true;
        this.sendButton.disabled = true;
    }

    enableInput() {
        this.messageInput.disabled = false;
        this.sendButton.disabled = false;
        this.messageInput.focus();
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    showSplash() {
        const animation = document.getElementById('asciiAnimation');
        const chars = ['.', '+', '*', 'o', "'", '-', '~', '|'];
        const width = 60;

        let frame = [];
        for (let i = 0; i < width; i++) {
            if (i === 0 || i === width - 1 || Math.random() < 0.2) {
                frame.push(chars[Math.floor(Math.random() * chars.length)]);
            } else {
                frame.push(' ');
            }
        }

        const animateFrame = () => {
            // Randomly mutate some characters
            for (let i = 0; i < width; i++) {
                if (Math.random() < 0.15) {
                    if (Math.random() < 0.3) {
                        frame[i] = chars[Math.floor(Math.random() * chars.length)];
                    } else {
                        frame[i] = ' ';
                    }
                }
            }

            animation.textContent = frame.join('');
        };

        // Animate at 20 FPS
        const interval = setInterval(animateFrame, 50);

        // Store interval ID to clear later
        this.splashInterval = interval;
    }

    hideSplash() {
        clearInterval(this.splashInterval);
        this.splashScreen.style.opacity = '0';
        this.splashScreen.style.transition = 'opacity 0.3s';

        setTimeout(() => {
            this.splashScreen.style.display = 'none';
            this.mainContainer.style.display = 'flex';
            this.messageInput.focus();
        }, 300);
    }

    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    new MIRAClient();
});
