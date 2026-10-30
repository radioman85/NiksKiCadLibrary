---
name: dccex-remote-control
description: Use ONLY when implementing or extending the DCC-EX train control protocol in this repo's multi-purpose Pico W remote controller (Raspberry Pi Pico W v1/v2 with buttons, LEDs, 0.91" SSD1306 OLED and rotary encoder). The remote acts as a client/throttle for an EX-CommandStation, controlling trains, signals, streetlights, turnouts, outputs and EXRAIL routes over the DCC-EX Native Protocol; it also acts as a USB-serial gateway so a PC terminal can drive the command station (shell.py, exclusive TERMINAL mode). Contains the exact command/broadcast syntax, the implemented non-blocking WiFi client architecture (train_controller_sw/dccex.py), the CONTROL + 6-page MENU UI (TRAIN/SEARCH/CAB ENTRY/NETWORK/HW TEST/TERMINAL) with OLED layouts and LED semantics, plus guidelines for future controller modes. Trigger keywords: DCC-EX, EX-CommandStation, native protocol, throttle, cab, loco, signal, streetlight, turnout, accessory, output, roster, track power, E-stop, EXRAIL, Pico W, dcc_ex_train_controller, ARCHITECTURE.md, pin mapping, GPIO, menu, search, hw test, shell, terminal, PC terminal, USB serial, gateway.
version: 0.1.0
---

# DCC-EX Native Protocol on the Pico W Remote Control

## Start here: the docs and spec

Read these before making any code changes:

- `train_controller_sw/dcc_ex_train_controller.md` — hardware/behaviour spec.
  Single source of truth for the physical layout the code must match (note:
  it is a spec and may be ahead of the current code).
- `train_controller_sw/ARCHITECTURE.md` — current module structure, the
  interfaces between modules and the view/menu layout described below.
- `train_controller_sw/README.md` — deploy and runtime behaviour.

Things to keep in sync:

- Control items layout (OLED, D5/D6 corner LEDs, rotary encoder IC1, SW7/SW8,
  D1-D4/SW9-SW12 pairs) — `dcc_ex_train_controller.md`.
- GPIO pin mapping in its "Control Peripheral to Pico Interface" table
  (buttons GPIO 0-5, rotary GPIO 13-15, LEDs GPIO 16-21, I2C GPIO 8/9).
- Functional mapping table: button/encoder actions -> DCC-EX commands
  (e.g. rotary CW = `<t CAB speed 1>`, rotary press = E-stop speed -1).
- OLED line usage and LED indicator semantics. **D6 is the CS link, not a
  generic "WiFi" LED** (D1-D4 functions, D5 track power, D6 CS link).

`config.py` in `train_controller_sw/` must stay in sync with that table.

This skill is the reference for building and extending the DCC-EX client in
this project's remote control. It is grounded in the official docs at
<https://dcc-ex.com/>, which take precedence if anything here conflicts with
the installed EX-CommandStation version.

## What DCC-EX is

- **EX-CommandStation** (CS) is open-source command station firmware running on
  an Arduino/ESP32/etc. It drives DCC track power, decoders and layout I/O.
- The **DCC-EX Native Protocol** is the ASCII text protocol a client (our
  remote) uses to talk to the CS over USB/serial or a TCP socket.
- This product is a **client/throttle only**. It never generates DCC; it sends
  `<...>` commands to the CS, and the CS broadcasts state back.

## Data flow

```
Pico W remote                      EX-CommandStation                    Layout
  buttons/encoder  ->  dccex client ──TCP :2560──>  `<t 3 42 1>`  ->  DCC to decoder -> train
  OLED/LEDs       <-  broadcasts  <───────────────  `<l 3 5 170 1>`            ^
```

Both directions use the same message format. There is **no client
identification**: the CS broadcasts `...` messages to *every* client, and a
response is *not* guaranteed to be a direct reply to the last command sent.

## Message format (mandatory rules)

- Frame: `<` + single case-sensitive OPCODE + zero or more space-separated
  params + `>` e.g. `<t 3 42 1>`.
