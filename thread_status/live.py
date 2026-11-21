"""Bounded, serialized Telegram delivery. No model, messages, or topic renames."""
import asyncio
import json
import logging
from dataclasses import dataclass
from http.client import IncompleteRead
from threading import Thread, Lock
from time import sleep
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import State, Topic
from .telegram import render

LOG = logging.getLogger(__name__)
# Standard set returned by getForumTopicIconStickers.
ICONS = {State.FRESH: '5312536423851630001', State.WORKING: '5312016608254762256', State.ANSWER: '5377316857231450742',
         State.APPROVAL: '5372819184658949787', State.DONE: '5237699328843200968',
         State.FAILED: '5377498341074542641', State.STOPPED: '5408906741125490282',
         State.UNKNOWN: '5357121491508928442'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Retry(Exception):
    def __init__(self, delay=None, *, reason='transient'):
        super().__init__(delay)
        self.delay, self.reason = delay, reason


class Rejected(Exception):
    """Only numeric status codes are safe to log (URLs can contain the bot token)."""


UNCHANGED = 'not_modified'
MAX_ATTEMPTS = 3


class Bot:
    def __init__(self, token):
        self._url = 'https://api.telegram.org/bot' + token + '/editForumTopic'

    async def edit_forum_topic(self, **payload):
        # Only called by the dedicated delivery thread, never the gateway loop.
        req = Request(self._url, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        status = 200
        try:
            with build_opener(NoRedirect()).open(req, timeout=8) as response:
                result = json.load(response)
        except HTTPError as err:
            with err:
                status = err.code
                if status == 408 or 500 <= status < 600:
                    raise Retry(reason=f'http_{status}') from None
                try:
                    result = json.load(err)
                except ValueError:
                    result = {}
        except ValueError:
            raise Retry(reason='invalid_response') from None
        if not isinstance(result, dict):
            result = {}
        code = result.get('error_code', status)
        if code == 429 or status == 429:
            params = result.get('parameters')
            delay = params.get('retry_after') if isinstance(params, dict) else None
            raise Retry(delay if type(delay) is int and delay > 0 else 2, reason='rate_limit')
        if code == 408 or isinstance(code, int) and 500 <= code < 600:
            raise Retry(reason=f'http_{code}')
        if code == 400 and 'TOPIC_NOT_MODIFIED' in str(result.get('description', '')).upper():
            return UNCHANGED
        if status == 200 and result.get('ok') is True and result.get('result') is True:
            return True
        if code == 200:
            raise Retry(reason='invalid_response')
        raise Rejected(code if isinstance(code, int) else status)


@dataclass(frozen=True)
class TopicScope:
    profile: str
    chat: str
    threads: frozenset | None  # None means explicitly opted in to all topics.

    @classmethod
    def from_config(cls, live):
        value = live['threads']
        if value != '*' and not isinstance(value, list):
            raise ValueError('threads must be a list of topic IDs or "*"')
        return cls(live['profile'], str(live['chat_id']),
                   None if value == '*' else frozenset(map(str, value)))

    def __contains__(self, topic):
        return (isinstance(topic, Topic) and topic.platform == 'telegram'
                and topic.profile == self.profile and topic.chat == self.chat
                and topic.thread.isascii() and topic.thread.isdecimal() and int(topic.thread) > 0
                and not (self.chat.startswith('-') and int(topic.thread) == 1)
                and (self.threads is None or topic.thread in self.threads))


class Delivery:
    def __init__(self, allowed, bot):
        self.allowed, self.bot = allowed, bot
        self.lock, self.pending, self.running = Lock(), {}, False

    def __call__(self, topic, state):
        if topic not in self.allowed:
            return
        with self.lock:
            self.pending[topic] = (state, 0)
            if not self.running:
                self.running = True
                Thread(target=self._drain, daemon=True, name='topic-icon-delivery').start()

    def _drain(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.running = False
                    return
                topic = next(iter(self.pending))
                state, attempt = self.pending.pop(topic)
            try:
                result = asyncio.run(render(self.bot, topic, state, allowed=self.allowed, icons=ICONS))
                outcome = UNCHANGED if result == UNCHANGED else ('api_ok' if result is True else 'skipped')
                LOG.info('thread-status %s topic=%s state=%s attempt=%s', outcome, topic.thread, state.value, attempt + 1)
            except Rejected as exc:
                LOG.warning('thread-status rejected topic=%s state=%s code=%s', topic.thread, state.value, exc.args[0])
            except (Retry, OSError, IncompleteRead) as exc:
                reason = exc.reason if isinstance(exc, Retry) else type(exc).__name__
                delay = exc.delay if isinstance(exc, Retry) and exc.delay is not None else 2 ** attempt
                if attempt + 1 >= MAX_ATTEMPTS:
                    LOG.warning('thread-status exhausted topic=%s state=%s attempts=%s reason=%s',
                                topic.thread, state.value, MAX_ATTEMPTS, reason)
                    if reason == 'rate_limit':  # Also protect the next queued edit.
                        sleep(delay)
                    continue
                LOG.warning('thread-status retry topic=%s state=%s attempt=%s delay=%s reason=%s',
                            topic.thread, state.value, attempt + 2, delay, reason)
                sleep(delay)
                with self.lock:
                    self.pending.setdefault(topic, (state, attempt + 1))
            except Exception as exc:
                LOG.warning('thread-status delivery failed: %s', type(exc).__name__)  # never log token-bearing URLs


def configured(home):
    from hermes_cli.config import load_config
    entry = load_config().get('plugins', {}).get('entries', {}).get('thread-status', {})
    live = entry.get('telegram_live')
    if not live:
        return None
    from dotenv import dotenv_values
    token = dotenv_values(home / '.env', interpolate=False).get('TELEGRAM_BOT_TOKEN', '')
    if not token or token.partition(':')[0] != str(live['bot_id']):
        raise ValueError('bot identity mismatch')
    return Delivery(TopicScope.from_config(live), Bot(token))
