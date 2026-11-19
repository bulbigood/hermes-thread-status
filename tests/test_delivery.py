"""Exercise delivery with deterministic scheduling and no network access."""
import io
from email.message import Message
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from thread_status.core import State, Topic
from thread_status.live import Bot, Delivery, ICONS, Retry, TopicScope, Rejected, UNCHANGED


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

    def test_new_topic_accepted_only_in_selected_chat_profile_and_platform(self):
        scope = TopicScope.from_config(dict(profile='profile', chat_id=123, threads='*'))
        delivery = Delivery(scope, self.bot)
        for topic in (Topic('other', 'telegram', '123', '87'), Topic('profile', 'telegram', '124', '87'),
                      Topic('profile', 'discord', '123', '87'), Topic('profile', 'telegram', '123', '')):
            delivery(topic, State.WORKING)
        self.thread.assert_not_called()
        delivery(Topic('profile', 'telegram', '123', '87'), State.WORKING)
        delivery._drain()
        self.bot.edit_forum_topic.assert_awaited_once_with(chat_id=123, message_thread_id=87,
                                                          icon_custom_emoji_id=ICONS[State.WORKING])

    def test_only_explicit_wildcard_broadens_scope(self):
        for value, expected in (([12], True), ([13], False), ([], False), (['*'], False)):
            scope = TopicScope.from_config(dict(profile='profile', chat_id=123, threads=value))
            self.assertEqual(self.topic in scope, expected)
        with self.assertRaises(ValueError):
            TopicScope.from_config(dict(profile='profile', chat_id=123, threads=None))

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

    def test_ok_and_unchanged_have_distinct_logs_and_no_retry(self):
        for response, outcome in [(True, 'api_ok'), (UNCHANGED, 'not_modified')]:
            self.bot.edit_forum_topic.reset_mock()
            self.bot.edit_forum_topic.return_value = response
            self.delivery(self.topic, State.WORKING)
            with self.assertLogs('thread_status.live', level='INFO') as log:
                self.delivery._drain()
            self.assertIn(f'thread-status {outcome} topic=', log.output[0])
            self.bot.edit_forum_topic.assert_awaited_once()

    def test_permanent_error_stops_immediately(self):
        self.bot.edit_forum_topic.side_effect = Rejected(403)
        self.delivery(self.topic, State.WORKING)
        with self.assertLogs('thread_status.live') as log, patch('thread_status.live.sleep') as pause:
            self.delivery._drain()
        self.assertIn('rejected', log.output[0]); self.assertIn('code=403', log.output[0])
        self.bot.edit_forum_topic.assert_awaited_once(); pause.assert_not_called()

    def test_network_retries_are_bounded(self):
        self.bot.edit_forum_topic.side_effect = TimeoutError()
        self.delivery(self.topic, State.WORKING)
        with self.assertLogs('thread_status.live') as log, patch('thread_status.live.sleep') as pause:
            self.delivery._drain()
        self.assertEqual(self.bot.edit_forum_topic.await_count, 3)
        self.assertEqual([c.args[0] for c in pause.call_args_list], [1, 2])
        self.assertIn('exhausted', log.output[-1])


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_classification(self):
        for code, body, expected in [(400, b'{"description":"Bad Request: TOPIC_NOT_MODIFIED"}', UNCHANGED),
                                    (403, b'{"description":"Forbidden"}', Rejected),
                                    (408, b'', Retry), (503, b'upstream unavailable', Retry)]:
            with self.subTest(code=code), patch('thread_status.live.build_opener') as opener:
                opener.return_value.open.side_effect = HTTPError('https://example.invalid', code, '', Message(), io.BytesIO(body))
                if expected == UNCHANGED:
                    self.assertEqual(await Bot('test').edit_forum_topic(), UNCHANGED)
                else:
                    with self.assertRaises(expected):
                        await Bot('test').edit_forum_topic()

    async def test_rate_limit_honors_telegram_retry_after(self):
        error = HTTPError('https://example.invalid', 429, 'limited', Message(),
                          io.BytesIO(b'{"error_code":429,"parameters":{"retry_after":12}}'))
        with patch('thread_status.live.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(Retry) as raised:
                await Bot('test').edit_forum_topic()
        self.assertEqual(raised.exception.args, (12,))