- Messages cannot be nested; a second `<` inside a message is a syntax error.
- On a byte stream (serial), frames are delimited by `<` and `>`. On a
  per-packet transport (WiFi), the **first byte** of an incoming message
  decides the protocol: `<<` native DCC-EX, anything else wiThrottle.
- OPCODEs are one char and case sensitive: `<F` is not `<f`.
- Keyword params are case *insensitive* (`<D WIFI ON>` == `<D wifi on>`).
- Numeric params are base-10 unless noted; strings are quoted `"..."`.
- Invalid/unrecognised commands get `<X>` back; empty list responses may be
  bare `<jT>`, `<jR>`, `<jA>` etc.
- A client **MUST accept and ignore anything it does not understand**
  (old CS versions broadcast older formats). This keeps the remote forward
  and backward compatible.

## Client state rules (throttle design)

These rules avoid the classic throttle bugs:

1. **Track power starts Unknown.** Only set it to On/Off when you either
   (a) send a power command yourself, or (b) receive a `<p ...>` broadcast.
   Never infer it from connection state.
2. **No "acquire" concept.** No handshake per loco; just send `<t ...>`.
3. **Broadcast-driven state.** Keep the remote's own model of every object
   (loco speed/functions, turnout state, sensor state) updated from
   broadcasts, not from the command you sent. Two throttles driving the same
   layout both see the same broadcasts, so this stays consistent.
4. **Never block on the network in the running loop.** After boot, the
   hardware loop must keep running even when WiFi or the CS is absent. Use
   short socket timeouts and background reconnect (`wifi.py` and
   `dccex.update()` already do this).
5. **Startup may block once, the loop never does.** With `WIFI_ENABLED` the
   boot phase blocks in `main.connect_to_command_station()` until the CS link
   is up (it renders `WAITING FOR DCC-EX WIFI` / `LINKING TO DCC-EX`). With
   `WIFI_ENABLED = False` the remote boots straight into offline mode and the
   controller renders as before — buttons/LEDs/OLED stay functional, track
   power stays Unknown, link shows "no CS".

## Reference: commands the remote sends

| Purpose | Command | Notes |
|---|---|---|
| Track power on/off | `<1>` / `<0>` | Optionally `<1 MAIN>` etc. when TrackManager is used. Broadcast `<p1>` / `<p0>`. |
| Loco speed+dir | `<t cab speed dir>` | `speed` 0-127 (0=stop), **-1 = E-stop**; `dir` **1=fwd, 0=rev**. Broadcast `<l ...>`. |
| Request loco status | `<t cab>` | Forces a `<l ...>` broadcast. |
| Loco function | `<F cab funct state>` | `funct` 0-68, `state` 1=on / 0=off. |
| Forget loco(s) | `<- cab>` or `<->` | Removes from CS reminders table (all with `<->`). |
| Emergency stop all | `<!>` | CS broadcasts `<l ...>` for every loco in its reminders. |
| Turnout list | `<JT>` | -> `<jT id1 id2 ...>`. |
| Turnout detail | `<JT id>` | -> `<jT id C|T|X "desc">`. |
| Throw/close turnout | `<T id state>` | `state` `1`/`T`=Throw, `0`/`C`=Close, `X`=examine. -> `<H ...>`. |
| List turnouts | `<T>` | -> one `<H ...>` per defined turnout. |
| Accessory (signals/streetlights/points on accessory decoders) | `<a addr subaddr activate>` | `addr` 0-511, `subaddr` 0-3, `activate` **0=off/Closed, 1=on/Thrown**. |
| Accessory linear form | `<a linear activate>` | `linear` 1-2044 (linear = addr*4+subaddr+1). |
| Extended accessory (signal aspect) | `<A address aspect>` | Sends a DCC Extended Accessories packet; `aspect` 0 always = stop, others decoder-defined. |
| Output / VPIN set | `<Z id state>` | `state` 0=INACTIVE, 1=ACTIVE. Streetlights on EX-IOExpander/VPINs. |
| Output list | `<Z>` | -> `<Y id vpin iflag state>` per output. |
| Sensor list/status | `S` / `<Q>` | `<S>` -> `<Q id vpin pullup>` per sensor; `<Q>` -> active/inactive list. |
| Route/automation list | `<JA>` / `<JA id>` | -> `<jA ...>`; detail -> `<jA id R|A "desc">`. |
| Start route/automation | `</ START [cab] id>` | Omit `cab` for a ROUTE; pass cab for an AUTOMATION. |
| Stop sequence | `</ KILL id>` | |
| Roster list/detail | `<JR>` / `<JR id>` | -> `<jR ...>`. |
| CS status | `<s>` | -> `<iDCCEX version / board / driver / build>` then turnout `<H>` each. |
| Cab slots | `<#>` | -> `<# n>` (20/30/50). |
| Store/clear EEPROM | `<E>` / `<e>` | Persists turnout/output/sensor defs. |
| Read CV | `<R cab CV>` | Programming track. |
| Write CV | `<W cab CV value>` | Programming track. |

