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
        this.ws = new WebSocket('ws://' + window.location.hostname + ':9001');
        this.ws.addEventListener('open', (event) => { this.onopen(event); });
        this.ws.addEventListener('message', (event) => { this.onmessage(event); });
        this.ws.addEventListener('close', (event) => { this.onclose(event); });
        this.ws.addEventListener('error', (event) => { this.onerror(event); });
    }

    disconnect() {
        if (this.ws) {
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
