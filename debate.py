import re

''' Reviewer debate state machine (docs/PAIR_PROGRAMMING_PLAN.md §4).

Pure logic, no I/O: the coder bridge feeds in what each reviewer said and
acts on the returned action.  Two reviewers argue about the current hunk;
the "reviewer" (Bobby) opens, the "coder" (Dyson) answers, and after
max_turns coder replies without agreement the streamer rules. '''

STANCES = ("approve", "concern", "agree", "disagree")

# Stances that mean "I hold a concern / I don't accept your position".
HOLDS = {"concern", "disagree"}

# "STANCE: concern" on its own line at the end of a model reply.
STANCE_RE = re.compile(r"\s*\**\bstance\b\**\s*[:=][\s*]*(approve|concern|agree|disagree)\b\W*$",
                       re.IGNORECASE | re.MULTILINE)


def parse_stance(text):
    """Split a model reply into (spoken_text, stance).  stance is None when
    the reply carried no recognizable tag."""
    if not text:
        return "", None
    match = STANCE_RE.search(text)
    if not match:
        return text.strip(), None
    return text[:match.start()].strip(), match.group(1).lower()


def match_ruling(text, reviewer_nicks, coder_nicks):
    """Return which reviewer the streamer sided with ("reviewer" / "coder"),
    or None when the utterance is a ruling in the streamer's own words."""
    cleaned = re.sub(r"[.!?,]+$", "", text.strip().lower()).strip()
    for who, nicks in (("reviewer", reviewer_nicks), ("coder", coder_nicks)):
        nicks = f"(?:{nicks})"
        side = (rf"(?:(?:i )?(?:go|side|agree) with {nicks}"
                rf"|{nicks}(?:'s| is|s) right"
                rf"|{nicks} wins"
                rf"|listen to {nicks})")
        if re.fullmatch(rf"(?:(?:okay|ok|all right|alright|yeah|fine),?\s+)?{side}(?: on this(?: one)?)?", cleaned):
            return who
    return None


class Debate:
    def __init__(self, max_turns=2, reviewer="bobby", coder="dyson"):
        self.max_turns = max_turns
        self.reviewer = reviewer
        self.coder = coder
        # Bumped on every start/cancel so late LLM results can be dropped.
        self.generation = 0
        self.reset()

    def reset(self):
        self.state = "idle"
        self.hunk = None
        self.thread = []
        self.coder_turns = 0

    @property
    def active(self):
        return self.state not in ("idle", "done")

    def start(self, hunk):
        """A new hunk is on screen; the reviewer owes an opening take."""
        self.generation += 1
        self.reset()
        self.hunk = hunk
        self.state = "opening"
        return self.generation

    def cancel(self):
        self.generation += 1
        self.reset()

    def add(self, speaker, text, stance):
        entry = {"speaker": speaker, "text": text, "stance": stance}
        self.thread.append(entry)
        return entry

    def position(self, speaker):
        """The speaker's most recent line, for restating at deadlock."""
        for entry in reversed(self.thread):
            if entry["speaker"] == speaker:
                return entry["text"]
        return None

    def reviewer_said(self, text, stance):
        """Bobby's opening take or rebuttal.  Returns an action tuple."""
        if self.state not in ("opening", "awaiting_reviewer"):
            return ("ignore",)
        opening = self.state == "opening"

        # No parsable stance: on the opener assume approval (don't start a
        # debate on noise); mid-debate assume he's holding his position, so
        # the loop runs out and the streamer gets asked.
        if stance not in STANCES:
            stance = "approve" if opening else "disagree"
        self.add(self.reviewer, text, stance)

        if stance not in HOLDS:
            self.state = "done"
            if opening:
                return ("done", {"status": "approved", "comment": None})
            # Bobby withdrew his concern; nothing to apply.
            return ("done", {"status": "agreed", "comment": None})

        self.state = "awaiting_coder"
        return ("ask_coder", {"text": text, "turn": self.coder_turns + 1,
                              "max_turns": self.max_turns})

    def coder_said(self, text, stance):
        """Dyson's reply to Bobby.  Returns an action tuple."""
        if self.state != "awaiting_coder":
            return ("ignore",)
        if stance not in STANCES:
            stance = "disagree"
        self.coder_turns += 1
        self.add(self.coder, text, stance)

        if stance not in HOLDS:
            # Dyson concedes: Bobby's concern becomes a review comment.
            self.state = "done"
            return ("done", {"status": "agreed", "comment": self.position(self.reviewer)})

        if self.coder_turns >= self.max_turns:
            self.state = "deadlock"
            return ("deadlock", {"reviewer": self.position(self.reviewer),
                                 "coder": self.position(self.coder)})

        self.state = "awaiting_reviewer"
        return ("ask_reviewer", {"text": text})

    def rule(self, text, reviewer_nicks, coder_nicks):
        """The streamer settles an active debate.  Returns a ruling dict."""
        if not self.active:
            return None
        side = match_ruling(text, reviewer_nicks, coder_nicks)
        if side == "reviewer":
            ruling = {"winner": self.reviewer, "text": self.position(self.reviewer)}
        elif side == "coder":
            ruling = {"winner": self.coder, "text": None}
        else:
            ruling = {"winner": "streamer", "text": text}
        self.state = "done"
        return ruling