Deprecated — do NOT use in new code: `<t slot cab speed dir>`, `<f cab ...>`,
`<T ...>` variants other than `<T id 0|1>`, and `<D>`.

## Reference: broadcasts the remote must parse

| Broadcast | Meaning |
|---|---|
| `l cab reg speedbyte functMap` | Loco state. `reg` is legacy/unused (any digit or -1 = not in reminders). `speedbyte`: reverse `2-127`=speed 1-126, `0`=stop, `1`=estop; forward `130-255`=speed 1-126, `128`=stop, `129`=estop. `functMap` = function bits (`1`=F0, `2`=F1, `3`=F0+F1...). |
| `p onoff [MAIN|PROG|JOIN]` | Track power: `<p0>`/`<p1>`/`<p1 JOIN>`. |
| `H id [DCC|SERVO|VPIN|LCN] ... 0|1` | Turnout state changed (also returned by `<T>`/`<T id X>`). |
| `q id` | Sensor deactivated. |
| `Q id` | Sensor activated. |
| `jT id state "desc"` | Turnout description/state query reply. |
| `jA id type "desc"` | Route/automation query reply. |
| `jB id param` | Route/automation state: `0` inactive, `1` active, `2` hidden, `"text"` label. |
| `jR id "desc" "f1/f2/..."` | Roster query reply. |
| `jO id type position count "desc"` | Turntable detail. `type` 0=DCC, 1=EXTT. |
| `jP id index angle "desc"` | Turntable position. |
| `i id position moving` | Turntable state broadcast. |
| `m "text"` | EXRAIL `MESSAGE(...)` text sent to all throttles (5.4+). |
| `r address` | Loco address read from programming track. |
| `O` / `X` | Success / error acknowledgements. |

Not all are needed for the current implementation: `dccex.py` parses `l`, `p`,
`i`, `jT`/`jA` lists, `jR` roster, `Y`/`Q` scan items and `O`/`X`; everything
else is accepted and ignored.

## Transport: state of the implementation

`dccex.py` already implements the client described below. Extend it, do not
re-create it.

### dccex.py — DCC-EX client (implemented)

```python
class DCCEX:
    def __init__(self, host, port, wifi, state):
        # host/port from config; wifi injected only for is_connected();
        # state is the shared RemoteState the client updates from broadcasts.
        self.state = state          # written by _handle()-ers
        self.sock = None            # dropped/reopened in the background
        self.buffer = b""           # incomplete-frame buffer
        self.on_frame = None        # optional callback(frame_bytes); the USB shell uses it
        ...
    def update(self):               # non-blocking: drain socket, parse frames
        ...
    def send(self, command):        # str like "<t 3 42 1>", sendall(), -> bool
        ...
```

Behaviour:

- `update()` is gated by `WiFi.is_connected()`; no socket is even attempted
  while WiFi is down. Reconnects happen at most every
  `DCCEX_RECONNECT_INTERVAL` (`_connect_if_due`), with `DCCEX_CONNECT_TIMEOUT`
  for the connect and `DCCEX_SOCKET_TIMEOUT` (0.05 s) on the connected socket.
  A timed-out-but-quiet socket is *not* treated as an error (EAGAIN/
  ETIMEDOUT/ETIME are distinguished from a real drop). `_drop()` always closes
  and clears the parse buffer.
