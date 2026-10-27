"""Constants shared by abstract_gpt modules."""

import re

# mct/mct_repl.py
CYAN, DIM, BOLD, RESET = "\033[36m", "\033[2m", "\033[1m", "\033[0m"
WHITE = "\033[97m"
PROMPT = f"{BOLD}mct>{RESET} "
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FENCE_RE = re.compile(r"^\s*```\s*(\S*)\s*$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
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

# integrations/app_server.py
ELICITATION = "mcpServer/elicitation/request"   # codex's approval ask before an MCP tool call
OUTPUT_CAP = 64 * 1024   # chars of one command's output the console stores (a stray `rg .` streamed 10 MB)

# integrations/comms.py
HOOK_MARKER = "abstract-gpt-comms"

# processes/rollover.py
MODES = ("auto", "manual", "off")
