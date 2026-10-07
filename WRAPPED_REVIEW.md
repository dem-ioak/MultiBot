# Wrapped pipeline review

Scope: everything that collects, stores, or delivers Wrapped data — `bot/cogs/events.py`, `bot/cogs/wrapped.py`, `bot/util/helper_functions.py` (archiving), `bot/util/dataclasses.py`, and the `compile` command in `bot/cogs/moderation.py`. The step that turns the data into images lives outside this repo, so it is not reviewed.

Nothing in the wrapped code was changed. Findings marked **(confirmed)** were checked against the live database with read-only count/stat queries on 2026-10-06; the rest come from reading the code.

## Status (2026-10-07)

The findings below describe the code as it was reviewed. They have since been fixed in the working tree, with these exceptions:

| Finding | What was done instead |
|---|---|
| 2.4 AFK baked into the event type | The four event types are kept so existing analysis still works; a leave now always mirrors its join, so they can no longer mismatch. |
| 2.5 Everything is UTC | Voice timestamps stay UTC to match the stored data. The new daily message buckets use Eastern dates and hours. |
| 4.1 Lifetime counters | The lifetime totals in `wrapped` are still written. Daily buckets are written alongside them to a new `wrappeddaily` collection. |
| 4.4 Deleted messages never subtracted | Not changed. Only cached messages could be subtracted, which would make the counts inconsistent. |
| 5 Blocking database calls | The message and voice listeners run their database calls off the event loop. Settings are not cached and the driver is still pymongo. |
| 6.3 Server maps | `SERVER_NAMES` is now only an override; `SHARE_CHANNELS` still needs an entry per server. |

None of this is live until it is deployed and the bot restarted. The archive files on the host still need bringing back in with `python scripts/repair_vc_events.py --import-archives bot/archive --apply`.

## Summary

The event-driven idea is sound for voice data, and the join/leave events currently in the database are clean. The problems are around it:

1. **Failures are invisible.** Every listener exception is swallowed, so data loss is only discovered when the Wrapped is built.
2. **Voice events have no recovery path.** Anything that happens while the bot is down or reconnecting is never recorded, and nothing reconciles afterwards.
3. **The archive trigger does not measure what it is meant to.** It moves the events out of the database every few hundred events, into local files on one machine.
4. **Message stats are not event-driven at all.** They are lifetime counters with no dates, so they cannot be scoped to a year or broken down.
5. **Some users are silently not counted**, and the message-count update can drop increments.
6. **The delivery side has several bugs** that produce "Interaction Failed", including `/wrapped` not working in DMs.

## 1. Failures are invisible

- **`on_error` discards everything.** `MyBot.on_error` in `bot/main.py:47` is `pass`, so an exception in any listener disappears with no log line. The app-command error handler (`bot/main.py:66`) does the same for commands.
- **A cog that fails to load is skipped silently.** `load_cogs` (`bot/main.py:39`) catches the exception and continues. If `events.py` ever fails to import, the bot runs normally and collects nothing; the only trace is "loaded 9/10 cogs".
- **This is already hiding a bug.** `on_guild_channel_create` (`bot/cogs/events.py:457`) uses `is_text` two lines before assigning it, so it raises on every channel creation and has never written a record.

For a pipeline that has to run unattended for a year, this is the first thing I would fix: log the traceback in `on_error`, and have the bot report (for example a daily DM to you) how many events and message increments it wrote.

## 2. Voice events

### 2.1 No recovery after downtime or reconnects

Voice time is derived by pairing JOIN with LEAVE and START_STREAM with END_STREAM. Nothing handles the cases where one half is never seen:

- **Bot restart.** Anyone already in a voice channel at startup has no JOIN. Anyone who left while it was down has no LEAVE, so their session stays open until their next event.
- **Gateway reconnect without a restart.** When discord.py cannot resume a session it rebuilds voice state from a fresh snapshot and does not fire `on_voice_state_update` for what changed in between.
- **`on_ready` does not reconcile.** It only refreshes the boards and watchlist (`bot/cogs/events.py:36`).

The 213 join/leave events currently stored have no unmatched pairs **(confirmed)**, but that is a three-day window with no evident restart.

Suggested fix: write a heartbeat timestamp every minute. On `on_ready`, close every open session at the last heartbeat, then open a new session for everyone currently in voice.

### 2.2 Events are lost when a Discord call fails

The handler builds the event list, makes Discord API calls, and only then writes to the database (`bot/cogs/events.py:316`). Any exception before the write drops the events for that update:

- `before.channel.delete()` (line 205) raises if the channel is already gone, which happens when two people leave at the same moment.
- `create_voice_channel` / `move_to` (lines 279–282) raise on missing permissions, rate limits, or the user leaving before the move.
- `after.channel.category.id` (line 275) raises if the join-to-create channel has no category.

In each case the LEAVE for the channel the user came from is lost. Write the events first, then do the channel management.

### 2.3 Stream events do not pair up