- On (re)connect it sends `<s>`, `<t CAB>`, `<JR CAB>` to learn the CS
  identity, (re)acquire the loco and fetch its roster name.
- `send()` fire-and-forgets with `sendall()`, appends a closing `>` and `\n`
  when missing, and returns False if no socket is up.
- The frame reader (`_drain`) keeps a byte buffer, strips everything before
  the first `<`, extracts one `<...>` frame, dispatches it, and continues;
  incomplete frames stay buffered. Bytes outside a frame and any frame with an
  unknown opcode are ignored — mandated by the protocol.
- Dispatch handlers only update the injected `RemoteState`. They never do I/O,
  so the OLED/LED rendering stays a pure read of local state. Handlers:
  `l` (loco state for the active `state.loco_cab`; while a LOCO scan runs it
  also collects every frame whose reminder slot is `!= -1` as a search item),
  `p` (power), `i` (cs_info), `jT`/`jA` + `Y`/`Q` + `jR` (search items /
  roster detail for `loco_cab`), `O`/`X` (both mark a running search done;
  `X` also records `last_error`).
- `_handle()` first calls the optional `on_frame(frame)` observer hook with
  every complete frame, before parsing. `shell.py` registers itself there to
  stream CS broadcasts to the PC terminal while TERMINAL mode is active.

## Integration with this repo's layout (implemented)

The composition root is `main.py`; it builds hardware first, optional network
services second, and keeps everything non-blocking in the loop.

1. `config.py` — holds `DCCEX_HOST`, `DCCEX_PORT = 2560`, the default cab
   `DCCEX_LOCO_ADDRESS` (overridable at runtime from SEARCH or CAB ENTRY and
   then persisted), reconnect intervals/timeouts, and the UI constants
   (`MODE_*`, `MENU_*`, `SEARCH_*`, `HW_*`).
2. `storage.py` — flash persistence: `load_cab()/save_cab()/clear_cab()`
   against `/state.json`. `main.py` restores the cab after `RemoteState()`;
   `remote.py` saves it on every cab bind (SEARCH selection or CAB ENTRY
   commit). All access guarded, corrupt file → `None` → config default.
3. `main.py` — factory `create_remote()` builds `Inputs`, `Outputs`,
   `RemoteState`, `WiFi`, `DCCEX` and injects them into `RemoteControl`.
   `connect_to_command_station()` blocks at boot while integrating (renders
   `startup_status`). The loop runs
   `remote.update_inputs()` → `remote.update_outputs()` →
   `wifi.update()` → `dccex.update()` → `web.update()` →
   `shell.update()`. `KeyboardInterrupt` (Ctrl+C) is swallowed only while
   the exclusive PC-terminal mode is active (cancel-line); otherwise `main.py`
   re-raises it so the REPL stays reachable — mpremote/deploy interrupt a
   running app with Ctrl+C before entering raw REPL.
4. `remote.py` — `RemoteControl` is the *bridge*: it reads raw inputs from
   `Inputs`, decides per mode which DCC-EX commands to issue via
   `DCCEX.send(...)`, and stores broadcast-updated state in `RemoteState`.
5. `webpage.py` — HTTP observer of `RemoteState`, serviced only while
   `WiFi.is_connected()`, short-timeout sockets.
6. `shell.py` — interactive PC-terminal console over the USB-CDC serial line
   (`sys.stdin`/`sys.stdout`). Built with `Shell(dccex, remote.state)`,
   serviced by `shell.update()` every tick. Reads stdin non-blocking via
   `select.select`, line-buffers with local echo/backspace, and forwards
   lines to `DCCEX.send()`. Only active while `state.terminal_active`
   (see the TERMINAL menu page below); otherwise it drains stdin and shows a
   hint, sending nothing to the CS. Registers itself on `dccex.on_frame` to
   stream broadcasts to the terminal.

