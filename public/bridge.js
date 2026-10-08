// Shared by the review page and the request console: the bridge token and
// the authenticated WebSocket to the coder bridge.
// The token comes from a password prompt and is kept in localStorage, so it
// never has to appear on screen; a legacy ?token= is stored and then scrubbed
// from the address bar.  Both pages live on the same origin, so one paste
// covers both.  Bridge port via ?port=, default 9010.

// Named for the review page, which stored it first; kept so a saved token
// still works.
const TOKEN_KEY = 'metachat.review.token';

function loadToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
}

function saveToken(token) {
    try {
        if (token) localStorage.setItem(TOKEN_KEY, token);
        else localStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* storage blocked: the token lives for this page only */ }
}

class BridgeClient {
    // onMessage gets each parsed frame; onStatus gets true/false as the
    // connection opens and closes.
    constructor({ onMessage, onStatus = () => {} }) {
        this.onMessage = onMessage;
        this.onStatus = onStatus;

        const params = new URLSearchParams(window.location.search);
        this.port = params.get('port') || '9010';
        if (params.has('token')) {
            saveToken(params.get('token'));
            params.delete('token');
            const query = params.toString();
            history.replaceState(null, '', window.location.pathname + (query ? '?' + query : ''));
        }
        this.token = loadToken();

        this.buildTokenForm();
        if (this.token) this.connect();
        else this.promptForToken('');
    }

    // A real form with a username field so password managers can fill it.
    buildTokenForm() {
        this.tokenForm = document.createElement('form');
        this.tokenForm.id = 'token-form';
        this.tokenForm.className = 'hidden';
        this.tokenForm.autocomplete = 'on';
        this.tokenForm.innerHTML = `
            <label for="token-input">Bridge token</label>
            <input type="text" name="username" value="metachat-review"
                   autocomplete="username" hidden>
            <input type="password" id="token-input" name="password"
                   autocomplete="current-password" required>
            <button type="submit">Connect</button>
            <span id="token-error"></span>`;
        document.body.append(this.tokenForm);
        this.tokenInput = this.tokenForm.querySelector('#token-input');
        this.tokenError = this.tokenForm.querySelector('#token-error');
        this.tokenForm.addEventListener('submit', (event) => {
            event.preventDefault();
            this.token = this.tokenInput.value.trim();
            saveToken(this.token);
            this.tokenInput.value = '';
            this.tokenForm.classList.add('hidden');
            this.connect();
        });
    }

    promptForToken(error) {
        this.tokenError.textContent = error;
        this.tokenForm.classList.remove('hidden');
        this.tokenInput.focus();
    }

    connect() {
        this.ws = new WebSocket('ws://' + window.location.hostname + ':' + this.port);
        this.ws.onopen = () => {
            this.reconnectTimeout = 0;
            this.ws.send(JSON.stringify({ type: 'hello', token: this.token, role: 'viewer' }));
            this.onStatus(true);
        };
        this.ws.onmessage = (event) => {
            try {
                this.onMessage(JSON.parse(event.data));
            } catch (e) {
                console.error('bad frame', e);
            }
        };
        this.ws.onclose = (event) => {
            this.onStatus(false);
            // 1008 is the bridge rejecting the token: retrying won't help.
            if (event.code === 1008) {
                saveToken('');
                this.promptForToken('Token rejected. Paste the current one.');
            } else {
                this.reconnect();
            }
        };
        this.ws.onerror = () => { this.ws.close(); };
    }

    reconnect() {
        this.reconnectTimeout = Math.min((this.reconnectTimeout || 0) + 1000, 60000);
        window.setTimeout(() => this.connect(), this.reconnectTimeout);
    }

    // Returns false when not connected, so the caller can keep the message.
    send(msg) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
        this.ws.send(JSON.stringify(msg));
        return true;
    }
}
