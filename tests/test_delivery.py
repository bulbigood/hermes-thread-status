"""Exercise delivery with deterministic scheduling and no network access."""
import io
from email.message import Message
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from thread_status.core import State, Topic
from thread_status.live import Bot, Delivery, ICONS, Retry


class DeliveryTests(unittest.TestCase):
    topic = Topic('profile', 'telegram', '123', '12')

    def setUp(self):
        self.bot = AsyncMock()
        self.delivery = Delivery({self.topic}, self.bot)
        thread = patch('thread_status.live.Thread')
        self.thread = thread.start()
        self.addCleanup(thread.stop)

    def test_all_states_only_edit_the_icon(self):
        self.assertEqual(set(ICONS), set(State))
        self.assertEqual(len(set(ICONS.values())), len(State))
        for state in State:
            self.delivery(self.topic, state)
            self.delivery._drain()
            self.bot.edit_forum_topic.assert_awaited_with(chat_id=123, message_thread_id=12,
                                                         icon_custom_emoji_id=ICONS[state])

    def test_queue_coalesces_superseded_state(self):
        for state in (State.WORKING, State.ANSWER, State.DONE):
            self.delivery(self.topic, state)
        self.thread.assert_called_once()
        self.delivery._drain()
        self.bot.edit_forum_topic.assert_awaited_once_with(chat_id=123, message_thread_id=12,
                                                          icon_custom_emoji_id=ICONS[State.DONE])

    def test_retry_does_not_overwrite_new_state(self):
        self.bot.edit_forum_topic.side_effect = [Retry(1), True]
        self.delivery(self.topic, State.WORKING)
        with patch('thread_status.live.sleep', side_effect=lambda _: self.delivery(self.topic, State.DONE)):
            self.delivery._drain()
        self.assertEqual([c.kwargs['icon_custom_emoji_id'] for c in self.bot.edit_forum_topic.await_args_list],
                         [ICONS[State.WORKING], ICONS[State.DONE]])

    def test_out_of_scope_never_starts_delivery(self):
        for topic in (Topic('other', 'telegram', '123', '12'), Topic('profile', 'telegram', '456', '12')):
            self.delivery(topic, State.WORKING)
        self.thread.assert_not_called()
        self.bot.edit_forum_topic.assert_not_awaited()


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_honors_telegram_retry_after(self):
        error = HTTPError('https://example.invalid', 429, 'limited', Message(),
                          io.BytesIO(b'{"error_code":429,"parameters":{"retry_after":12}}'))
        with patch('thread_status.live.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(Retry) as raised:
                await Bot('test').edit_forum_topic()
        self.assertEqual(raised.exception.args, (12,))
