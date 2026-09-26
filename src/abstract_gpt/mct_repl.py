"""MCT pointer-exchange REPL using Codex exec and persisted thread IDs.

Adapted from abstract_claude's MCT arbiter and terminal UI.
See MCT-LICENSE.txt for the original copyright and license.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

from .mct_archive import Archive
from . import actions

CYAN, DIM, BOLD, RESET = "\033[36m", "\033[2m", "\033[1m", "\033[0m"
WHITE = "\033[97m"
PROMPT = f"{BOLD}mct>{RESET} "


def _rl(prompt: str) -> str:
    """Readline needs every zero-width escape in a prompt bracketed by \001…\002,
    otherwise it counts the colour codes as visible columns, wraps early and
    redraws the typed line over its own start (the classic unescaped-PS1 bug)."""
    import re
    return re.sub(r"(\x1b\[[0-9;?]*[A-Za-z])", "\001\\1\002", prompt)
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _term_width() -> int:
    try:
        return max(40, shutil.get_terminal_size().columns)
    except (ValueError, OSError):
        return 80


def _rule(text: str = "") -> str:
    """A dim horizontal rule spanning the full terminal width."""
    w = _term_width()
    s = f"── {text} " if text else ""
    return DIM + s + "─" * max(0, w - len(s)) + RESET


def _fmt_dt(dt: float) -> str:
    return f"{int(dt // 60)}m{int(dt % 60):02d}s" if dt >= 60 else f"{dt:.0f}s"


def _clip(text: str):
    """Copy text to the terminal's clipboard via OSC 52 (tmux-wrapped when
    inside tmux). Needs OSC 52 support in the viewing terminal."""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    seq = f"\033]52;c;{b64}\a"
    if os.environ.get("TMUX"):
        seq = "\033Ptmux;" + seq.replace("\033", "\033\033") + "\033\\"
    sys.stdout.write(seq)
    sys.stdout.flush()


def _tool_line(name: str, inp: dict, strip_prefix: str = "") -> str:
    """One compact line for a streamed tool call: tool name + its most
    telling argument, workspace prefix stripped, capped to the terminal."""
    for k in ("file_path", "path", "command", "pattern", "query",
              "description", "url", "prompt", "text"):
        v = inp.get(k)
        if v:
            s = str(v).replace("\n", " ")
            if strip_prefix:
                s = s.replace(strip_prefix, "")
            return f"{name} · {s[:max(20, _term_width() - len(name) - 10)]}"
    return name


_FENCE_RE = re.compile(r"^\s*```\s*(\S*)\s*$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")


def _render_md(body: str, blocks: list) -> str:
    """Render A's markdown for the terminal: white text reflowed to the full
    terminal width; fenced code blocks framed verbatim with a language label
    and a `/copy N` handle (contents appended to `blocks`)."""
    width = _term_width()
    out, para = [], []

    def flush():
        if para:
            txt = " ".join(p.strip() for p in para)
            for ln in textwrap.fill(txt, width).splitlines():
                out.append(WHITE + ln + RESET)
            para.clear()

    in_code, lang, code = False, "", []
    for raw in body.splitlines():
        m = _FENCE_RE.match(raw)
        if m:
            if in_code:
                blocks.append("\n".join(code))
                head = f"── {lang or 'code'} · copy: /copy {len(blocks)} "
                out.append(DIM + head + "─" * max(0, width - len(head)) + RESET)
                out.extend(WHITE + c + RESET for c in code)
                out.append(DIM + "─" * width + RESET)
                in_code, lang, code = False, "", []
            else:
                flush()
                in_code, lang = True, m.group(1)
            continue
        if in_code:
            code.append(raw)
            continue
        s = raw.rstrip()
        if not s.strip():
            flush()
            out.append("")
            continue
        st = s.lstrip()
        if st.startswith("#"):
            flush()
            out.append(BOLD + WHITE + st.lstrip("#").strip() + RESET)
            continue
        if st.startswith(("|", ">")):
            flush()
            out.append(WHITE + s + RESET)
            continue
        lm = _LIST_RE.match(s)
        if lm:
            flush()
            indent = " " * len(lm.group(0))
            for ln in textwrap.fill(s, width,
                                    subsequent_indent=indent).splitlines():
                out.append(WHITE + ln + RESET)
            continue
        para.append(s)
    flush()
    if in_code and code:        # unterminated fence: show it anyway
        blocks.append("\n".join(code))
        out.extend(WHITE + c + RESET for c in code)
    return "\n".join(out)

SYSTEM = """\
You are A, the frontier seat of a hugpy Station, in the mct pointer-exchange
test grounds. Each turn your prompt arrives as a FILE POINTER and your answer
leaves as a FILE: Read the prompt file, Write your complete answer (markdown)
to the response file. The operator's terminal renders the cat of both files,
so the response file IS your visible answer — put everything there. Your chat
reply must be exactly the single acknowledgement line you were asked for.
Your session is resumed across turns, so you may remember earlier exchanges;
the exchange files remain the canonical record of the conversation.
Some prompts arrive as an explicit [B digest] of several interim messages from
C, possibly opening with a reconcile brief about an aborted prior turn — the
framing is part of the contract: later messages supersede earlier ones where
they conflict, and after an abort you verify workspace state before building
on it."""


def _default_ws() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return cfg / "abstract_gpt" / "mct" / "repl"


def _setup_readline(ws: Path):
    try:
        import readline
        hist = ws / "repl_history"
        try:
            readline.read_history_file(hist)
        except OSError:
            pass
        import atexit
        atexit.register(lambda: _write_hist(hist))
    except ImportError:
        pass


def _write_hist(hist: Path):
    try:
        import readline
        readline.write_history_file(hist)
    except OSError:
        pass


def _next_turn(exchange: Path) -> int:
    last = 0
    for p in exchange.glob("turn-*-prompt.md"):
        m = re.match(r"turn-(\d+)-prompt\.md$", p.name)
        if m:
            last = max(last, int(m.group(1)))
    return last + 1


def _system_prompt(ws: Path) -> str:
    # the directive reaches this seat the same way it reaches the mct seat:
    # the station composes <ws>/operator-guidance.md at launch
    guidance = ws / "operator-guidance.md"
    try:
        extra = guidance.read_text(encoding="utf-8").strip()
    except OSError:
        extra = ""
    return SYSTEM + ("\n\n" + extra if extra else "")


def _cat_frame(label: str, path: Path, body: str, color: str = WHITE):
    print(_rule(f"cat {path} ─ {label}"))
    print((color + body.rstrip("\n") + RESET) if color else body.rstrip("\n"))


def _md_frame(label: str, path: Path, body: str, blocks: list):
    """The response frame: full-width rule, then rendered markdown."""
    print(_rule(f"cat {path} ─ {label}"))
    print(_render_md(body.rstrip("\n"), blocks))


class Arbiter:
    """B, deterministic: runs turns in a worker thread so the read loop stays
    open, and disposes of interim lines per docs/QUERY-ARBITRATION-SOP.md."""

    TICK_S = 0.5        # live spinner redraw cadence
    POLL_S = 5          # idle-timeout poll cadence: how often _reap re-checks
                        # "time since last subprocess output" while the turn runs

    def __init__(self, ws: Path, exchange: Path, model: str, timeout: int,
                 fresh: bool = False, dangerous: bool = False):
        self.ws, self.exchange = ws, exchange
        self.archive = Archive(ws)
        self.archive.start()
        self.model, self.timeout = model, timeout
        self.dangerous = dangerous
        self.lock = threading.Lock()
        self.print_lock = threading.Lock()   # spinner vs activity lines
        self.tui = False            # True under the prompt_toolkit box: the
                                    # status bar replaces in-place redraws
        self.flight = None          # {"proc","tag","t0","interrupted",...}
        self.blocks = []            # code blocks of the last response (/copy N)
        self.coalesce = []          # interim texts for the next digest turn
        self.appendq = []           # texts, one own turn each, after digest
        self.reconcile = None       # tag of the last aborted turn, unbriefed
        # A's chat history: the Codex session this REPL resumes each turn.
        # Persisted so relaunching the REPL keeps the conversation too.
        self.sid = None
        self.sid_path = ws / "session.json"
        self.forgot_sid = None      # id dropped by --fresh at startup (banner)
        try:
            doc = json.loads(self.sid_path.read_text(encoding="utf-8"))
            self.sid = (doc.get("session_id") or "").strip() or None
        except (OSError, ValueError):
            pass
        self.status_path = ws / "status.json"
        if fresh:
            # Forget only this workspace thread; keep Codex login intact.
            self.forgot_sid, self.sid = self.sid, None
            self._save_sid()
        self._write_status()

    # ── lanes ────────────────────────────────────────────────────────────
    def dispatch(self, line: str):
        """One line from C. Prefix picks the lane; idle lines start a turn."""
        if line.startswith("todo:") or line.startswith("?"):
            return self._capture(line.split(":", 1)[1].strip()
                                 if line.startswith("todo:")
                                 else line.lstrip("?").strip())
        with self.lock:
            busy = self.flight is not None
        if line.startswith("!"):
            body = line[1:].strip()
            if busy:
                return self._interrupt(body)
            # nearest weaker lane, said out loud (SOP: floor and ceiling)
            print(f"{DIM}· no turn in flight — nothing to interrupt; "
                  f"running it as a plain turn{RESET}")
            return self._start(body)
        if line.startswith("+"):
            body = line[1:].strip()
            if busy:
                with self.lock:
                    self.appendq.append(body)
                    n = len(self.appendq)
                print(f"{DIM}⊕ appended — own turn after current work "
                      f"({n} queued){RESET}")
                self._write_status()
                return
            return self._start(body)
        if busy:
            with self.lock:
                self.coalesce.append(line)
                n = len(self.coalesce)
            print(f"{DIM}⇄ coalesced into the next slot ({n} pending — "
                  f"'!' to interrupt instead){RESET}")
            self._write_status()
            return
        return self._start(line)

    def _capture(self, body: str):
        if not body:
            print(f"{DIM}· empty capture ignored{RESET}")
            return
        with self.lock:
            during = f"during {self.flight['tag']}" if self.flight else "idle"
        stamp = time.strftime("%Y-%m-%d %H:%M")
        todo = self.ws / "todo.md"
        with todo.open("a", encoding="utf-8") as f:
            f.write(f"- [ ] {stamp} {body}  (captured {during})\n")
        print(f"{DIM}☑ captured → {todo} (no turn){RESET}")

    def _interrupt(self, body: str):
        with self.lock:
            fl = self.flight
            if fl is None:      # turn ended while C typed — weaker lane
                self.coalesce.append(body)
                print(f"{DIM}· turn already finished — coalesced instead{RESET}")
                return
            fl["interrupted"] = True
            if body:
                self.coalesce.append(body)
            tag, proc = fl["tag"], fl["proc"]
        print(f"{DIM}✋ interrupting {tag} — reconcile brief will open the "
              f"next turn{RESET}")
        self._write_status()
        try:
            proc.terminate()
            threading.Timer(5, proc.kill).start()   # SIGTERM grace, then kill
        except OSError:
            pass

    # ── turns ────────────────────────────────────────────────────────────
    def _start(self, text: str, note: str = "", fresh: bool = False):
        if not text:
            return
        n = _next_turn(self.exchange)
        tag = f"turn-{n:04d}"
        p_prompt = self.exchange / f"{tag}-prompt.md"
        p_resp = self.exchange / f"{tag}-response.md"
        p_prompt.write_text(text + "\n", encoding="utf-8")
        pointer = (f"mct pointer exchange, {tag}.\n"
                   f"1. Read your prompt from: {p_prompt}\n"
                   f"2. Write your complete answer (markdown) to: {p_resp}\n"
                   f"Reply in chat with only: DONE {tag}")
        sysp = _system_prompt(self.ws)
        with self.lock:
            sid = None if fresh else self.sid
        cmd = [actions.binary(), *actions.toolserver_args(), "exec", "--json", "--skip-git-repo-check"]
        if self.dangerous:
            cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            cmd += ["--sandbox", "workspace-write", "-c", 'approval_policy="never"']
        cmd += ["-c", "developer_instructions=" + json.dumps(sysp)]
        if self.model:
            cmd += ["--model", self.model]
        if sid:
            cmd += ["resume", sid]
        cmd += [pointer]
        try:
            proc = subprocess.Popen(cmd, cwd=self.ws, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    bufsize=1, env=actions.env())
        except FileNotFoundError:
            print(f"{DIM}✗ Codex CLI not found; set AG_CODEX_BIN{RESET}")
            return
        fl = {"proc": proc, "tag": tag, "t0": time.monotonic(),
              "last_out": time.monotonic(),   # bumped on every stream read; the
                                              # idle-timeout in _reap measures
                                              # `now - last_out`, never wall-clock
              "wall0": time.time(), "interrupted": False, "prompt": p_prompt,
              "resp": p_resp, "note": note, "text": text,
              "resumed": bool(sid)}
        with self.lock:
            self.flight = fl
        sess = f"resume {sid[:8]}…" if sid else "new session"
        print(f"{DIM}⇢ prompt → {p_prompt.name} · pointers → A "
              f"(codex exec, {sess}{', ' + self.model if self.model else ''})"
              f"{' · ' + note if note else ''}"
              f" · REPL stays open: lines coalesce, '!' interrupts{RESET}",
              flush=True)
        self._write_status()
        threading.Thread(target=self._reap, args=(fl,), daemon=True).start()
        threading.Thread(target=self._tick, args=(fl,), daemon=True).start()

    def _tick(self, fl):
        # live flight state: one spinner+timer line redrawn in place (no more
        # printed 30s heartbeats). C's partially typed input is re-echoed
        # after the prompt so typing stays visible through redraws.
        if self.tui:            # the bottom status bar shows this instead
            return
        i = 0
        while True:
            time.sleep(self.TICK_S)
            with self.lock:
                if self.flight is not fl:
                    return
            dt = time.monotonic() - fl["t0"]
            try:
                import readline
                buf = readline.get_line_buffer()
            except ImportError:
                buf = ""
            spin = SPIN[i % len(SPIN)]
            i += 1
            # one physical line only: `\r` returns to the start of the line the
            # cursor is on, so a re-echo that wraps would be redrawn from its
            # own second row and stack/overwrite. Keep the visible tail.
            try:                       # the tty itself, not $COLUMNS (stale under a
                w = os.get_terminal_size(sys.stdout.fileno()).columns   # resized ssh)
            except (OSError, ValueError):
                w = shutil.get_terminal_size().columns
            hint = " · '!' interrupts" if w >= 64 else ""
            head = f"{spin} {fl['tag']} · {_fmt_dt(dt)}{hint}  mct ⏳> "
            room = max(w - len(head) - 4, 8)      # -4: ⏳ and the spinner are wide glyphs
            if len(buf) > room:
                buf = "…" + buf[-(room - 1):]
            with self.print_lock:
                sys.stdout.write(
                    f"\r\033[2K{CYAN}{spin}{RESET} {DIM}{fl['tag']} · "
                    f"{_fmt_dt(dt)}{hint}{RESET}  "
                    f"{BOLD}mct ⏳>{RESET} {buf}")
                sys.stdout.flush()

    def _stream(self, fl, doc):
        """Persist Codex JSONL and render tool activity and acknowledgements."""
        for raw in fl["proc"].stdout:
            with (self.exchange / (fl["tag"] + "-stream.jsonl")).open("a", encoding="utf-8") as log:
                log.write(raw)
            fl["last_out"] = time.monotonic()
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "thread.started":
                doc["sid"] = event.get("thread_id") or doc["sid"]
            elif kind in ("item.started", "item.updated", "item.completed"):
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = (item.get("text") or "").strip()
                    if text:
                        doc["ack"] = text.splitlines()[-1]
                        self._activity(fl, "💬 " + text.splitlines()[0])
                else:
                    detail = item.get("command") or item.get("tool") or item.get("type", "activity")
                    self._activity(fl, "⚒ " + str(detail))
            elif kind in ("error", "turn.failed"):
                error = event.get("error") or {}
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                self._activity(fl, "✗ " + (event.get("message") or message or kind))

    def _activity(self, fl, text):
        with self.lock:
            if self.flight is not fl:
                return
        with self.print_lock:
            w = _term_width()
            lead = "" if self.tui else "\r\033[2K"
            sys.stdout.write(f"{lead}{DIM}  {text[:w - 2]}{RESET}\n")
            sys.stdout.flush()

    def _reap(self, fl):
        proc = fl["proc"]
        doc = {"ack": None, "sid": None}
        err_buf = []
        t_out = threading.Thread(target=self._stream, args=(fl, doc),
                                 daemon=True)
        t_err = threading.Thread(
            target=lambda: err_buf.append(proc.stderr.read() or ""),
            daemon=True)
        t_out.start()
        t_err.start()
        # IDLE-reset timeout (not wall-clock): a turn is killed only after
        # `idle_cap` seconds with NO new subprocess output — i.e. genuinely
        # hung. A healthy turn that keeps streaming (tool calls, notes, text)
        # bumps fl["last_out"] in _stream and is never capped, however long.
        idle_cap = self.timeout if (self.timeout and self.timeout > 0) \
            else _default_idle_timeout()
        timed_out = False
        while True:
            try:
                proc.wait(timeout=self.POLL_S)
                break                       # child exited on its own → done
            except subprocess.TimeoutExpired:
                if time.monotonic() - fl["last_out"] > idle_cap:
                    proc.kill()
                    proc.wait()
                    timed_out = True
                    break
        rc = -1 if timed_out else proc.returncode
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        if not t_out.is_alive():
            proc.stdout.close()
        if not t_err.is_alive():
            proc.stderr.close()
        err = err_buf[0] if err_buf else ""
        with self.lock:
            live, self.flight = self.flight, None
        if live is None:    # defensive: never true in practice
            return
        dt = time.monotonic() - fl["t0"]
        ack, sid = doc["ack"], doc["sid"]
        (self.exchange / (fl["tag"] + "-stderr.txt")).write_text(err, encoding="utf-8")
        self.archive.event("turn_finished", tag=fl["tag"], session_id=sid,
                           exit_code=rc, interrupted=fl["interrupted"], timed_out=timed_out)
        self.archive.queue_turn(fl, self.model, sid, dt, ack)
        if sid and not fl["interrupted"]:
            with self.lock:
                self.sid = sid
            self._save_sid()
        if not self.tui:
            sys.stdout.write("\r\033[2K")   # clear the spinner/prompt line
            sys.stdout.flush()
        if fl["interrupted"]:
            print(f"{DIM}✋ {fl['tag']} aborted mid-flight at C's request "
                  f"({dt:.1f}s in). Partial workspace changes may remain; the "
                  f"next turn is briefed to reconcile.{RESET}\n")
            with self.lock:
                self.reconcile = fl["tag"]
        elif timed_out:
            print(f"{DIM}✗ {fl['tag']} killed after {idle_cap}s with no output "
                  f"(idle timeout — healthy streaming turns are never capped; "
                  f"tune $MCT_TURN_IDLE_TIMEOUT){RESET}\n")
        elif rc != 0 and fl["resumed"] and not fl["resp"].exists():
            # stale session id (seat wiped, session pruned): drop the history
            # and replay this turn once on a fresh session — fresh=True means
            # the replay can never loop back here
            print(f"{DIM}✗ resume failed (exit {rc}) — session id is stale; "
                  f"starting a fresh session and replaying as a new turn"
                  f"{RESET}")
            if (err or "").strip():
                print(f"{DIM}{err.strip()[-300:]}{RESET}")
            print()
            with self.lock:
                self.sid = None
            self._save_sid()
            self._write_status()
            return self._start(fl["text"], note="replay, fresh session",
                               fresh=True)
        else:
            # C's view: the cat of both files — they are the turn itself
            _cat_frame("C's prompt", fl["prompt"],
                       fl["prompt"].read_text(encoding="utf-8"))
            if fl["resp"].exists():
                blocks = []
                _md_frame("A's response", fl["resp"],
                          fl["resp"].read_text(encoding="utf-8"), blocks)
                with self.lock:
                    self.blocks = blocks
                tail = f"ack: {ack or '(no chat reply)'} · {dt:.1f}s"
                if blocks:
                    tail += (f" · {len(blocks)} code block"
                             f"{'s' if len(blocks) > 1 else ''} — /copy N")
                print(_rule(tail) + "\n")
            else:
                print(f"{DIM}✗ A wrote no response file (exit {rc}, {dt:.1f}s)."
                      f" chat said: {ack or '(nothing)'}{RESET}")
                if (err or "").strip():
                    print(f"{DIM}{err.strip()[-800:]}{RESET}")
                print()
        self._write_status()
        self._launch_pending()

    # ── queue drain ──────────────────────────────────────────────────────
    def _launch_pending(self):
        with self.lock:
            batch, self.coalesce = self.coalesce, []
            reconcile, self.reconcile = self.reconcile, None
        if batch:
            return self._start(self._digest(batch, reconcile),
                               note=f"digest of {len(batch)}"
                                    + (", reconcile" if reconcile else ""))
        if reconcile:
            # aborted with no follow-up text: hold the brief for whatever
            # C sends next rather than spending a turn on an empty digest
            with self.lock:
                self.reconcile = reconcile
            print(f"{DIM}· reconcile brief for {reconcile} held for your next "
                  f"message{RESET}")
            self._write_status()
        with self.lock:
            nxt = self.appendq.pop(0) if self.appendq else None
        if nxt:
            return self._start(nxt, note="appended")

    def _digest(self, batch, reconcile):
        # deterministic B: verbatim join with explicit framing — the SOP's
        # collation rules 1–3 are inference, rule 4 (mark the digest) is ours
        head = (f"[B digest — deterministic collation of {len(batch)} interim "
                f"message(s) from C, not C's verbatim single message. Later "
                f"messages supersede earlier ones where they conflict.]")
        parts = [head]
        if reconcile:
            parts.append(
                f"Reconcile: {reconcile} was aborted mid-flight at C's "
                f"explicit request ('!'). The workspace may contain partial "
                f"changes toward its instruction (see {reconcile}-prompt.md "
                f"in the exchange dir). Verify workspace state before "
                f"building on it.")
        parts.append("Interim messages, in order:\n" + "\n".join(
            f"{i}. {t}" for i, t in enumerate(batch, 1)))
        return "\n\n".join(parts)

    # ── session (A's chat history) ───────────────────────────────────────
    def _save_sid(self):
        try:
            with self.lock:
                sid = self.sid
            if sid:
                self.sid_path.write_text(json.dumps(
                    {"session_id": sid,
                     "updated": time.strftime("%Y-%m-%dT%H:%M:%S")},
                    indent=1) + "\n", encoding="utf-8")
            elif self.sid_path.exists():
                self.sid_path.unlink()
        except OSError:
            pass

    def reset_session(self) -> str:
        """/new — forget A's chat history; next turn starts a fresh session."""
        with self.lock:
            had, self.sid = self.sid, None
        self._save_sid()
        self._write_status()
        return had or ""

    # ── status (terminal + console) ──────────────────────────────────────
    def in_flight_tag(self):
        with self.lock:
            return self.flight["tag"] if self.flight else None

    def status_line(self) -> str:
        with self.lock:
            fl, sid = self.flight, self.sid
            co, ap, rec = len(self.coalesce), len(self.appendq), self.reconcile
        if fl:
            dt = time.monotonic() - fl["t0"]
            s = f"⏳ {fl['tag']} — A working, {dt:.0f}s"
        else:
            s = "● idle — no turn in flight"
            s += f" · session {sid[:8]}…" if sid else " · fresh session"
        pend = []
        if co:
            pend.append(f"{co} coalesced")
        if ap:
            pend.append(f"{ap} appended")
        if rec:
            pend.append(f"reconcile brief ({rec})")
        return s + (" · pending: " + ", ".join(pend) if pend else "")

    def bar_text(self) -> str:
        """The bottom status bar (tui): spinner + turn + elapsed while A works,
        idle + session otherwise, then pending queries and the key hints."""
        with self.lock:
            fl, sid = self.flight, self.sid
            co, ap, rec = len(self.coalesce), len(self.appendq), self.reconcile
        if fl:
            dt = time.monotonic() - fl["t0"]
            spin = SPIN[int(dt / self.TICK_S) % len(SPIN)]
            s = f"{spin} {fl['tag']} · {_fmt_dt(dt)} · '!' interrupts"
        else:
            s = "● idle" + (f" · session {sid[:8]}…" if sid else " · fresh session")
        pend = []
        if co:
            pend.append(f"{co} coalesced")
        if ap:
            pend.append(f"{ap} appended")
        if rec:
            pend.append(f"reconcile ({rec})")
        if pend:
            s += " · pending: " + ", ".join(pend)
        return s + "   Enter sends · \\+Enter / Alt+Enter newline · /help"

    def print_status(self):
        """/status (or bare Enter while busy): flight + every pending query."""
        print(f"{DIM}{self.status_line()}{RESET}")
        with self.lock:
            co, ap = list(self.coalesce), list(self.appendq)
        for sym, q in (("⇄", co), ("⊕", ap)):
            for t in q:
                print(f"{DIM}   {sym} {t[:90] + ('…' if len(t) > 90 else '')}"
                      f"{RESET}")

    def _write_status(self, state: str = ""):
        """Mirror flight + queue state to <ws>/status.json (atomic rename) so
        the console's 🛡 session tab can show whether A is working and which
        queries are pending (served by GET /api/mct/status)."""
        with self.lock:
            fl = self.flight
            doc = {"backend": "mct", "pid": os.getpid(),
                   "state": state or ("working" if fl else "idle"),
                   "turn": fl["tag"] if fl else None,
                   "since": fl["wall0"] if fl else None,
                   "model": self.model or None,
                   "session_id": self.sid,
                   "pending": {"coalesced": [t[:140] for t in self.coalesce],
                               "appended": [t[:140] for t in self.appendq],
                               "reconcile": self.reconcile},
                   "updated": time.time()}
        tmp = self.status_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self.status_path)
        except OSError:
            pass

    # ── shutdown ─────────────────────────────────────────────────────────
    def busy(self) -> bool:
        with self.lock:
            return bool(self.flight or self.coalesce or self.appendq)

    def drain(self):
        while self.busy():
            time.sleep(0.2)

    def kill(self):
        with self.lock:
            fl = self.flight
        if fl:
            try:
                fl["proc"].kill()
            except OSError:
                pass

    def mark_offline(self):
        self._write_status(state="offline")


