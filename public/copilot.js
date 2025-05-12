class Copilot {
    constructor() {
        this.output = document.getElementById("output");
        this.input = document.getElementById("input");
        this.status = document.getElementById("status");

        this.input.addEventListener('keydown', (event) => { this.onkeydown(event); });
        this.input.addEventListener('focus', (event) => { this.onfocus(event); });
        this.connect();

        window.setInterval(() => { this.tick() }, 1000);
    }

    postMessage(command, detail) {
        if (window.parent) {
            window.parent.postMessage({ 'command': command, 'detail': detail }, '*');
        }
    }

    tick() {
        this.postMessage('tick', {});
    }

    connect() {
        if (this.ws) {
            this.disconnect();
        }

        // store references to listeners
        this.onOpenListener = (event) => { this.onopen(event); };
        this.onMessageListener = (event) => { this.onmessage(event); };
        this.onCloseListener = (event) => { this.onclose(event); };
        this.onErrorListener = (event) => { this.onerror(event); };

        // create websocket
        this.ws = new WebSocket('ws://' + window.location.hostname + ':9001');

        // attach listeners
        this.ws.addEventListener('open', this.onOpenListener);
        this.ws.addEventListener('message', this.onMessageListener);
        this.ws.addEventListener('close', this.onCloseListener);
        this.ws.addEventListener('error', this.onErrorListener);
    }

    disconnect() {
        if (this.ws) {
            this.ws.removeEventListener('open', this.onOpenListener);
            this.ws.removeEventListener('message', this.onMessageListener);
            this.ws.removeEventListener('close', this.onCloseListener);
            this.ws.removeEventListener('error', this.onErrorListener);

            try {
                this.ws.close();
            }
            catch (e) { }
        }

        this.ws = null;
    }

    reconnect() {
        this.disconnect();
        this.reconnectTimeout = Math.min((this.reconnectTimeout || 0) + 1000, 60000);
        console.log(`Attempting to reconnect in ${this.reconnectTimeout}ms`);
        window.setTimeout(() => { this.disconnect(); this.connect(); }, this.reconnectTimeout);
    }

    setStatus(connected) {
        this.status.innerHTML = connected ? "&#128994;" : "&#128992;";
        this.input.disabled = !connected;
    }

    onopen(event) {
        console.log('WebSocket connection established');
        this.setStatus(true);
    }

    onmessage(event) {
        console.log('Received message from server:', event.data);
        this.addOutput(event.data);
    }

    onclose(event) {
        console.log('WebSocket connection closed');
        this.setStatus(false);
        this.reconnect();
    }

    onerror(event) {
        console.error('WebSocket error:', event);
        this.setStatus(false);
        this.reconnect();
    }

    onkeydown(event) {
        if (event.key === 'Enter') {
            this.ws.send(this.input.value);
            this.addOutput(this.input.value);
            window.setTimeout(function () { this.input.value = '' }, 10);
        }
    }

    onfocus(event) {
        console.log("Asking parent to send the active file..");
        this.postMessage('send', {});
    }

    addOutput(msg) {
        var div = document.createElement('div');
        div.id = "o" + (new Date()).getTime();
        div.innerHTML = marked.parse(msg);
        this.output.appendChild(div);
        hljs.configure({ cssSelector: 'div#' + div.id + ' pre code' });
        hljs.highlightAll();
        window.scrollTo(0, document.body.scrollHeight);

    }
}

window.addEventListener('load', () => {
    top.copilot = window.copilot = new Copilot();
});