Invariant (from README): **hardware never waits on the network** and **the
physical-input loop runs regardless of network state**.

## UI model (implemented): CONTROL view + 5-page MENU

The remote currently implements one controller type (THROTTLE). There is no
mode ring yet; instead there is a CONTROL view and a settings MENU reached by
holding the rotary center button ≥ 2 s. See `ARCHITECTURE.md` §3 for the OLED
line layouts.

### Startup view

Rendered only while `WIFI_ENABLED` and the boot handshake runs
(`startup_status` non-empty): one centered line, either
`WAITING FOR DCC-EX WIFI` (scanning/joining the CS network) or
`LINKING TO DCC-EX` (TCP link being opened). Truncated to 16 chars.

### Controller view (type-specific)

Rendered by `Outputs._render_control()`. Currently the THROTTLE view:

- line 1 `THROTTLE  CAB <addr>`
- line 2 `FWD/REV <speed>` or `STOP` / `EMERGENCY STOP`
- line 3 `F0-F3 <state>` (four function bits, mirrors D1-D4)
- line 4 `PWR <on/off/?>  <CS/no CS>`

Actions (mapping table in the spec):

- rotary CW/CCW: `<t CAB speed 1|0>` (signed speed, 0 = stop; direction is the
  sign of the last turn);
- center short press (< 2 s): E-stop `<t CAB -1 0>`;
- center hold (≥ 2 s): enter the settings menu;
- SW9..SW12: toggle F0..F3 (`<F CAB n 1|0>`);
- SW7: reserved; SW8: reserved (exits the menu when in it).

### Menu (6 pages, `MENU_PAGES` in config.py)

Entered with a ≥ 2 s center hold. Rotary cycles `menu_index`;
SW8 leaves the menu back to CONTROL. Pages:

| page | content |
|---|---|
| TRAIN — Controller info | line 1 controller type (`THROTTLE`), line 2 DCC address (`CAB n`). **Background info only by design — no live control states**, no other lines. Roster name / CS version are optional static additions. |
| SEARCH | SW7 cycles device type (LOCO/TURNOUT/OUTPUT/SENSOR/ROUTE); center starts a scan. Other types use list queries `<JT>` `<Z>` `<S>` `<JA>`. A LOCO scan has no list command (DCC-EX enumerates locos only via the in-use *reminder table*), so it probes every short address 1..`SEARCH_LOCO_PROBE_MAX` with the non-intrusive `<t cab>` status call and keeps only `<l ...>` frames whose slot `reg != -1` (the CS answers unused addresses with a dummy `<l cab -1 128 0>`). A scan ends on `<O>`, on `<X>`, or after `SEARCH_TIMEOUT_MS`, so it can never hang. Result lines: `SEARCH <TYPE>` / `scanning...`-`n found` / selected hit / hint. With results on screen the rotary browses them (`search_sel`) — it no longer changes pages; the center press on a highlighted **LOCO** rebinds the throttle to that cab (`state.loco_cab`, `<t cab>` + `<JR cab>` acquired, local throttle reset), then returns to CONTROL. The chosen cab is persisted to `/state.json` (`storage.py`) and restored at boot, so a reboot returns to the same train; the next SEARCH selection simply overwrites it. Results capped at `SEARCH_MAX_ITEMS`. |
| CAB ENTRY | Manual DCC address entry (menu_index `MENU_CAB`), for short *and* long addresses 1..10239 the LOCO scan cannot discover by probing. Five digit slots hold the place values 10⁴..10⁰ (`CAB_ENTRY_DIGITS`), a caret (`^`) marks the active slot. The rotary **edits the active digit** (0–9, wraps) instead of paging; a short center press advances the cursor; on the final **OK** slot (position `CAB_ENTRY_DIGITS`) it commits. Commit validates 1 ≤ value ≤ `CAB_MAX_ADDRESS` and then behaves exactly like a SEARCH rebind — persist to `/state.json`, `<t cab>` + `<JR cab>`, local throttle reset, back to CONTROL. Invalid commits (0, or > 10239) set `cab_entry_err`, blank the entry and stay on the page. SW7 is backspace: at slot 0 it resets the whole entry, at the OK slot it steps back onto the last digit, elsewhere it zeroes the active digit and steps back one. Entering the page (via `_set_menu_index`) always resets the editor. SW8 escapes to CONTROL without persisting. |
| NETWORK | read-only: `NETWORK SETTINGS` / SSID / password / `IP <host>`. |
| HW TEST | center starts the test: all LEDs blink once in sequence, then all 7 switches (center + SW7..SW12) must be pressed once in any order, each lighting its paired LED; `ALL SWITCHES OK` flashes before returning. Line details in `ARCHITECTURE.md` §3. |
| TERMINAL | center short toggles the exclusive PC-terminal mode. **All physical controls are frozen while it is active** — only the center exit press responds — so the PC and the handheld operator cannot drive the train simultaneously. |