The database currently holds 49 START_STREAM and 51 END_STREAM **(confirmed)**. The likely cause is the END condition (`bot/cogs/events.py:236`): it fires on a channel change even when the stream carries over, and the START condition then never fires for the new channel. When the user finally stops, a second END is written for one START.

### 2.4 AFK is baked into the event type

JOIN_AFK / LEAVE_AFK are chosen from the server's `afk_corner` setting at the moment of the event. If that setting changes, or is `-1` (true for two of the five servers **(confirmed)**), the same channel produces a JOIN_VC and a LEAVE_AFK that do not pair. The channel ID is already stored, so one JOIN and one LEAVE type is enough; classify AFK when analysing.

### 2.5 Smaller issues

- **Bots are recorded.** `on_voice_state_update` does not filter `member.bot`, so music bots generate events and can trigger join-to-create.
- **Channel names are not stored.** Events hold only `channel_id`, and temporary VCs are deleted. Any "favourite channel" stat cannot be named at year end; `NAME_VC` exists in the enum but is never written.
- **VC duration is always "UNKNOWN".** The log line reads `vc_data["start_time"]`, but the field is `created_at`; none of the 2,227 VC records has `start_time` **(confirmed)**, and a bare `except` hides it.
- **Timestamps can tie.** A move writes LEAVE and JOIN with the identical timestamp, and Mongo keeps only milliseconds. Sorting by timestamp alone is ambiguous; sort by `(timestamp, _id)`.
- **Timestamps are taken late.** `curr_time` is set after a blocking database read, and the handler itself may have been waiting behind other blocking calls (see 5).
- **Everything is UTC.** The rest of the bot works in Eastern time. "Most active hour" stats and the year boundary will be five hours off unless the analysis converts.

## 3. Archiving

### 3.1 The trigger does not measure fullness

`get_size_and_limit` (`bot/util/helper_functions.py:38`) treats `storageSize` as a limit. It is not: it is the disk space the collection currently occupies, and it grows with the data. `size / storageSize > 0.9` compares uncompressed size with compressed size.

In practice **(confirmed)**:

- The collection holds 311 events spanning 2026-10-04 to 2026-10-07, at 35 KB against a `storageSize` of 53 KB.
- So the archive fires at roughly 430 events, every few days.
- The whole database is about 0.35 MB of data. Events average 112 bytes, so a full year is a few megabytes.

Archiving is not needed at this volume. If you want it, trigger on a date (monthly) or on `dbStats` against the real quota.

### 3.2 The archive is the only copy and it is fragile

- **One machine, one folder.** Events are deleted from Mongo after being written to `bot/archive/` on whatever host runs the bot. That folder is gitignored and is empty on this machine, so a year of voice data lives in hundreds of small JSON files on the host's disk.
- **A stray file stops archiving.** File names are sorted with `int(x.split("_")[0])` (line 28). Any non-numeric file in the folder (for example `desktop.ini`) raises on every attempt; the swallowed error means you would not know.
- **The path depends on the working directory.** `bot/archive/` only resolves when the bot is started from the repo root; otherwise `os.listdir` raises on every attempt.
- **A crash between write and delete duplicates events** in the next archive. The `_id` is kept, so they can be de-duplicated if the analysis does so.
- **It runs on every voice event.** `collstats` is called after each insert (`bot/cogs/events.py:317`), adding a database round trip to every update.

### 3.3 Two bot instances share one database

Running the bot locally for development against the same `DATABASE_URL` while production is up double-counts every message and voice event, and lets one instance archive and delete events the other is writing.

## 4. Message stats

### 4.1 Lifetime counters, not events

`WRAPPED` holds one document per user per server with running totals. There is no timestamp and no reset anywhere in the repo.

- A "2025" or "2026" Wrapped is only correct if the collection is wiped at exactly midnight on January 1.
- Nothing time-based is possible: busiest month, busiest hour, streaks, per-channel breakdown.
- It is inconsistent with voice, which keeps the raw events.

Suggested fix: bucket by day, one document per `(guild, user, date)` with the same `$inc` fields. It stays small and can answer any date-range question.

### 4.2 Some users are never counted

- **25 of 190 users have no `WRAPPED` document (confirmed).** For them `wrapped_data` is `None`, and `WRAPPED.update_one(None, …)` (`bot/cogs/events.py:379`) raises a `TypeError` that is swallowed. Every message they send is lost.
- **Users with no `USERS` document are skipped entirely** (line 337). Documents are created only in `on_member_join` and `on_guild_join`, so anyone who joins while the bot is down is never tracked.

### 4.3 Increments can be dropped

`WRAPPED.update_one(wrapped_data, {"$inc": …})` uses the whole fetched document as the filter. If the document changes between the read (line 334) and the write (line 379), the filter matches nothing and the increment is dropped with no error. There is an `await` in between on level-up (line 348), and a second bot instance widens the window. The same pattern is used for XP (line 355).

