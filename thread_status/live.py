"""Bounded, serialized Telegram delivery. No model, messages, or topic renames."""
import asyncio
import json
import logging
from dataclasses import dataclass
from threading import Thread, Lock
from time import sleep
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import State, Topic
from .telegram import render

LOG = logging.getLogger(__name__)
# Standard set returned by getForumTopicIconStickers.
ICONS = {State.WORKING: '5312016608254762256', State.ANSWER: '5377316857231450742',
         State.APPROVAL: '5372819184658949787', State.DONE: '5237699328843200968',
         State.FAILED: '5377498341074542641', State.STOPPED: '5408906741125490282',
         State.UNKNOWN: '5357121491508928442'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Retry(Exception):
    pass


class Bot:
    def __init__(self, token):
        self._url = 'https://api.telegram.org/bot' + token + '/editForumTopic'

    async def edit_forum_topic(self, **payload):
        # Only called by the dedicated delivery thread, never the gateway loop.
        req = Request(self._url, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
        try:
            with build_opener(NoRedirect()).open(req, timeout=8) as response:
                result = json.load(response)
        except HTTPError as err:
            with err:
                if err.code >= 500:
                    raise Retry(2) from None
                result = json.load(err)
        if result.get('error_code') == 429:
            raise Retry(max(1, int(result.get('parameters', {}).get('retry_after', 2))))
        if 'TOPIC_NOT_MODIFIED' in result.get('description', '').upper():
            return True
        return result.get('ok') is True and result.get('result') is True


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
                ok = asyncio.run(render(self.bot, topic, state, allowed=self.allowed, icons=ICONS))
                LOG.log(logging.INFO if ok else logging.WARNING,
                        'thread-status %s topic=%s state=%s', 'accepted' if ok else 'rejected', topic.thread, state.value)
            except (Retry, OSError) as exc:
                delay = int(exc.args[0]) if isinstance(exc, Retry) else 2 ** (attempt + 1)
                LOG.warning('thread-status retry topic=%s delay=%s', topic.thread, delay)
                sleep(delay)
                if attempt < 2:
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