### PC Terminal (USB serial gateway to the CS)

From the TERMINAL menu page a short center press sets `state.terminal_active`;
the OLED switches to the `PC TERMINAL / ACTIVE` view, and the USB serial line
becomes a command console bridging the PC to the command station:

- PC side: any serial terminal (`screen /dev/ttyACM0 115200`, `picocom`,
  `mpremote repl` — the normal REPL prompt is replaced by `DCCEX>`).
  **Pitfall:** never use `mpremote run main.py` as the console. `run` is
  raw-REPL script execution — keystrokes are not forwarded to a running
  script (the char echo you see is mpremote, not the board), so the shell
  seems dead. Boot/reset the board and connect `screen` (or `mpremote repl`,
  which does forward input) instead.
- Commands: raw DCC-EX frames are forwarded verbatim (`<t 3 24 1>`, `<1>`,
  `<T 5 1>`, ...), and a few local shorthands expand to frames:
  `speed <n> [f|r]`, `power on|off`, `stop [all]` (`<!>` broadcast when
  `all`), `func <n> [on|off]`,
  `status`, `help`/`?`. `status` prints cab, signed speed, function bits,
  track power and CS link.
- Broadcasts: every CS frame (`<l ...>`, `<p ...>`, ...) arriving on the
  socket is echoed to the terminal via the `on_frame` hook, so the PC sees
  live state.
- Exclusive control: while active, `RemoteControl.update_inputs()` drains the
  rotary and returns early; every other button is ignored. A short center
  press clears the flag and returns to the TERMINAL menu page (and to normal
  handheld control).
- `Ctrl+C` on the console arrives as `KeyboardInterrupt` in MicroPython even
  while the loop runs; both `main.py` and `Shell.update()` treat it as
  cancel-line ONLY while TERMINAL mode is active. Outside that mode the
  interrupt propagates and stops the program, so the REPL / `mpremote cp` /
  `deploy.sh` keep working without a board reset.

### LED semantics (D1..D6)

D1-D4: F0-F3 function state (from broadcasts). D5: track power (from `<p ...>`).
D6: **CS link** (`cs_link`), set when the TCP link to the CS is up — not a raw
WiFi indicator.

## Future: extending to a multi-type "assignable" controller

The goal is that the remote becomes a generic assignable controller. When the
THROTTLE/single-menu model is replaced, keep this shape in mind:

- **Mode ring** (rotary-click or button cycles): THROTTLE, SIGNALS,
  STREETLIGHTS, TURNOUTS, ROUTES, SETUP. The mode decides what the
  rotary/buttons control and what the LEDs mean.
- **Object selection**: rotary scrolls the object list for the current mode
  (loco roster, signal ids, output ids, turnout ids). A second rotary action
  or dedicated buttons change the value:
  - THROTTLE: rotary = speed 0-127 step; direction button; F0-F28 button(s);
    push-to-toggle stop; long-press `<!>` E-stop.
  - SIGNALS: `<a addr subaddr 1>` for the chosen aspect, `<a addr subaddr 0>`
    otherwise; or `<A addr aspect>`.
  - STREETLIGHTS/TURNOUTS: `<a addr subaddr activate>` or `<Z id state>`
    toggle (0<->1). Turnouts prefer `<T id T|C>`.
  - ROUTES: trigger `</ START [cab] id>`.
