// Review page: opened in a browser on the streamer's main PC (the stream
// captures that screen).  Connects to the coder bridge as a viewer and renders
// hunk, state, comment, scroll, reviewer-debate (thread/debate), thinking,
// activity, and review_result frames (an APPROVED / CHANGES NEEDED stamp).
// In label mode the diff area shows the image being labeled instead, with the
// label set below it marked with each pick and the streamer's call.  Blind,
// no picks show until the streamer asks for help.  Calls and picks are
// "label:flag:flag"; the flags show on the label they go with.  The label
// cards and flags are buttons too: pick a label, toggle flags, then "Label it"
// sends the call to the bridge, same as saying it.
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

        // Comments and debate turns keyed by hunk index so back/next keeps them,
        // for the review pass with this id.
        this.reviewId = null;
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
        // Label mode: the image on screen, and each image's picks and final
        // call by index, so going back shows what was said.
        this.labelItem = null;
        this.labelTask = null;
        this.labelState = new Map();
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
            case 'label_item': this.onLabelItem(msg); break;
            case 'label_pick': this.onLabelPick(msg); break;
            case 'label_call': this.onLabelCall(msg); break;
            case 'label_result': this.onLabelResult(msg); break;
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
        document.body.classList.toggle('label-mode', msg.mode === 'label');
        if (msg.mode !== 'label') {
            this.labelItem = null;
        }
        if (msg.mode !== 'review' && msg.mode !== 'label') {
            this.commentsByHunk.clear();
            this.threadByHunk.clear();
            this.debateByHunk.clear();
            this.renderSide();
            this.showWorkbench();
        }
    }

    onHunk(msg) {
        // A new review pass starts its threads over, even without a mode
        // change in between to clear them.
        if (msg.review !== undefined && msg.review !== this.reviewId) {
            this.reviewId = msg.review;
            this.commentsByHunk.clear();
            this.threadByHunk.clear();
            this.debateByHunk.clear();
        }
        this.currentHunk = msg.index;
        this.el.file.textContent = msg.file || '';
        this.el.hunk.textContent =
            (msg.index && msg.total) ? `hunk ${msg.index}/${msg.total}` : '';
        this.setCaption(this.coder, msg.summary || '');
        // The summary opens the hunk's thread too, so it's still readable
        // once the reviewer's reply replaces the caption.  Only the first
        // visit: going back to a hunk keeps the thread it already has.
        if (msg.summary && !this.threadByHunk.has(msg.index)) {
            this.pushFor(this.threadByHunk, { speaker: this.coder, text: msg.summary });
        }

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
        return `--- a/${msg.file}\n+++ b/${msg.file}\n${this.withHunkHeader(diff, msg.start)}`;
    }

    // diff2html drops every line before the first @@, so a hunk sent as bare
    // +/- lines (a coder hand-building one for a new file) would render empty.
    // Give it a header counted from its own lines.
    withHunkHeader(diff, start) {
        if (/^@@ /m.test(diff)) {
            return diff;
        }
        const lines = diff.replace(/\n$/, '').split('\n');
        const oldCount = lines.filter((l) => !l.startsWith('+')).length;
        const newCount = lines.filter((l) => !l.startsWith('-')).length;
        const from = start || 1;
        const oldRange = oldCount ? `${from},${oldCount}` : '0,0';
        return `@@ -${oldRange} +${from},${newCount} @@\n${diff}`;
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

    // --- Label mode ---

    labelStateFor(index) {
        if (!this.labelState.has(index)) {
            this.labelState.set(index, { picks: {}, decided: null, chosen: null, flags: new Set() });
        }
        return this.labelState.get(index);
    }

    // Clicking a label selects it (again clears it); a flag toggles.
    chooseLabel(name) {
        const state = this.labelStateFor(this.labelItem.index);
        state.chosen = state.chosen === name ? null : name;
        this.renderLabel();
    }

    toggleFlag(name) {
        const state = this.labelStateFor(this.labelItem.index);
        if (!state.flags.delete(name)) state.flags.add(name);
        this.renderLabel();
    }

    submitLabel() {
        const item = this.labelItem;
        const state = this.labelStateFor(item.index);
        if (!state.chosen) return;
        this.bridge.send({ type: 'label_click', index: item.index, label: state.chosen,
                           flags: [...state.flags] });
    }

    onLabelItem(msg) {
        if (msg.task !== this.labelTask) {
            this.labelTask = msg.task;
            this.labelState.clear();
        }
        this.labelItem = msg;
        this.currentHunk = null;
        this.showingWorkbench = false;
        const state = this.labelStateFor(msg.index);
        if (msg.suggestion) state.picks[this.coder] = msg.suggestion;
        if (msg.decided) state.decided = { label: msg.decided };
        this.el.file.textContent = msg.file || '';
        this.el.hunk.textContent = `image ${msg.index}/${msg.total}` + (msg.blind ? ' · blind' : '');
        if (msg.suggestion && !msg.blind) {
            this.setCaption(this.coder, `I'd say ${this.spoken(msg.suggestion)}.` +
                            (msg.note ? ` ${msg.note}` : ''));
        } else if (msg.blind) {
            this.setCaption(null, Object.keys(state.picks).length
                ? '' : 'your call — or "I can\'t tell" for help');
        }
        this.el.side.classList.add('hidden');
        this.renderLabel();
    }

    onLabelPick(msg) {
        if (!this.labelItem) return;
        this.labelStateFor(msg.index).picks[msg.speaker] = msg.label;
        if (msg.index === this.labelItem.index) {
            const text = msg.speaker === this.coder
                ? `I'd say ${this.spoken(msg.label)}.` + (msg.text ? ` ${msg.text}` : '')
                : (msg.text || this.spoken(msg.label));
            this.setCaption(msg.speaker, text);
            this.renderLabel();
        }
    }

    // The streamer's call lands on the image on screen; the next one follows
    // right behind it.
    onLabelCall(msg) {
        if (!this.labelItem) return;
        this.labelStateFor(this.labelItem.index).decided = { label: msg.label, by: msg.by };
        this.setCaption(msg.by, this.spoken(msg.label));
        this.renderLabel();
    }

    // "unclear:blurry" as said: "unclear, blurry".
    spoken(value) {
        return (value || '').split(':').join(', ');
    }

    // A "label:flag" value's label and flags.
    splitValue(value) {
        const [label, ...flags] = (value || '').split(':');
        return { label, flags };
    }

    renderLabel() {
        const item = this.labelItem;
        const state = this.labelStateFor(item.index);
        const root = document.createElement('div');
        root.className = 'labeler';

        if (item.instructions) {
            const instructions = document.createElement('div');
            instructions.className = 'label-instructions';
            instructions.textContent = item.instructions;
            root.append(instructions);
        }

        const frame = document.createElement('div');
        frame.className = 'label-image';
        if (item.image) {
            const img = document.createElement('img');
            img.src = item.image;
            img.alt = item.file || '';
            frame.append(img);
        } else {
            frame.classList.add('missing');
            frame.textContent = `can't show ${item.file || 'this image'}`;
        }
        root.append(frame);

        const options = document.createElement('div');
        options.className = 'label-options';
        const decided = state.decided ? this.splitValue(state.decided.label) : null;
        for (const option of item.labels || []) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'label-option';
            if (state.chosen === option.name) chip.classList.add('chosen');
            chip.addEventListener('click', () => this.chooseLabel(option.name));
            if (decided && decided.label === option.name) {
                chip.classList.add('decided');
                if (decided.flags.length) {
                    const flags = document.createElement('div');
                    flags.className = 'label-flags';
                    flags.textContent = decided.flags.join(' · ');
                    chip.append(flags);
                }
            }
            const name = document.createElement('div');
            name.className = 'label-name';
            name.textContent = option.name;
            chip.append(name);
            if (option.description) {
                const desc = document.createElement('div');
                desc.className = 'label-desc';
                desc.textContent = option.description;
                chip.append(desc);
            }
            const who = document.createElement('div');
            who.className = 'label-who';
            for (const [speaker, value] of Object.entries(state.picks)) {
                const pick = this.splitValue(value);
                if (pick.label !== option.name) continue;
                const tag = document.createElement('span');
                tag.className = 'label-tag ' + this.speakerClass(speaker);
                tag.textContent = [speaker.toUpperCase(), ...pick.flags].join(' · ');
                who.append(tag);
            }
            chip.append(who);
            options.append(chip);
        }
        root.append(options);

        // The flags the streamer can add after a label, as a reminder.
        if ((item.flags || []).length) {
            const flags = document.createElement('div');
            flags.className = 'label-flag-list';
            for (const flag of item.flags) {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'label-flag';
                chip.textContent = flag.name;
                if (flag.description) chip.title = flag.description;
                if (decided && decided.flags.includes(flag.name)) chip.classList.add('on');
                if (state.flags.has(flag.name)) chip.classList.add('chosen');
                chip.addEventListener('click', () => this.toggleFlag(flag.name));
                flags.append(chip);
            }
            root.append(flags);
        }

        const submit = document.createElement('button');
        submit.type = 'button';
        submit.className = 'label-submit';
        submit.disabled = !state.chosen;
        submit.textContent = state.chosen
            ? `Label it: ${[state.chosen, ...state.flags].join(', ')}`
            : 'Label it';
        submit.addEventListener('click', () => this.submitLabel());
        const actions = document.createElement('div');
        actions.className = 'label-actions';
        actions.append(submit);
        root.append(actions);

        this.el.diff.classList.remove('empty');
        this.el.diff.replaceChildren(root);
    }

    onLabelResult(msg) {
        this.showStamp(msg.labeled === msg.total ? 'approved' : 'changes',
                       `LABELED ${msg.labeled}/${msg.total}`);
    }

    // A big rubber stamp over the code with the review's verdict.
    onReviewResult(msg) {
        this.showStamp(msg.approved ? 'approved' : 'changes',
                       msg.approved ? 'APPROVED' : 'CHANGES NEEDED');
    }

    showStamp(kind, text) {
        const STAMP_MS = 3000;
        const stamp = document.createElement('div');
        stamp.className = 'stamp ' + kind;
        stamp.textContent = text;
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
