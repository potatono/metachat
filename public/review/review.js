// Review page: OBS browser source + streamer's remote browser.
// Connects to the coder bridge as a viewer (token via ?token=, bridge port
// via ?port=, default 9010) and renders hunk/state/comment/scroll frames.

class ReviewPage {
    constructor() {
        const params = new URLSearchParams(window.location.search);
        this.token = params.get('token') || '';
        this.port = params.get('port') || '9010';

        this.el = {};
        for (const id of ['mode', 'activity', 'chunk', 'file', 'hunk', 'status', 'diff', 'comments', 'caption']) {
            this.el[id] = document.getElementById(id);
        }

        // Comments keyed by hunk index so back/next keeps pins.
        this.commentsByHunk = new Map();
        this.currentHunk = null;

        this.connect();
    }

    connect() {
        this.ws = new WebSocket('ws://' + window.location.hostname + ':' + this.port);
        this.ws.onopen = () => {
            this.reconnectTimeout = 0;
            this.ws.send(JSON.stringify({ type: 'hello', token: this.token, role: 'viewer' }));
            this.setStatus(true);
        };
        this.ws.onmessage = (event) => {
            try {
                this.handle(JSON.parse(event.data));
            } catch (e) {
                console.error('bad frame', e);
            }
        };
        this.ws.onclose = () => { this.setStatus(false); this.reconnect(); };
        this.ws.onerror = () => { this.ws.close(); };
    }

    reconnect() {
        this.reconnectTimeout = Math.min((this.reconnectTimeout || 0) + 1000, 60000);
        window.setTimeout(() => this.connect(), this.reconnectTimeout);
    }

    setStatus(connected) {
        this.el.status.innerHTML = connected ? '&#128994;' : '&#128992;';
    }

    handle(msg) {
        switch (msg.type) {
            case 'state': this.onState(msg); break;
            case 'hunk': this.onHunk(msg); break;
            case 'comment': this.onComment(msg); break;
            case 'scroll': this.onScroll(msg); break;
        }
    }

    onState(msg) {
        this.el.mode.textContent = msg.mode || 'idle';
        this.el.activity.textContent = msg.activity || '';
        this.el.chunk.textContent =
            (msg.chunk && msg.chunks) ? `chunk ${msg.chunk}/${msg.chunks}` : '';
        if (msg.mode !== 'review') {
            this.commentsByHunk.clear();
            this.renderComments();
        }
    }

    onHunk(msg) {
        this.currentHunk = msg.index;
        this.el.file.textContent = msg.file || '';
        this.el.hunk.textContent =
            (msg.index && msg.total) ? `hunk ${msg.index}/${msg.total}` : '';
        this.el.caption.textContent = msg.summary || '';

        this.el.diff.classList.remove('empty');
        if (msg.content !== undefined) {
            // whole_file view: plain content instead of a diff
            const pre = document.createElement('pre');
            pre.className = 'whole-file';
            pre.textContent = msg.content;
            this.el.diff.replaceChildren(pre);
        } else {
            this.el.diff.innerHTML = Diff2Html.html(this.withHeader(msg), {
                drawFileList: false,
                outputFormat: 'line-by-line',
                matching: 'lines',
            });
        }
        document.getElementById('diff-container').scrollTop = 0;
        this.renderComments();
    }

    // diff2html needs file headers; hunks from the channel may be bare @@ blocks.
    withHeader(msg) {
        const diff = msg.diff || '';
        if (/^(diff --git|---)/m.test(diff)) {
            return diff;
        }
        return `--- a/${msg.file}\n+++ b/${msg.file}\n${diff}`;
    }

    onComment(msg) {
        if (this.currentHunk === null) return;
        const list = this.commentsByHunk.get(this.currentHunk) || [];
        list.push(msg.text);
        this.commentsByHunk.set(this.currentHunk, list);
        this.renderComments();
    }

    renderComments() {
        const list = this.commentsByHunk.get(this.currentHunk) || [];
        this.el.comments.replaceChildren(...list.map((text) => {
            const div = document.createElement('div');
            div.className = 'comment';
            div.textContent = text;
            return div;
        }));
    }

    onScroll(msg) {
        const container = document.getElementById('diff-container');
        const step = container.clientHeight * 0.7;
        container.scrollTop += (msg.direction === 'up') ? -step : step;
    }
}

window.addEventListener('load', () => {
    window.review = new ReviewPage();
});
