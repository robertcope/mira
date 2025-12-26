// MIRA Web Interface - HTTP client -- RBC 123

class MIRAClient {
    constructor() {
        this.messageHistory = [];
        this.apiKey = null;
        this.currentTier = 'balanced';
        this.enabledDocs = [];
        this.processing = false;
        this.pollingInterval = null;
        this.pollingIntervalMs = 60000; // Poll every 5 seconds
        this.lastMessageCount = 0;

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
        console.log('Initializing MIRA client...');

        // Configure marked.js for better rendering
        if (typeof marked !== 'undefined') {
            marked.setOptions({
                breaks: true,  // Convert \n to <br>
                gfm: true,     // GitHub Flavored Markdown
            });
        }

        try {
            console.log('Checking server health...');
            const response = await fetch('/v0/api/health');
            const data = await response.json();
            console.log('Server health check:', data);

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

            // Setup event listeners
            this.setupEventListeners();

            // Load initial preferences
            console.log('Loading preferences...');
            await this.loadPreferences();

            // Restore UI display state from server
            console.log('Loading UI display state...');
            await this.loadDisplayState();

            // Start polling for updates from other devices
            this.startPolling();

            console.log('Initialization complete!');

        } catch (error) {
            console.error('Initialization error:', error);
            this.showInitError('Failed to initialize MIRA: ' + error.message);
        }
    }

