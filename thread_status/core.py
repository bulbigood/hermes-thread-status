"""Frontend-independent state machine. Callers serialize updates and delivery."""
from dataclasses import dataclass, field
from collections import deque
from enum import Enum


class State(str, Enum):
    FRESH = "fresh"
    WORKING = "working"
    ANSWER = "needs_answer"
    APPROVAL = "needs_approval"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Topic:
    profile: str
    platform: str
    chat: str
    thread: str


@dataclass
class Turn:
    owner: tuple
    pending: dict = field(default_factory=dict)

    @property
    def state(self):
        if State.APPROVAL in self.pending.values():
            return State.APPROVAL
        return State.ANSWER if self.pending else State.WORKING


class Tracker:
    def __init__(self):
        self.active = {}
        # Bound transient history; a late hook can arrive even when no turn
        # from the retired session had reached pre_llm_call yet.
        self.retired = deque(maxlen=1024)
        self.seen = deque(maxlen=1024)

    def fresh(self, topic, session, old_session=None):
        key = (topic, session)
        if old_session and old_session != session:
            self.retired.append((topic, old_session))
        if key in self.retired or key in self.seen:
            return None
        self.seen.append(key)
        self.active.pop(topic, None)
        return State.FRESH

    def start(self, topic, owner):
        key = (topic, owner[0])
        if key in self.retired:
            return None
        if key not in self.seen:
            self.seen.append(key)
        current = self.active.get(topic)
        if current and current.owner == owner:
            return None
        self.active[topic] = Turn(owner)
        return State.WORKING

    def wait(self, topic, session, request, kind, resolved=False):
        current = self.active.get(topic)
        if not request or not current or current.owner[0] != session:
            return None
        before = current.state
        if resolved:
            current.pending.pop(request, None)
        elif kind in ('approval', 'sudo', 'clarify'):
            current.pending[request] = State.ANSWER if kind == 'clarify' else State.APPROVAL
        return current.state if current.state != before else None

    def finish(self, topic, owner, completed=False, interrupted=False, failed=False):
        current = self.active.get(topic)
        if not current or current.owner != owner:
            return None
        del self.active[topic]
        if interrupted:
            return State.STOPPED
        if failed:
            return State.FAILED
        return State.DONE if completed else State.UNKNOWN
