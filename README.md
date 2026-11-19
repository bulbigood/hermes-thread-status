# Hermes Thread Status

Shows foreground turn state in allowlisted Telegram topic icons, without changing their titles. Requires Hermes Agent 0.22.5 hooks and a bot permitted to edit the target topics. Without `plugins.entries.thread-status.telegram_live`, the plugin only logs transitions.

Set `telegram_live` to an object with `profile`, `bot_id`, `chat_id`, and `threads`. Use a list of topic IDs to restrict updates, or the quoted string `"*"` for all current and future topics in that one chat and profile. A new topic gets its status on the first tracked turn, not on creation or installation. An empty list permits no topics. The bot ID must match the existing `TELEGRAM_BOT_TOKEN` in the profile's `.env`; do not put a token in this setting. Reload the plugin after changing it. Disable with `hermes plugins disable thread-status`; disabling does not clear the last icon.

Icons: ⚡ working, ❓ waiting for an answer, 🛃 waiting for approval, ✅ foreground turn completed, ‼️ failed, 🏁 interrupted, 👀 unknown exit. Detached jobs are not aggregated. A gateway outage can leave an old icon until the next tracked turn. Wait events use their session and request IDs; the canonical end event is matched to a specific turn.

`core.py` tracks states independently of the frontend. `live.py` serializes Telegram delivery off the hook thread and coalesces pending updates. Only a topic ID, chat ID and standard icon ID are sent. Run `python3 -m unittest discover -s tests -v` for offline tests.