One change fixes 4.2 and 4.3: filter on `{"_id": primary_key}` with `upsert=True`. That also removes the need to pre-create documents and the read before the write.

### 4.4 What the counters actually count

| Counter | What it counts | Problem |
|---|---|---|
| `word_count` | `len(content.split(" "))` | An image-only message counts as 1 word; double spaces count extra; newlines do not split. |
| `image_count` | 1 if the message has any attachment | Videos and files count as images; three images count once. |
| `gif_count` | 1 if `tenor.com` is in the text | Uploaded GIFs and other GIF hosts are missed. It drops to zero if Discord's picker stops producing Tenor links — worth checking it still does. |
| `user_pings.<id>` | every user in `message.mentions` | Includes reply pings, self-mentions, and bots. |
| `user_pings.count` | messages with at least one mention | Stored in the same map as the user IDs, so the analysis must special-case the key. |
| `everyone_pings` | `message.mention_everyone` | Also true for `@here`. |

Deleted messages are never subtracted.

## 5. Blocking database calls

pymongo is synchronous and is called directly inside the listeners. Each message costs three reads and one write (`bot/cogs/events.py:333–379`); each voice update costs two reads, an insert, and a `collstats`. The event loop is blocked for all of them. Under a burst this delays the gateway heartbeat and pushes voice timestamps later than the real event. Caching the server settings and using the single upsert from 4.3 removes most of it; an async Mongo driver removes the rest.

## 6. Delivery (`bot/cogs/wrapped.py`)

### 6.1 Bugs that users hit

- **`/wrapped` fails in DMs.** The first line reads `interaction.guild.name` (line 243), and `guild` is `None` in a DM. The announcement tells users to run it "either here or in any one of these servers" (line 303).
- **Sharing fails for users without the `sent_wrapped` field.** `user_data["sent_wrapped"]` (line 117) is a plain lookup, the field is not in the `User` dataclass, and 70 of 190 users do not have it **(confirmed)**.
- **Sharing is one-time forever.** The flag is not tied to a year; the 23 users who shared in 2025 **(confirmed)** cannot share again.
- **A failed share locks the user out.** The flag is set (line 121) before the post is sent (line 133). If the channel is missing or the send fails, they are marked as shared with nothing posted. A double click can also post twice, since the check and the set are separate.
- **Pages die after 60 seconds.** `ServerWrappedView` has `timeout=60` and each page flip creates a new view. Reading a page for a minute kills the buttons with no visual change — this is the "Interaction Failed" the footer warns about.
- **The server list dies on restart.** `WrappedListView` has `timeout=None` but no `custom_id`s and is not re-registered, so it is not actually persistent.
- **Closed DMs give "Interaction Failed".** `interaction.user.send` (line 211) runs before the interaction is answered; a `Forbidden` leaves it unanswered and still uses up the cooldown.
- **The cooldown is bypassable.** Each button creates its own `CooldownMapping` (line 194), so running `/wrapped` again starts fresh.
- **Only `final.png` is checked for eligibility.** A missing page image raises on the flip.

### 6.2 `/announce`

- **The success count is wrong.** `success += 1` runs before the send (line 306), so failures are counted in both totals.
- **One missing user aborts the run.** If `get_user` returns `None`, the `except` block's `user_obj.name` raises, the loop stops, and no report is sent. Re-running DMs everyone who already received it, since nothing records who was notified.
- **No pacing.** DMs go out back to back; a larger member list risks Discord flagging the bot.
- **It is a global command.** `/announce` and `/compile` are visible to every user in every server; only the ID check stops them.

### 6.3 Hardcoding

- The year "2025" is in a dozen strings and the folder name.
- `SERVER_NAMES` and `SHARE_CHANNELS` cover three servers; the bot is now in five **(confirmed)**. A folder for either new server raises `KeyError`.
- `Wrapped2025` is resolved from the working directory and does not exist on this machine; `/wrapped` raises if it is missing or contains a non-numeric name.
- `compile` writes to `C:/Users/demio/OneDrive/Desktop/Jupyter/Wrapped2025` and drops `user_map.json` in the working directory (currently an untracked file in the repo).
- `compile` downloads every avatar with blocking `requests` calls before answering, so the bot freezes and the interaction expires before the ✅ is sent.
- `compile` only sees current members. Someone who left mid-year can still appear in other people's stats (most-pinged, for example) with no name or avatar.

## Suggested order

1. Log exceptions in `on_error` and on cog load, and add a daily write-count report.
2. Switch the message update to an `_id` filter with upsert, and backfill missing users on `on_ready`.
3. Stop the archive trigger (or make it date-based) and keep events in Mongo.
4. Write voice events before making Discord calls, and add heartbeat-based reconciliation on `on_ready`.
5. Move message stats to daily buckets before the next year starts.
6. Fix the delivery bugs before the next announcement: DM support, `sent_wrapped`, view timeouts, `/announce` counting.