    async fetchApiKey() {
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
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();

        if (!content) return;
        if (this.processing) {
            this.showError('Please wait for current message to complete');
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
        this.processing = true;

        // Show thinking indicator
        this.showThinking();

        // Create new assistant message container
        const assistantMessageDiv = this.addMessage('assistant', '');

        try {
            // Send via HTTP POST
            const response = await fetch('/v0/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    message: content
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();

            if (!data.success) {
                throw new Error(data.error?.message || 'Chat request failed');
            }

            // Strip internal emotion tags before display
            const cleanResponse = this.stripEmotionTag(data.data.response);

            // Store raw content as data attribute for later retrieval
            assistantMessageDiv.setAttribute('data-raw-content', cleanResponse);

            // Display the complete response with markdown rendering
            assistantMessageDiv.innerHTML = marked.parse(cleanResponse);

            // Log metadata
            console.log('Response metadata:', data.data.metadata);

            // Save display state to server after successful message exchange
            await this.saveDisplayState();

        } catch (error) {
            console.error('Chat error:', error);
            this.showError(error.message);
            // Remove the empty assistant message on error
            assistantMessageDiv.remove();
        } finally {
            // Hide thinking indicator
            this.hideThinking();

            // Enable input
            this.enableInput();
            this.processing = false;
        }
    }

    handleSlashCommand(command) {
        const parts = command.slice(1).split(/\s+/);
        const cmd = parts[0].toLowerCase();
        const args = parts.slice(1);

        switch (cmd) {
            case 'help':
                this.showSystemMessage('/tier [balanced|advanced|nuanced]\n/history [limit]\n/clear\nquit, exit, bye');
                break;

            case 'tier':
                if (args.length > 0) {
                    const tier = args[0].toLowerCase();
                    if (['balanced', 'advanced', 'nuanced'].includes(tier)) {
                        this.setTier(tier);
                    } else {
                        this.showError('Invalid tier. Options: balanced, advanced, nuanced');
                    }
                } else {
                    this.showSystemMessage(`Current tier: ${this.currentTier}\n\nOptions:\n  balanced: Gemini 3 Flash • Balanced\n  advanced: Gemini 3 Pro • Advanced\n  nuanced: Opus • Nuanced`);
                }
                break;

            case 'history':
                const limit = args.length > 0 ? parseInt(args[0]) : 20;
                if (isNaN(limit) || limit < 1 || limit > 100) {
                    this.showError('Invalid limit. Use a number between 1 and 100.');
                } else {
                    this.showHistory(limit);
                }
                break;

            case 'clear':
                this.clearMessages();
                this.clearServerDisplayState();
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

    async showHistory(limit = 20) {
        try {
            const response = await fetch(`/v0/api/data?type=history&limit=${limit}`, {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${this.apiKey}`
                }
            });

            if (!response.ok) {
                this.showError('Failed to fetch history');
                return;
            }

            const data = await response.json();
            if (!data.success) {
                this.showError('Failed to fetch history: ' + (data.error?.message || 'Unknown error'));
                return;
            }

            const messages = data.data?.messages || [];
            if (messages.length === 0) {
                this.showSystemMessage('No history found.');
                return;
            }

            // Display history header
            this.showSystemMessage(`━━━ History (${messages.length} messages) ━━━`);

            // Display messages
            messages.forEach(msg => {
                const timestamp = new Date(msg.created_at).toLocaleString();
                const role = msg.role === 'user' ? 'You' : 'MIRA';
                const preview = msg.content.substring(0, 150) + (msg.content.length > 150 ? '...' : '');

                this.addHistoryMessage(role, preview, timestamp);
            });

            this.showSystemMessage(`━━━ End of History ━━━`);

        } catch (error) {
            console.error('Failed to fetch history:', error);
            this.showError('Failed to fetch history: ' + error.message);
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

    async loadDisplayState() {
        try {
            const response = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'ui_session',
                    action: 'get_display'
                })
            });

            const data = await response.json();
            // Response structure: { success: true, data: { messages: [...], message_count: N } }
            if (data.success && data.data && data.data.messages && data.data.messages.length > 0) {
                console.log(`Restoring ${data.data.messages.length} messages from server`);

                // Clear any existing messages
                this.messagesContainer.innerHTML = '';

                // Restore each message
                data.data.messages.forEach(msg => {
                    const messageDiv = document.createElement('div');
                    messageDiv.className = `message message-${msg.role}`;

                    // For assistant messages with markdown, render it and store raw content
                    if (msg.role === 'assistant' && typeof marked !== 'undefined') {
                        messageDiv.setAttribute('data-raw-content', msg.content);
                        messageDiv.innerHTML = marked.parse(msg.content);
                    } else {
                        messageDiv.textContent = msg.content;
                    }

                    this.messagesContainer.appendChild(messageDiv);
                });

                // Track message count for polling
                this.lastMessageCount = data.data.messages.length;

                this.scrollToBottom();
            } else {
                console.log('No stored display state found');
                this.lastMessageCount = 0;
            }
        } catch (error) {
            console.error('Failed to load display state:', error);
        }
    }

    async saveDisplayState() {
        try {
            // Extract messages from DOM
            const messageElements = this.messagesContainer.querySelectorAll('.message:not(.message-history)');
            const messages = [];

            messageElements.forEach(el => {
                // Skip history messages - only save current chat display
                if (el.classList.contains('message-history')) {
                    return;
                }

                // Determine role from class
                let role = 'user';
                if (el.classList.contains('message-assistant')) {
                    role = 'assistant';
                } else if (el.classList.contains('message-error')) {
                    role = 'system';
                }

                // Get content - for assistant messages, use raw markdown if available
                let content;
                if (role === 'assistant' && el.hasAttribute('data-raw-content')) {
                    content = el.getAttribute('data-raw-content');
                } else {
                    content = el.textContent;
                }

                messages.push({ role, content });
            });

            const response = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'ui_session',
                    action: 'save_display',
                    data: { messages }
                })
            });

            const result = await response.json();
            if (result.success) {
                console.log(`Saved ${messages.length} messages to server`);
                // Update last message count after successful save
                this.lastMessageCount = messages.length;
            } else {
                console.error('Failed to save display state:', result);
            }
        } catch (error) {
            console.error('Failed to save display state:', error);
        }
    }

    updateStatusBar() {
        const tierDescriptions = {
            balanced: 'Gemini 3 Flash • Balanced',
            advanced: 'Gemini 3 Pro • Advanced',
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

    addHistoryMessage(role, content, timestamp) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message message-history';

        const headerDiv = document.createElement('div');
        headerDiv.className = 'history-header';
        headerDiv.innerHTML = `<span class="history-role">${role}</span> <span class="history-timestamp">${timestamp}</span>`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'history-content';
        contentDiv.textContent = content;

        messageDiv.appendChild(headerDiv);
        messageDiv.appendChild(contentDiv);

        this.messagesContainer.appendChild(messageDiv);
        this.scrollToBottom();

        return messageDiv;
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

    async clearServerDisplayState() {
        try {
            const response = await fetch('/v0/api/actions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${this.apiKey}`
                },
                body: JSON.stringify({
                    domain: 'ui_session',
                    action: 'clear_display'
                })
            });

            const result = await response.json();
            if (result.success) {
                console.log('Server display state cleared');
            } else {
                console.error('Failed to clear server display state:', result);
            }
        } catch (error) {
            console.error('Failed to clear server display state:', error);
        }
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

    stripEmotionTag(text) {
        // Remove internal emotion tags like <mira:my_emotion>⏰</mira:my_emotion>
        return text.replace(/\n?<mira:my_emotion>.*?<\/mira:my_emotion>/g, '');
    }

    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    startPolling() {
        // Don't start multiple polling intervals
        if (this.pollingInterval) {
            return;
        }

        console.log(`Starting polling every ${this.pollingIntervalMs}ms for cross-device updates`);

        this.pollingInterval = setInterval(async () => {
            // Don't poll while processing a message to avoid conflicts
            if (this.processing) {
                return;
            }

            try {
                const response = await fetch('/v0/api/actions', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${this.apiKey}`
                    },
                    body: JSON.stringify({
                        domain: 'ui_session',
                        action: 'get_display'
                    })
                });

                const data = await response.json();

                if (data.success && data.data && data.data.messages) {
                    const serverMessageCount = data.data.messages.length;

                    // Only update if server has more messages than we currently have
                    if (serverMessageCount > this.lastMessageCount) {
                        console.log(`Detected ${serverMessageCount - this.lastMessageCount} new messages from another device`);

                        // Save scroll position
                        const wasScrolledToBottom =
                            this.messagesContainer.scrollHeight - this.messagesContainer.scrollTop <=
                            this.messagesContainer.clientHeight + 100;

                        // Clear and reload all messages
                        this.messagesContainer.innerHTML = '';

                        data.data.messages.forEach(msg => {
                            const messageDiv = document.createElement('div');
                            messageDiv.className = `message message-${msg.role}`;

                            if (msg.role === 'assistant' && typeof marked !== 'undefined') {
                                messageDiv.setAttribute('data-raw-content', msg.content);
                                messageDiv.innerHTML = marked.parse(msg.content);
                            } else {
                                messageDiv.textContent = msg.content;
                            }

                            this.messagesContainer.appendChild(messageDiv);
                        });

                        // Update tracked count
                        this.lastMessageCount = serverMessageCount;

                        // Only auto-scroll if user was already at bottom
                        if (wasScrolledToBottom) {
                            this.scrollToBottom();
                        }
                    }
                }
            } catch (error) {
                // Silently fail - don't spam console with polling errors
                // Only log if it's not a network issue
                if (error.message && !error.message.includes('Failed to fetch')) {
                    console.error('Polling error:', error);
                }
            }
        }, this.pollingIntervalMs);
    }

    stopPolling() {
        if (this.pollingInterval) {
            console.log('Stopping polling');
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    new MIRAClient();
});
