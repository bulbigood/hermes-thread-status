"""Hermes entry point. Delivery is opt-in and limited to configured topics."""
import logging
from threading import RLock

from .core import Topic, Tracker


def current_topic(platform, session):
    from gateway.session_context import get_session_env
    def get(key):
        return get_session_env('HERMES_SESSION_' + key)
    if not session or get('ID') != session or get('PLATFORM') != platform:
        return None
    chat, thread = get('CHAT_ID'), get('THREAD_ID')
    if not chat or not thread:
        return None
    return Topic(get('PROFILE') or 'default', platform, str(chat), str(thread))


def preview(topic, state):
    logging.getLogger(__name__).info('thread-status preview: %s %s/%s %s',
                                    topic.platform, topic.chat, topic.thread, state.value)


def reset_topic(platform, session):
    # /new runs outside a turn: its ContextVars may be empty or stale. Hermes
    # persists the replacement route before emitting on_session_reset.
    from hermes_constants import get_routing_process_hermes_home, profile_name_for_home
    from hermes_state_registry import acquire, release
    home = get_routing_process_hermes_home()
    db = acquire(home / 'state.db')
    try:
        entry = db.gateway_routing_entry_for_session(session)
    finally:
        release(db)
    if not entry or entry.get('suspended'):
        return None
    origin = entry.get('origin') or {}
    profile = origin.get('profile') or profile_name_for_home(home)
    if entry.get('transport_profile') not in (None, profile):
        return None  # Do not use a different profile's bot for this route.
    chat, thread = origin.get('chat_id'), origin.get('thread_id')
    if not profile or origin.get('platform') != platform or not chat or not thread:
        return None
    return Topic(profile, platform, str(chat), str(thread))


def bind(ctx, route=current_topic, publish=preview, reset_route=reset_topic):
    tracker, lock = Tracker(), RLock()

    def observe(event):
        def hook(*, session_id='', platform='', turn_id='', parent_session_id='',
                 kind='', request_id='', completed=False, interrupted=False, failed=False,
                 old_session_id='', **unused):
            if parent_session_id or not session_id:
                return
            boundary = event in ('on_session_reset', 'on_session_start')
            if boundary and platform != 'telegram':
                return
            topic = (reset_route if event == 'on_session_reset' else route)(platform, session_id)
            if not topic:
                return
            with lock:
                owner = (session_id, turn_id)
                if boundary:
                    state = tracker.fresh(topic, session_id, old_session_id if event == 'on_session_reset' else None)
                elif event == 'pre_llm_call' and turn_id:
                    state = tracker.start(topic, owner)
                elif event == 'on_session_end' and turn_id:
                    state = tracker.finish(topic, owner, completed, interrupted, failed)
                elif event in ('on_human_input_request', 'on_human_input_resolved'):
                    state = tracker.wait(topic, session_id, request_id, kind,
                                         resolved=event == 'on_human_input_resolved')
                else:
                    return
                if state is not None:
                    publish(topic, state)  # Local and non-networking; do not block hook dispatch.
        return hook

    for event in ('on_session_reset', 'on_session_start', 'pre_llm_call',
                  'on_human_input_request', 'on_human_input_resolved', 'on_session_end'):
        ctx.register_hook(event, observe(event))


def register(ctx):
    from hermes_constants import get_hermes_home
    from .live import configured
    delivery = configured(get_hermes_home())
    bind(ctx, publish=delivery or preview)
