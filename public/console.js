// Request console: the page at /.  Sends typed requests to the coder over the
// bridge (frame type "request") and shows the conversation as a running log
// from the bridge's chat frames: the streamer's requests and voice lines, the
// coder's spoken replies, and the second reviewer's review lines.  Between
// messages, the coder's tool steps and test results appear as compact
// activity blocks (from state labels and activity frames).  A spoken change
// request being dictated shows live above the request box.
// Token and connection handling live in /bridge.js.

// The coder speaks a sentence at a time; lines from the same speaker this
// close together are one reply.
const MERGE_MS = 8000;
// An unsent request survives the trip to the review page and back.
const DRAFT_KEY = 'metachat.console.draft';

class ConsolePage {
    constructor() {
        this.el = {};
        for (const id of ['mode', 'chunk', 'label', 'status', 'log', 'empty',
                          'composer', 'input', 'dictation', 'dictation-text']) {
            this.el[id] = document.getElementById(id);
        }
        this.coder = 'dyson';
        this.reviewer = null;
        this.last = null;  // { speaker, at, div, textEl } of the newest message
        this.stepBlock = null;  // the <ul> of steps after the newest message
        this.lastLabel = null;
        this.mode = 'idle';

        try {
            this.el.input.value = sessionStorage.getItem(DRAFT_KEY) || '';
            sessionStorage.removeItem(DRAFT_KEY);
        } catch (e) { /* storage blocked: no draft to restore */ }

        this.el.composer.addEventListener('submit', (event) => {
            event.preventDefault();
            this.sendRequest();
        });
        this.el.input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                this.sendRequest();
            }
        });

        this.bridge = new BridgeClient({
            onMessage: (msg) => this.handle(msg),
            onStatus: (connected) => this.setStatus(connected),
        });
    }

    setStatus(connected) {
        this.el.status.innerHTML = connected ? '&#128994;' : '&#128992;';
    }

    handle(msg) {
        switch (msg.type) {
            case 'state': this.onState(msg); break;
            case 'chat': this.append(msg.speaker, msg.text || '', msg.at); break;
            case 'activity': this.onActivity(msg); break;
            case 'dictation': this.onDictation(msg); break;
            case 'review_result':
                this.appendStep(msg.approved ? 'Review: approved' : 'Review: changes needed',
                                msg.approved ? 'pass' : 'fail');
                break;
        }
    }

    onState(msg) {
        this.mode = msg.mode || 'idle';
        if (msg.mode === 'review') {
            this.goToReviewPage();
            return;
        }
        this.el.mode.textContent = msg.mode || 'idle';
        this.el.chunk.textContent =
            (msg.chunk && msg.chunks) ? `chunk ${msg.chunk}/${msg.chunks}` : '';
        this.el.label.textContent = (msg.mode !== 'idle' && msg.label) || '';
        if (msg.coder) this.coder = msg.coder;
        if (msg.reviewer !== undefined) this.reviewer = msg.reviewer;
        // Each new tool label is a step; "Thinking" between tools isn't.
        if (msg.label && msg.label !== this.lastLabel && msg.label !== 'Thinking'
                && msg.mode !== 'idle') {
            this.appendStep(msg.label);
        }
        this.lastLabel = msg.label;
    }

    // Once the coder starts editing (or a review starts), the review page
    // takes over: its workbench shows each diff as it lands.  It sends us back
    // when we're planning or idle again (only when we opened it), and we only
    // leave while implementing or reviewing, so the two never bounce.
    goToReviewPage() {
        try {
            sessionStorage.setItem(DRAFT_KEY, this.el.input.value);
        } catch (e) { /* storage blocked: the draft is lost */ }
        window.location.replace('/review?from=console');
    }

    onActivity(msg) {
        if (msg.kind === 'edit' && (this.mode === 'implement' || this.mode === 'review')) {
            this.goToReviewPage();
        } else if (msg.kind === 'tests') {
            this.appendStep(msg.summary || '', msg.ok ? 'pass' : 'fail');
        }
    }

    onDictation(msg) {
        const active = msg.state === 'active';
        this.el.dictation.classList.toggle('hidden', !active);
        this.el['dictation-text'].textContent = active ? (msg.text || '\u2026') : '';
        // A sent request arrives as a chat line; only a dropped one needs noting.
        if (msg.state === 'cancelled') this.appendStep('Dictated request cancelled');
    }

    sendRequest() {
        const text = this.el.input.value.trim();
        if (!text) return;
        if (this.bridge.send({ type: 'request', text })) {
            // The bridge echoes it back as a chat frame, so it's logged then.
            this.el.input.value = '';
        } else {
            this.append('system', 'Not connected to the bridge; your request is still in the box.');
        }
        this.el.input.focus();
    }

    speakerClass(speaker) {
        if (speaker === this.coder) return 'speaker-dyson';
        if (speaker === this.reviewer) return 'speaker-bobby';
        if (speaker === 'system') return 'speaker-system';
        return 'speaker-streamer';
    }

    // Runs fn, then follows the conversation unless the streamer has
    // scrolled back to read something.
    followLog(fn) {
        const log = this.el.log;
        const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
        fn();
        if (atBottom) log.scrollTop = log.scrollHeight;
    }

    // Steps since the last message share one compact block.
    appendStep(text, tests) {
        this.followLog(() => {
            if (!this.stepBlock || this.el.log.lastElementChild !== this.stepBlock) {
                this.el.empty.remove();
                this.stepBlock = document.createElement('ul');
                this.stepBlock.className = 'steps';
                this.el.log.append(this.stepBlock);
            }
            const li = document.createElement('li');
            if (tests) {
                li.className = 'tests ' + tests;
                const badge = document.createElement('span');
                badge.className = 'badge-' + tests;
                badge.textContent = tests === 'pass' ? 'PASS' : 'FAIL';
                li.append(badge, ' ');
            }
            li.append(text);
            this.stepBlock.append(li);
        });
    }

    // at is the bridge's timestamp (seconds), so replayed history groups by
    // when lines were said, not when they arrived.
    append(speaker, text, at) {
        this.followLog(() => this.appendMessage(speaker, text, at));
    }

    appendMessage(speaker, text, at) {
        const now = at ? at * 1000 : Date.now();
        const log = this.el.log;

        // Only merge into a message that's still the newest thing in the log.
        if (this.last && this.last.speaker === speaker && speaker === this.coder
                && now - this.last.at < MERGE_MS && log.lastElementChild === this.last.div) {
            this.last.textEl.textContent += ' ' + text;
            this.last.at = now;
        } else {
            this.el.empty.remove();
            const div = document.createElement('div');
            div.className = 'msg ' + this.speakerClass(speaker);
            const who = document.createElement('div');
            who.className = 'who';
            who.textContent = speaker || '';
            const body = document.createElement('div');
            body.className = 'text';
            body.textContent = text;
            div.append(who, body);
            log.append(div);
            this.last = { speaker, at: now, div, textEl: body };
        }
    }
}

window.addEventListener('load', () => {
    window.console_page = new ConsolePage();
});
