"""Frontend-independent state machine. Callers serialize updates and delivery."""
from dataclasses import dataclass, field
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
        self.superseded = {}

    def fresh(self, topic):
        previous = self.active.pop(topic, None)
        if previous:
            self.superseded[topic] = previous.owner
        return State.FRESH

    def start(self, topic, owner):
        if self.superseded.get(topic) == owner:
            return None
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
