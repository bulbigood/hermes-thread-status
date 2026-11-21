import unittest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

from thread_status import bind
from thread_status.core import State, Topic
from thread_status.telegram import render


TOPIC = Topic('default', 'telegram', '123', '12')


class Events(unittest.TestCase):
    def setUp(self):
        self.hooks, self.seen = {}, []
        self.reset_route = Mock(return_value=TOPIC)
        bind(SimpleNamespace(register_hook=self.hooks.__setitem__), lambda p, s: TOPIC,
             lambda t, state: self.seen.append(state), reset_route=self.reset_route)

    def fire(self, event, **kw):
        self.assertIsNone(self.hooks[event](session_id='s', platform='telegram', **kw))

    def test_waits_and_resume(self):
        self.fire('pre_llm_call', turn_id='a')
        for event, req, kind in [('request', '1', 'clarify'), ('request', '2', 'approval'),
                                 ('resolved', '2', 'approval'), ('resolved', '1', 'clarify')]:
            self.fire('on_human_input_' + event, request_id=req, kind=kind)
        self.fire('on_session_end', turn_id='a', completed=True)
        self.assertEqual(self.seen, [State.WORKING, State.ANSWER, State.APPROVAL,
                                     State.ANSWER, State.WORKING, State.DONE])

    def test_old_completion_cannot_overwrite_new_turn(self):
        self.fire('pre_llm_call', turn_id='a')
        self.fire('pre_llm_call', turn_id='b')
        self.fire('on_session_end', turn_id='a', completed=True)
        self.fire('on_session_end', turn_id='b', failed=True, completed=True)
        self.assertEqual(self.seen, [State.WORKING, State.WORKING, State.FAILED])

    def test_legacy_end_and_child_ignored(self):
        self.fire('pre_llm_call', turn_id='a')
        self.fire('pre_llm_call', parent_session_id='s', turn_id='child')
        self.fire('on_session_end', completed=True)
        self.fire('on_session_end', turn_id='a', interrupted=True)
        self.assertEqual(self.seen, [State.WORKING, State.STOPPED])

    def test_unclassified_exit_is_not_success(self):
        self.fire('pre_llm_call', turn_id='a')
        self.fire('on_session_end', turn_id='a')
        self.assertEqual(self.seen[-1], State.UNKNOWN)

    def test_reset_replaces_wait_without_another_turn(self):
        self.fire('pre_llm_call', turn_id='a')
        self.fire('on_human_input_request', request_id='1', kind='clarify')
        self.hooks['on_session_reset'](platform='telegram', session_id='new', old_session_id='s')
        self.reset_route.assert_called_once_with('telegram', 'new')
        self.fire('pre_llm_call', turn_id='a')
        self.fire('on_session_end', turn_id='a', interrupted=True)
        self.assertEqual(self.seen, [State.WORKING, State.ANSWER, State.FRESH])

    def test_first_turn_progresses_from_fresh_to_done(self):
        self.fire('on_session_start')
        self.fire('pre_llm_call', turn_id='a')
        self.fire('on_session_end', turn_id='a', completed=True)
        self.assertEqual(self.seen, [State.FRESH, State.WORKING, State.DONE])
        self.fire('on_session_start')  # Rebuilding the prompt is not a new session.
        self.assertEqual(self.seen[-1], State.DONE)

    def test_reset_before_old_first_turn_fences_even_late_session_start(self):
        self.hooks['on_session_reset'](platform='telegram', session_id='new', old_session_id='s')
        self.fire('on_session_start')
        self.fire('pre_llm_call', turn_id='late')
        self.fire('on_session_end', turn_id='late', interrupted=True)
        self.hooks['on_session_start'](platform='telegram', session_id='new')
        self.hooks['pre_llm_call'](platform='telegram', session_id='new', turn_id='next')
        self.assertEqual(self.seen, [State.FRESH, State.WORKING])

    def test_reset_without_matching_topic_is_ignored(self):
        self.reset_route.return_value = None
        self.fire('on_session_reset')
        self.hooks['on_session_reset'](platform='cli', session_id='s')
        self.reset_route.assert_called_once_with('telegram', 's')
        self.assertEqual(self.seen, [])


class Telegram(unittest.IsolatedAsyncioTestCase):
    async def test_only_icon_sent(self):
        bot = AsyncMock()
        bot.edit_forum_topic.return_value = True
        self.assertTrue(await render(bot, TOPIC, State.DONE, allowed={TOPIC}, icons={State.DONE: '12345'}))
        bot.edit_forum_topic.assert_awaited_once_with(chat_id=123, message_thread_id=12, icon_custom_emoji_id='12345')

    async def test_skips_unauthorized_general_missing_icon_and_non_telegram(self):
        bot = AsyncMock()
        general = Topic('default', 'telegram', '-100123', '1')
        other = Topic('default', 'discord', '123', '12')
        for topic, allowed, icon in [(TOPIC, set(), '123'), (general, {general}, '123'),
                                     (TOPIC, {TOPIC}, '✅'), (other, {other}, '123')]:
            self.assertFalse(await render(bot, topic, State.DONE, allowed=allowed, icons={State.DONE: icon}))
        bot.edit_forum_topic.assert_not_awaited()
