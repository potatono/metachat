// Review page: opened in a browser on the streamer's main PC (the stream
// captures that screen).  Connects to the coder bridge as a viewer and renders
// hunk, state, comment, scroll, reviewer-debate (thread/debate), thinking,
// activity, and review_result frames (an APPROVED / CHANGES NEEDED stamp).
// Outside a review, the diff area is a workbench: the coder's latest edit as
// it lands, beside a feed of its recent steps.
// Opened by the request console (?from=console), it goes back there once
// we're planning or idle again; opened directly, it stays.
// Token and connection handling live in /bridge.js.

class ReviewPage {
    constructor() {
        this.el = {};
        for (const id of ['mode', 'activity', 'chunk', 'file', 'hunk', 'status', 'diff',
                          'side', 'thread', 'debate', 'comments', 'caption', 'caption-name',
                          'thinking']) {
            this.el[id] = document.getElementById(id);
        }

        // Comments and debate turns keyed by hunk index so back/next keeps them.
        this.commentsByHunk = new Map();
        this.threadByHunk = new Map();
        this.debateByHunk = new Map();
        this.currentHunk = null;
        // What each model is working on right now, keyed by speaker.
        this.working = new Map();
        this.coder = 'dyson';
        this.reviewer = null;
        this.mode = 'idle';
        this.lastLabel = null;
        this.fromConsole = new URLSearchParams(window.location.search).get('from') === 'console';
        this.stampUntil = 0;  // hold the trip back to the console until then
        this.buildWorkbench();

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
            case 'hunk': this.onHunk(msg); break;
            case 'comment': this.onComment(msg); break;
            case 'thread': this.onThread(msg); break;
            case 'debate': this.onDebate(msg); break;
            case 'scroll': this.onScroll(msg); break;
            case 'thinking': this.onThinking(msg); break;
            case 'activity': this.onActivity(msg); break;
            case 'review_result': this.onReviewResult(msg); break;
        }
    }

    onState(msg) {
        // The console sends us here once work starts (first edit, or a
        // review); hand back when we're planning or idle again.
        if (this.fromConsole && (msg.mode === 'plan' || msg.mode === 'idle')) {
            // Let the verdict stamp land before leaving.
            const wait = Math.max(0, this.stampUntil - Date.now());
            window.setTimeout(() => window.location.replace('/'), wait);
            return;
        }
        this.el.mode.textContent = msg.mode || 'idle';
        this.el.activity.textContent = msg.activity || '';
        this.el.chunk.textContent =
            (msg.chunk && msg.chunks) ? `chunk ${msg.chunk}/${msg.chunks}` : '';
        if (msg.coder) this.coder = msg.coder;
        if (msg.reviewer !== undefined) this.reviewer = msg.reviewer;
        if (msg.label && msg.mode !== 'idle') {
            this.working.set(this.coder, msg.label);
        } else {
            this.working.delete(this.coder);
        }
        this.renderThinking();
        this.mode = msg.mode || 'idle';
        // Each new tool label is a step in the workbench feed; "Thinking"
        // between tools isn't worth a line.
        if (msg.label && msg.label !== this.lastLabel && msg.label !== 'Thinking'
                && this.mode !== 'idle') {
            this.addStep({ text: msg.label });
        }
        this.lastLabel = msg.label;
        if (msg.mode !== 'review') {
            this.commentsByHunk.clear();
            this.threadByHunk.clear();
            this.debateByHunk.clear();
            this.renderSide();
            this.showWorkbench();
        }
    }

    onHunk(msg) {
        this.currentHunk = msg.index;
        this.el.file.textContent = msg.file || '';
        this.el.hunk.textContent =
            (msg.index && msg.total) ? `hunk ${msg.index}/${msg.total}` : '';
        this.setCaption(this.coder, msg.summary || '');

        this.showingWorkbench = false;
        this.el.diff.classList.remove('empty');
        if (msg.content !== undefined) {
            // whole_file view
            this.el.diff.replaceChildren(this.plainText(msg.content));
        } else {
            this.renderDiff(this.el.diff, this.withHeader(msg));
        }
        document.getElementById('diff-container').scrollTop = 0;
        this.renderSide();
    }

    plainText(text) {
        const pre = document.createElement('pre');
        pre.className = 'whole-file';
        pre.textContent = text;
        return pre;
    }

    // Falls back to plain text when diff2html failed to load from the CDN.
    renderDiff(target, diff) {
        if (typeof Diff2Html === 'undefined') {
            target.replaceChildren(this.plainText(diff));
            return;
        }
        target.innerHTML = Diff2Html.html(diff, {
            drawFileList: false,
            outputFormat: 'line-by-line',
            matching: 'lines',
        });
    }

    // --- Workbench (outside review) ---

    buildWorkbench() {
        this.showingWorkbench = false;
        const root = document.createElement('div');
        root.className = 'workbench';
        root.innerHTML = `
            <section class="wb-edit">
                <div class="wb-heading">Latest edit</div>
                <div class="wb-diff"><div class="wb-empty">no edits yet&hellip;</div></div>
            </section>
            <section class="wb-feed">
                <div class="wb-heading">Steps</div>
                <ol class="wb-steps"></ol>
            </section>`;
        this.wb = {
            root,
            diff: root.querySelector('.wb-diff'),
            steps: root.querySelector('.wb-steps'),
        };
    }

    showWorkbench() {
        if (this.showingWorkbench) return;
        this.showingWorkbench = true;
        this.currentHunk = null;
        this.el.file.textContent = '';
        this.el.hunk.textContent = '';
        this.el.diff.classList.remove('empty');
        this.el.diff.replaceChildren(this.wb.root);
    }

    onActivity(msg) {
        if (msg.kind === 'edit') {
            this.renderDiff(this.wb.diff, msg.diff || '');
            if (msg.truncated) {
                const more = document.createElement('div');
                more.className = 'wb-empty';
                more.textContent = '(diff truncated)';
                this.wb.diff.append(more);
            }
        } else if (msg.kind === 'tests') {
            this.addStep({ text: msg.summary || '', tests: msg.ok ? 'pass' : 'fail' });
        }
    }

    // Newest first; the top step is the one happening now.
    addStep(step) {
        const li = document.createElement('li');
        const time = document.createElement('time');
        time.textContent = new Date().toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit', second: '2-digit' });
        li.append(time);
        if (step.tests) {
            li.className = 'wb-tests ' + step.tests;
            const badge = document.createElement('span');
            badge.className = 'wb-badge';
            badge.textContent = step.tests === 'pass' ? 'PASS' : 'FAIL';
            li.append(badge, ' ' + step.text);
        } else {
            li.append(step.text);
        }
        this.wb.steps.prepend(li);
        while (this.wb.steps.children.length > 40) this.wb.steps.lastChild.remove();
    }

    // diff2html needs file headers; hunks from the channel may be bare @@ blocks.
    withHeader(msg) {
        const diff = msg.diff || '';
        if (/^(diff --git|---)/m.test(diff)) {
            return diff;
        }
        return `--- a/${msg.file}\n+++ b/${msg.file}\n${diff}`;
    }

    // The footer shows whoever spoke last, in their colour.
    setCaption(speaker, text) {
        this.el['caption-name'].textContent = (speaker || '').toUpperCase();
        this.el['caption-name'].className = this.speakerClass(speaker);
        this.el.caption.textContent = text;
    }

    speakerClass(speaker) {
        if (speaker === this.coder) return 'speaker-dyson';
        if (speaker === this.reviewer) return 'speaker-bobby';
        return 'speaker-streamer';
    }

    pushFor(map, text) {
        if (this.currentHunk === null) return;
        const list = map.get(this.currentHunk) || [];
        list.push(text);
        map.set(this.currentHunk, list);
    }

    onComment(msg) {
        this.pushFor(this.commentsByHunk, msg.text);
        this.renderSide();
    }

    onThread(msg) {
        this.pushFor(this.threadByHunk, msg);
        // A new line from either reviewer clears a stale outcome.
        if (this.currentHunk !== null) this.debateByHunk.delete(this.currentHunk);
        this.setCaption(msg.speaker, msg.text);
        this.renderSide();
    }

    onDebate(msg) {
        if (this.currentHunk === null) return;
        if (msg.status === 'cancelled') {
            this.debateByHunk.delete(this.currentHunk);
        } else {
            this.debateByHunk.set(this.currentHunk, msg);
        }
        this.renderSide();
    }

    debateText(d) {
        const name = (n) => (n || '').toUpperCase();
        switch (d.status) {
            case 'approved': return `${name(this.reviewer)} approves`;
            case 'agreed': return d.comment ? `agreed: ${d.comment}` : 'agreed, nothing to change';
            case 'deadlock': return 'no agreement — your call';
            case 'ruled':
                if (d.winner === 'streamer') return `ruled: ${d.text}`;
                return d.text ? `ruled for ${name(d.winner)}: ${d.text}` : `ruled for ${name(d.winner)}`;
            default: return '';
        }
    }

    renderSide() {
        const turns = this.threadByHunk.get(this.currentHunk) || [];
        this.el.thread.replaceChildren(...turns.map((t) => {
            const div = document.createElement('div');
            div.className = 'turn ' + this.speakerClass(t.speaker);
            const who = document.createElement('div');
            who.className = 'who';
            who.textContent = t.speaker || '';
            if (t.stance) {
                const chip = document.createElement('span');
                chip.className = 'stance ' + t.stance;
                chip.textContent = t.stance;
                who.append(chip);
            }
            const text = document.createElement('div');
            text.textContent = t.text || '';
            div.append(who, text);
            return div;
        }));

        const debate = this.debateByHunk.get(this.currentHunk);
        this.el.debate.textContent = debate ? this.debateText(debate) : '';
        this.el.debate.className = 'debate-status' + (debate ? ' ' + debate.status : '');

        const comments = this.commentsByHunk.get(this.currentHunk) || [];
        this.el.comments.replaceChildren(...comments.map((text) => {
            const div = document.createElement('div');
            div.className = 'comment';
            div.textContent = text;
            return div;
        }));

        this.el.side.classList.toggle('hidden', !turns.length && !debate && !comments.length);
        this.el.side.scrollTop = this.el.side.scrollHeight;
    }

    onThinking(msg) {
        if (msg.done) {
            this.working.delete(msg.speaker);
        } else {
            this.working.set(msg.speaker, msg.text || '');
        }
        this.renderThinking();
    }

    // Reasoning summaries arrive as "**Title**" + body paragraphs: show the
    // latest title and the tail of what follows it.  Tool labels have no title.
    splitThought(text) {
        const titles = [...text.matchAll(/\*\*(.+?)\*\*/g)];
        const last = titles[titles.length - 1];
        const title = last ? last[1] : '';
        let detail = (last ? text.slice(last.index + last[0].length) : text)
            .replace(/\s+/g, ' ').trim();
        if (detail.length > 140) detail = '…' + detail.slice(-140);
        if (!title && !detail) detail = 'thinking…';
        return { title, detail };
    }

    renderThinking() {
        this.el.thinking.replaceChildren(...[...this.working].map(([speaker, text]) => {
            const row = document.createElement('div');
            row.className = 'thinking-row ' + this.speakerClass(speaker);
            const dot = document.createElement('span');
            dot.className = 'dot';
            const who = document.createElement('span');
            who.className = 'who';
            who.textContent = (speaker || '').toUpperCase();
            const { title, detail } = this.splitThought(text);
            const titleEl = document.createElement('span');
            titleEl.className = 'title';
            titleEl.textContent = title;
            const detailEl = document.createElement('span');
            detailEl.className = 'detail';
            detailEl.textContent = detail;
            row.append(dot, who, titleEl, detailEl);
            return row;
        }));
        this.el.thinking.classList.toggle('hidden', this.working.size === 0);
    }

    // A big rubber stamp over the code with the review's verdict.
    onReviewResult(msg) {
        const STAMP_MS = 3000;
        const stamp = document.createElement('div');
        stamp.className = 'stamp ' + (msg.approved ? 'approved' : 'changes');
        stamp.textContent = msg.approved ? 'APPROVED' : 'CHANGES NEEDED';
        // The diff scrolls; land the stamp in the visible part of it.
        const container = document.getElementById('diff-container');
        stamp.style.top = `${container.scrollTop + container.clientHeight * 0.4}px`;
        container.append(stamp);
        this.stampUntil = Date.now() + STAMP_MS;
        window.setTimeout(() => stamp.remove(), STAMP_MS);
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
