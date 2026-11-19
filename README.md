# Hermes Thread Status

An operator should be able to tell which topic needs attention without opening it.
This first milestone is an **offline preview**, not an installed service. The
Hermes plugin (`thread_status/`) observes turns and logs transitions. A separate
Telegram adapter can send an icon-only edit to an explicitly allowed topic using
an injected bot. The preview does not connect that adapter, read a token, rename
topics, or change any chat. Target host: Hermes Agent 0.22.5.

## Run locally

```sh
python3 -m unittest discover -s tests -v
```

The tests replay hook payloads and use a mock bot; no network, keys or model required.
`core.py` contains the shared state machine. `__init__.py` connects four Hermes
hooks to it. `telegram.py` translates a state into an `edit_forum_topic` call.
A new frontend implements its own renderer, not another state machine.

## What the indicator means

Working → needs answer / approval → working → done, failed, stopped or unknown.
A sudo prompt is grouped with approvals. A question in ordinary assistant text
does not count as a blocked turn. A late terminal event cannot finish a newer
turn; simultaneous questions are tracked by request ID. In this version, done
means the foreground turn finished, not that detached work has completed.

## Before the first live pilot

- Wire a nonblocking, per-topic serialized delivery worker to the correct bot
  profile, with an explicit topic allowlist, backoff and coalescing of stale edits.
- Select IDs from `getForumTopicIconStickers`; preserve the original icon for rollback.
- Record last delivered state so a restart can reconcile potentially stale icons.
- Verify routing and wait notifications in a disposable Hermes profile and test
  topic. Wait events have a session and request ID but not necessarily a turn ID.
  Do not claim stronger correlation for overlapping waits than the host provides.
- Decide separately whether to aggregate background workers before displaying done.
