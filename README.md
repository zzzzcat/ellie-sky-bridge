# Ellie Sky Bridge

Local bridge between Sky: Children of the Light and the existing Ellie chat in
SillyTavern.

The first milestone only handles chat:

1. Keep the Sky chat-history panel open.
2. Read new messages from `Big_Bro` with a vision API.
3. Submit an English in-game event to the currently open Ellie chat.
4. Preserve Ellie's complete response in SillyTavern.
5. Send only text outside `*action*` spans back to Sky.

Dry-run mode still focuses Sky and presses `C` when needed. It only suppresses
outgoing Ellie chat messages.

The SillyTavern extension does not send character-card data or old chat history
to the Python bridge. It only receives the new event and returns the newly
generated Ellie response.

## Setup

1. Review `config.json`.
2. Optional: create `.env` in this project directory to avoid entering the API
   key every launch:

   ```text
   OFOX_API_KEY=your_key_here
   ```

   `.env` is ignored by git. If this file is missing, the launcher prompts for
   the key without saving it.
3. Run the dry-run launcher:

   ```powershell
   .\start.ps1
   ```

4. Start SillyTavern, open Ellie's main chat, and leave that browser tab open.
5. Install/enable the `Ellie Sky Bridge` SillyTavern extension.
6. Reload the SillyTavern page after the extension is installed.
7. Start in dry-run mode:

   ```powershell
   .\start.ps1
   ```

8. After the log shows correct message detection and reply parsing, use live
   mode:

   ```powershell
   .\start.ps1 -Live
   ```

`Ctrl+Shift+F12` toggles pause globally while the bridge is running.

## Diagnostics

Each bridge launch writes a persistent diagnostic run under:

```text
state\diagnostics\YYYYMMDD-HHMMSS\
```

The latest run path is also written to:

```text
state\diagnostics\latest.txt
```

After stopping the bridge, inspect `events.jsonl` in that run directory. Each
line is one JSON event. The `images` subdirectory contains the exact chat-panel
and scene screenshots used for VLM calls.

Useful event types:

- `vlm_request`: the previous/current chat screenshots sent to the VLM.
- `vlm_response`: raw VLM text, parsed JSON, `new_messages`, and visible
  incoming messages.
- `message_decision`: whether a candidate message was submitted to
  SillyTavern or suppressed as a duplicate/outgoing echo.
- `sillytavern_reply`: pickup/generation timings and Ellie's raw reply.
- `sky_send_success` / `sky_send_dry_run`: outgoing game messages.

For repeated Big_Bro messages, compare the repeated `message_decision` events
with the preceding `vlm_response` events. If the same old text appears again in
`new_messages`, the duplicate originated in VLM new-message detection. If it is
suppressed, the ledger caught it locally.