def backfill_exchanges(exchange_dir, locus, base=None, token=None):
    """One-shot: ingest existing turn-NNNN-{prompt,response}.md pairs into the
    toolserver's per-locus exchanges DB (idempotent — (locus, turn) upserts).
    Returns {sent, failed, skipped}."""
    import urllib.request as _u
    base = (base or os.environ.get("TOOLSERVER_URL",
                                   "https://toolserver.hugpy.ai")).rstrip("/")
    token = token or (os.environ.get("TOOLSERVER_OPERATOR_TOKEN")
                      or os.environ.get("HUGPY_OPERATOR_TOKEN"))
    ex = Path(exchange_dir).expanduser()
    sent = failed = skipped = 0
    for p in sorted(ex.glob("turn-*-prompt.md")):
        m = re.match(r"turn-(\d+)-prompt\.md$", p.name)
        if not m:
            skipped += 1
            continue
        tag = f"turn-{m.group(1)}"
        resp = ex / f"{tag}-response.md"
        try:
            body = json.dumps({
                "locus": locus, "turn": tag,
                "prompt": p.read_text(encoding="utf-8", errors="replace")[:200000],
                "response": (resp.read_text(encoding="utf-8", errors="replace")[:200000]
                             if resp.exists() else ""),
                "pointers": {"ws": str(ex.parent), "backfill": True},
            }).encode()
            req = _u.Request(base + "/exchange/record", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("X-Operator-Token", token)
            _u.urlopen(req, timeout=15).read()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {tag}: {exc}", file=sys.stderr)
            failed += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _handle(arb: "Arbiter", ws: Path, exchange: Path, line: str) -> bool:
    """One submitted input (possibly multi-line). Returns True to leave."""
    arb.archive.event("operator_input", text=line)
    line = line.strip()
    if not line:
        if arb.busy():
            arb.print_status()    # bare Enter while busy = status
        return False
    if line in ("/quit", "/exit", "/q"):
        arb.kill()
        arb.mark_offline()
        return True
    if line == "/help":
        print(f"{DIM}mid-turn lanes (docs/QUERY-ARBITRATION-SOP.md):\n"
              f"  !msg      interrupt A now; reconcile brief opens next turn\n"
              f"  msg       coalesce into the next slot as a marked digest\n"
              f"  +msg      own turn after current work\n"
              f"  todo: msg capture to <ws>/todo.md, no turn (also ?msg)\n"
              f"idle lines start a turn immediately. While A works the status\n"
              f"bar under the input shows spinner+timer and A's activity\n"
              f"streams above as dim lines (⚒ tool calls · 💬 interim notes ·\n"
              f"✗ tool errors); your typing stays in the box.\n"
              f"input: Enter sends · \\ then Enter, Alt+Enter, Ctrl-J or\n"
              f"Shift+Enter add a line · a pasted block stays one prompt.\n"
              f"A keeps chat history via --resume (session.json).\n"
              f"/status — flight + pending queries (also: bare Enter while"
              f" busy)\n"
              f"/copy [N] — copy code block N of the last response to the\n"
              f"clipboard (OSC 52; default 1)\n"
              f"/new — forget A's chat history; next turn starts fresh\n"
              f"      (also applies an edited operator-guidance.md: the system\n"
              f"      prompt is recorded once per session — `sysprompt-watch`)\n"
              f"/model [id] — show/set A's model · /todo — captured items ·\n"
              f"/ws — paths · /quit — leave{RESET}")
        return False
    if line == "/status":
        arb.print_status()
        return False
    if line.startswith("/copy"):
        v = line[len("/copy"):].strip()
        try:
            idx = int(v) if v else 1
        except ValueError:
            print(f"{DIM}· usage: /copy [N]{RESET}")
            return False
        with arb.lock:
            blocks = list(arb.blocks)
        if not blocks:
            print(f"{DIM}· no code blocks in the last response{RESET}")
        elif not 1 <= idx <= len(blocks):
            print(f"{DIM}· blocks available: 1..{len(blocks)}{RESET}")
        else:
            _clip(blocks[idx - 1])
            print(f"{DIM}✂ block {idx} ({len(blocks[idx - 1])} chars) → "
                  f"clipboard (OSC 52 — needs terminal support){RESET}")
        return False
    if line == "/new":
        had = arb.reset_session()
        print(f"{DIM}session reset — next turn starts A fresh"
              f"{' (was ' + had[:8] + '…)' if had else ' (was already fresh)'}"
              f"{RESET}")
        return False
    if line == "/ws":
        print(f"{DIM}ws: {ws}\nexchange: {exchange}{RESET}")
        return False
    if line == "/todo":
        todo = ws / "todo.md"
        body = todo.read_text(encoding="utf-8").strip() \
            if todo.exists() else "(nothing captured)"
        print(f"{DIM}{body}{RESET}")
        return False
    if line.startswith("/model") and (len(line) == 6 or line[6] == " "):
        v = line[len("/model"):].strip()
        if v:
            arb.model = v
        print(f"{DIM}model: {arb.model or '(Codex default)'}{RESET}")
        return False
    arb.dispatch(line)
    return False


def _exit_on_eof(arb: "Arbiter") -> int:
    if arb.busy():
        print(f"{DIM}· draining in-flight/pending turns before exit "
              f"(Ctrl-C aborts){RESET}")
        try:
            arb.drain()
        except KeyboardInterrupt:
            arb.kill()
    print()
    arb.mark_offline()
    return 0


def _run_plain(arb: "Arbiter", ws: Path, exchange: Path) -> int:
    """The readline line loop (fallback: --plain, no TTY, no prompt_toolkit)."""
    _setup_readline(ws)
    while True:
        tag = arb.in_flight_tag()
        prompt = (f"{BOLD}mct{RESET} {DIM}⏳{tag}{RESET}{BOLD}>{RESET} "
                  if tag else PROMPT)
        try:
            line = input(_rl(prompt))
        except EOFError:
            return _exit_on_eof(arb)
        except KeyboardInterrupt:
            arb.kill()
            print()
            arb.mark_offline()
            return 0
        if _handle(arb, ws, exchange, line):
            return 0


def _ptk_keys():
    """Claude-Code-like keys: Enter sends; backslash before Enter, Alt+Enter, Ctrl-J
    or Shift+Enter insert a newline. Shift+Enter is only distinguishable on
    terminals that send CSI-u / modifyOtherKeys sequences — those are mapped
    onto the Alt+Enter binding."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    try:
        from prompt_toolkit.input import ansi_escape_sequences as _seq
        for esc in ("\x1b[13;2u", "\x1b[27;2;13~"):
            _seq.ANSI_SEQUENCES.setdefault(esc, (Keys.Escape, Keys.ControlM))
    except Exception:
        pass
    kb = KeyBindings()

    @kb.add("enter")
    def _send(event):
        buf = event.current_buffer
        before = buf.document.text_before_cursor
        if before.endswith("\\"):            # `\` + Enter → newline
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
            return
        buf.validate_and_handle()

    @kb.add("escape", "enter")                  # Alt+Enter (and mapped Shift+Enter)
    @kb.add("c-j")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _ctrl_c(event):
        buf = event.current_buffer
        if buf.text:                            # first Ctrl-C clears the box
            buf.reset()
            return
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    return kb


def _run_tui(arb: "Arbiter", ws: Path, exchange: Path, *, input=None,
             output=None) -> int:
    """The Claude-Code-style front end: a prompt box pinned to the bottom of
    the terminal, a status bar under it, output scrolling above (patch_stdout
    routes the worker threads' prints there), multi-line input as one prompt."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout

    arb.tui = True

    def _message():
        tag = arb.in_flight_tag()
        return ANSI(f"{BOLD}mct{RESET} {DIM}⏳{tag}{RESET}{BOLD}>{RESET} "
                    if tag else PROMPT)

    def _toolbar():
        return ANSI(f"{DIM}{arb.bar_text()}{RESET}")

    session = PromptSession(
        message=_message,
        multiline=True,
        key_bindings=_ptk_keys(),
        history=FileHistory(str(ws / "prompt_history")),
        prompt_continuation=lambda width, ln, wrap: ANSI(f"{DIM}{'…':>{max(width - 1, 1)}} {RESET}"),
        bottom_toolbar=_toolbar,
        refresh_interval=arb.TICK_S,
        enable_open_in_editor=False,
        input=input, output=output,
    )
    with patch_stdout(raw=True):
        while True:
            try:
                text = session.prompt()
            except EOFError:
                return _exit_on_eof(arb)
            except KeyboardInterrupt:
                arb.kill()
                print()
                arb.mark_offline()
                return 0
            if _handle(arb, ws, exchange, text):
                return 0


def _default_idle_timeout() -> int:
    """Default IDLE cap in seconds: how long a turn may go with NO subprocess
    output before it's treated as hung and killed. This is silence, not
    wall-clock — a turn that keeps streaming is never capped. Override with
    $MCT_TURN_IDLE_TIMEOUT (seconds). 0 / unset / invalid → 1800s (30 min)."""
    try:
        v = int((os.environ.get("MCT_TURN_IDLE_TIMEOUT") or "").strip() or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 1800


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ws", nargs="?", default=str(_default_ws()),
                    help="workspace dir (default: %(default)s)")
    ap.add_argument("--model", default="", help="A's model id (codex --model)")
    ap.add_argument("--timeout", type=int, default=_default_idle_timeout(),
                    help="IDLE timeout: seconds of NO subprocess output before "
                         "a turn is killed as hung (default $MCT_TURN_IDLE_TIMEOUT "
                         "or 1800). This is silence, not wall-clock — a streaming "
                         "turn is never capped. 0 → use the default.")
    ap.add_argument("--fresh", action="store_true",
                    help="drop the persisted session id: A starts a new Codex "
                         "session with no memory of earlier launches")
    ap.add_argument("--plain", action="store_true",
                    help="readline line loop instead of the bottom prompt box")
    ap.add_argument("--resume", action="store_true",
                    help="continue the workspace session from a previous launch")
    ap.add_argument("--dangerous", "--dangerously-bypass-approvals-and-sandbox",
                    action="store_true",
                    help="bypass Codex approvals and sandboxing (danger-full-access)")
    args = ap.parse_args(argv)
    actions.binary()  # Fail before starting the UI if Codex is unavailable.
    args.fresh = args.fresh or not args.resume
    args.model = args.model or actions.read_json(actions.root() / "config.json").get("default_model") or ""
    # resolve: the pointers A receives must be absolute — A resolves them
    # from its own cwd, not from wherever this REPL was launched
    ws = Path(args.ws).expanduser().resolve()
    exchange = ws / "exchange"
    exchange.mkdir(parents=True, exist_ok=True)
    arb = Arbiter(ws, exchange, args.model,
                  args.timeout or _default_idle_timeout(), fresh=args.fresh,
                  dangerous=args.dangerous)
    if args.fresh:
        hist = ("fresh session — new Codex thread at launch, no memory of earlier "
                "launches" + (f" (dropped {arb.forgot_sid[:8]}…)" if arb.forgot_sid else "")
                + "; A keeps chat context across turns of THIS launch (/new resets)")
    else:
        hist = (('resumes session ' + arb.sid[:8] + '…' if arb.sid else 'fresh session')
                + " — A keeps chat context across turns and relaunches (/new resets)")
    tui = not args.plain and sys.stdin.isatty() and sys.stdout.isatty()
    if tui:
        try:
            import prompt_toolkit  # noqa: F401
        except ImportError:
            tui = False
            print(f"{DIM}· prompt_toolkit not installed — plain line input "
                  f"(pip install prompt_toolkit for the bottom prompt box){RESET}")
    input_help = ("bottom box — Enter sends, +Enter / Alt+Enter / Ctrl-J add a line"
                  if tui else "plain line loop (--plain / no TTY)")
    print(f"{BOLD}mct{RESET} — pointer-exchange test grounds")
    print(f"{DIM}ws: {ws} · exchange: {exchange}\n"
          f"model: {args.model or '(Codex default)'} · "
          f"access: {'DANGEROUS full bypass' if args.dangerous else 'workspace-write'} · A gets file "
          f"POINTERS; this display is the cat of the files.\n"
          f"history: {hist}.\n"
          f"input: {input_help}\n"
          f"mid-turn lanes: !interrupt · coalesce (default) · +append · "
          f"todo:/? capture — docs/QUERY-ARBITRATION-SOP.md · /help{RESET}")
    if tui:
        return _run_tui(arb, ws, exchange)
    return _run_plain(arb, ws, exchange)


if __name__ == "__main__":
    sys.exit(main())
