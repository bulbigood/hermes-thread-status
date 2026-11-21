"""Optional integration test against the local Hermes runtime, using disposable stores."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from thread_status import reset_topic
from thread_status.core import Topic


@unittest.skipUnless(importlib.util.find_spec('hermes_state_registry'), 'Hermes runtime not on path')
class ResetRoute(unittest.TestCase):
    def test_index_is_in_launch_home_not_the_served_profile(self):
        from hermes_state_registry import acquire, release
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            served = root / 'profiles' / 'example'
            served.mkdir(parents=True)
            store = acquire(root / 'state.db')
            try:
                origin = dict(platform='telegram', profile='example', chat_id='123', thread_id='12')
                def save(transport):
                    store.save_gateway_routing_entry('lane', json.dumps(dict(
                        session_id='new', origin=origin, transport_profile=transport)))
                with patch('hermes_constants.get_hermes_home', return_value=served), \
                     patch('hermes_constants.get_routing_process_hermes_home', return_value=root):
                    save('example')
                    self.assertEqual(reset_topic('telegram', 'new'), Topic('example', 'telegram', '123', '12'))
                    self.assertIsNone(reset_topic('telegram', 'old'))
                    save('another')
                    self.assertIsNone(reset_topic('telegram', 'new'))
            finally:
                release(store)
