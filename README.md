# artex-engage

OpenCode plugin that keeps ARTEX as the single durable engagement store.

- On OpenCode start: runs `bin/artex_store.py init` to ensure ARTEX is up.
- On every tool call: appends the raw event to the current engagement's
  hash-chained `events.jsonl` and mirrors a summary row into Postgres.

The authoritative log is the raw JSONL under
`/root/ARTEX/data/engagements/<eng>/events.jsonl` (append-only, `seq`/`ts`/`sha256`
chain, no redaction). The Postgres mirror only feeds the ARTEX UI. OpenCode owns
all writes and all testing decisions; ARTEX is storage + UI only.

## Install

Register the plugin in `opencode.jsonc`:

```jsonc
{
  "plugin": ["/absolute/path/to/artex-engage"],
}
```

Set the active engagement (writes the pointer at `/root/ARTEX/data/.current_engagement`):

```sh
python3 bin/artex_store.py engage <engagement-name>
```

## Store commands

`init`, `engage`, `company`, `scope`, `asset`, `task`, `finding`, `act`, `tool`,
`record`, `read`. Events are piped as JSON on stdin to `record`.
