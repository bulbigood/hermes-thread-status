"""Icon-only renderer. The host supplies a profile-scoped bot and an allowed topic."""


async def render(bot, topic, state, *, allowed, icons):
    if topic.platform != 'telegram' or topic not in allowed:
        return False
    try:
        chat, thread = int(topic.chat), int(topic.thread)
    except (TypeError, ValueError):
        return False
    if not chat or thread <= 0 or (chat < 0 and thread == 1):
        return False
    icon = icons.get(state)
    if not isinstance(icon, str) or not icon.isascii() or not icon.isdecimal():
        return False
    return await bot.edit_forum_topic(chat_id=chat, message_thread_id=thread,
                                      icon_custom_emoji_id=icon)