- **OLED** (128x32, 4 rows): row 1 mode/title, row 2 selected object + state,
  row 3 value/status, row 4 track power + link. Center text with `ssd1306`'s
  `.text()` as already used in `outputs.py`.
- For decoders that support it, use roster `<JR id>` to show friendly
  locomotive names and function maps on the OLED instead of raw DCC addresses.

## Offline behaviour and diagnostics

- No WiFi / no CS: the controller/OLED render normally with `no CS` and track
  power `?`; all buttons stay functional (UI responds locally).
- Note the boot exception: with `WIFI_ENABLED` the remote blocks at startup
  until the CS access point and TCP link are up (startup view), then the loop
  is fully non-blocking.
- If a board is a RetDCC/older CS that broadcasts `<l cab reg speed ...>`
  with `reg` as a digit, ignore it as with any unknown variant.
- Use `<s>` + parsing `<iDCCEX ...>` at startup to confirm which CS firmware
  revision you are talking to before enabling newer features such as
  `<jB>`/`<m>` broadcasts.

## Pico W v1 vs v2

Same MicroPython code runs on both. Pico W v1 (RP2040, 2 MB flash) and
Pico 2 W (RP2350, 4 MB flash) share the `machine`/`network` API, the CYW43
WiFi chip, and GPIO. Differences that matter here:
- v2 has more RAM/CPU for parsing larger roster/JMRI-style broadcasts and
  faster OLED redraws; keep buffering frugal on v1.
- No change to the DCC-EX protocol work is needed between them; only bump RAM
  budgets (socket buffer `DCCEX_BUF_SIZE = 1024` is fine on both).

## Pitfalls checklist

- DO NOT assume responses correlate with commands (no client id).
- DO NOT block the running loop on `connect()`/`recv()` — always short
  timeouts (startup blocking is allowed; the running loop never is). The USB
  shell must use `select` with a 0 timeout on `sys.stdin`, never a blocking
  `input()`/`readline()`.
- DO keep the USB shell inert unless TERMINAL mode is active — it must not
  forward typed lines to the CS while the handheld operator has control.
- DO swallow `KeyboardInterrupt` ONLY while TERMINAL mode is active (Ctrl+C =
  cancel line). Outside TERMINAL mode let the interrupt propagate and stop the
  program so the REPL and `mpremote cp`/deploy work without a power cycle.
- DO NOT stop updating the OLED/LEDs when the link is down.
- DO track power as Unknown initially and only via `<p ...>` afterwards.
- DO ignore/`accept` unknown broadcasts rather than erroring.
- DO use `sendall()` (MicroPython `socket.send` may send partial frames).
- DO NOT wait for ACK before sending the next loco command (throttle-style
  fire-and-forget is expected by the CS).
- Remember params: numeric are base-10 except `<M>`/`<P>` raw-packet hex and
  `<p>`/`<t>` speed encodings above.

## References

- Hardware/behaviour spec (pin mapping, functional mapping, display):
  `train_controller_sw/dcc_ex_train_controller.md`
- Structure/interfaces/views: `train_controller_sw/ARCHITECTURE.md`
- Deploy/runtime behaviour incl. the PC terminal: `train_controller_sw/README.md`
- DCC-EX Native API Reference (parsing rules):
  <https://dcc-ex.com/reference/developers/api.html>
- Native Commands Summary Reference (authoritative command list):
  <https://dcc-ex.com/reference/software/command-summary-consolidated.html>
- Throttle Developers Technical Reference:
  <https://dcc-ex.com/throttles/tech-reference.html>
- EXRAIL scripting + myAutomation.h (REST/defines on the CS side):
  <https://dcc-ex.com/exrail/index.html>
- DCCEXProtocol: Arduino throttle-side library with porting ideas:
  <https://github.com/DCC-EX/DCCEXProtocol>