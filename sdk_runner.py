#!/usr/bin/env python3
"""SDK runner: headless Claude Code sessions through the Python Agent SDK.

Separate long-lived process (see docs/sdk-protocol.md). Newline-delimited JSON over a
Unix socket. The API key is handed to the CLI only through an apiKeyHelper command (the `key` subcommand
below); it is never in any environment or options, never logged,
persisted, or returned.

    python sdk_runner.py serve [--socket PATH] [--state PATH]
"""
import argparse
import asyncio
import fnmatch
import ipaddress
import json
import os
import re
import shlex
import socket
import signal
import subprocess
import sys
import time
import urllib.parse
import uuid

VERSION = "1"
MAX_LINE = 1 << 20
# The bot calls the runner with a 10 s timeout: every wait inside one request stays below it,
# otherwise the bot reports "not responding" while the runner still carries the request out.
TASK_WAIT = 8.0       # session.send: waiting for the previous task to finish its cleanup
INTERRUPT_WAIT = 2.0  # session.stop: client.interrupt()
STOP_WAIT = 6.0       # session.stop: waiting for the task to end
DEFAULT_DENY = "The owner declined."
ERR_CODES = ("bad_request", "not_found", "busy", "closed", "no_key", "unavailable", "internal", "too_big")


def log(*parts):
    """Log to stderr. Only names, ids, states, error codes - never bodies, inputs or keys."""
    try:
        print("sdk_runner:", *parts, file=sys.stderr, flush=True)
    except Exception:
        pass


CHAT_URL = re.compile(r"https://([a-z0-9-]+\.)+[a-z]{2,}(:443)?([/?#][^\\\s@]*)?", re.I)


# hidden entries named explicitly: a "/*" glob may skip dotfiles, and these carry settings and hooks
# the only built-in tools of a chat session: a guest's words must not reach other Claude sessions on this machine
# (newer CLIs ship ListAgents/SendMessage, allowed without asking), subagents, schedules or anything added later
CHAT_TOOLS = ["Read", "Grep", "Glob", "Bash", "Edit", "Write", "WebFetch", "WebSearch"]
CHAT_HIDDEN = (".mcp.json", ".claude", ".git", ".envrc")


def chat_sandbox(cwd, write=False):
    """Bash of a chat session runs at once inside the OS sandbox: it reads the project and nothing else in the
    home folder, writes nothing (a change has to leave the sandbox, which is a permission request), and reaches
    only the project site. No sandbox, no session: it never falls back to running unsandboxed."""
    site = _chat_site()  # the project site the sandbox may reach without asking; empty: none. A URL form is cut to its host (N8)
    root = os.path.realpath(cwd)
    hidden = [os.path.join(root, x) for x in CHAT_HIDDEN]
    # the Linux sandbox ignores globs («root/*» let any write through) and the root itself breaks bwrap (it cannot
    # make mount points for its own missing files): every entry is named; a new top-level file is still possible (denyWrite on the root breaks bwrap: the CLI adds its own missing mount points such as .gitconfig, 2.1.322)
    try:
        top = sorted(os.listdir(root))
    except OSError:
        top = []
    # the owner's session writes in the project, but never into git, its own settings or MCP config
    deny = hidden if write else [os.path.join(root, x) for x in top + [x for x in CHAT_HIDDEN if x not in top]]
    # reads: only the clone and the system dirs ordinary commands need; CC_CHAT_READ_ANY=1 restores the old «all but ~/»
    fs = {"denyRead": ["~/"], "allowRead": [root]} if os.environ.get("CC_CHAT_READ_ANY", "").strip() == "1" else \
        {"denyRead": ["/"], "allowRead": [root, "/usr", "/bin", "/lib", "/lib64", "/etc/ssl", "/etc/alternatives"]}
    return {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": True,
            "failIfUnavailable": True,
            "filesystem": {**fs, "denyWrite": deny},
            "network": {"allowedDomains": [site, "*." + site] if site else []}}


def public_host(host):
    """A name on the open internet: every address it resolves to is global (no loopback, LAN, tailnet, link-local)."""
    host = str(host or "").lower().rstrip(".")
    if "." not in host or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".ts.net")):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # a bare IP literal is never «a site»: the owner decides
    except ValueError:
        pass
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        return False
    # checked once here; the fetch resolves again (DNS rebinding). Chat auto-allow does not trust a bare name: chat_net_ok.
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def _chat_site():
    """CC_CHAT_SITE as a hostname (optional leading «*.»). Empty: the chat has no site."""
    site = os.environ.get("CC_CHAT_SITE", "").strip().lower().rstrip(".")
    if "://" in site:
        try:
            site = (urllib.parse.urlsplit(site).hostname or "").lower().rstrip(".")
        except ValueError:
            return ""
    return site[2:] if site.startswith("*.") else site


def chat_net_ok(host):
    """Whether a chat session may open this host without asking.
    A name outside CC_CHAT_SITE waits for the owner: the address checked here is not the one the fetch will use.
    CC_CHAT_WEB_ANY=1 restores the old rule (any name whose addresses are all global)."""
    if os.environ.get("CC_CHAT_WEB_ANY", "").strip() == "1":
        return public_host(host)
    site = _chat_site()
    host = str(host or "").lower().rstrip(".")
    if not site or (host != site and not host.endswith("." + site)):
        return False
    return public_host(host)


def chat_free(tool, inp):
    """A chat session may run this without the owner (reading the web needs no confirmation): web search,
    any public https page, sandbox network to a public host. Addresses of this machine and its networks wait for the owner."""
    if tool == "WebSearch":
        return True
    if tool == "Bash" and isinstance(inp, dict) and inp.get("dangerouslyDisableSandbox") in (None, False):  # «true» as a string is not «in the sandbox» (N10)
        # runs inside the OS sandbox whatever it is (the CLI still asks for «$(…)» and the like): nothing to confirm
        return True
    if tool == "SandboxNetworkAccess" and isinstance(inp, dict):
        return chat_net_ok(inp.get("host"))
    if tool != "WebFetch" or not isinstance(inp, dict):
        return False
    url = str(inp.get("url") or "")
    if not CHAT_URL.fullmatch(url):  # strict text first: parsers disagree on «\», «@», %-escapes in the host
        return False
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and u.port in (None, 443) and chat_net_ok(u.hostname)

# ---- the owner's permission rules ------------------------------------------------------------------------
# perms.json (Claude Code rule syntax): {"sessions": {"mode", "allow", "deny"}, "chat": {...}}; read on every decision,
# so a change applies at once. Anything unreadable or odd means «ask the owner», never «allow».
PERM_MODES = ("ask", "edits", "all")
PERM_RULE = re.compile(r"([A-Za-z][\w-]*)(?:\((.+)\))?\Z", re.S)
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
READ_TOOLS = ("Read", "Grep", "Glob")
BASH_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||&|\n)\s*")
BASH_SUBCMD = ("git", "npm", "pnpm", "yarn", "docker", "kubectl", "gh", "cargo", "go", "pip", "uv", "systemctl",
               "brew", "apt", "make", "terraform", "helm", "bun", "deno", "poetry", "dotnet")


def perm_load(path, chat):
    """The section of a policy file for this kind of session -> dict or None."""
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            pol = json.load(f)
        pol = pol.get("chat" if chat else "sessions")
    except (OSError, ValueError, AttributeError):
        return None
    return pol if isinstance(pol, dict) else None


def perm_cli_deny(path, chat):
    """The owner's deny rules handed to the CLI itself: it reads inside cwd and runs read-only commands without asking
    can_use_tool, so a «never» there only holds if the CLI knows it.
    -> list, or None when the file is set but cannot be read (not the same as «no rules»)."""
    if not isinstance(path, str) or not path:
        return []
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None  # unreadable is not «no rules»: the caller must not start a client without them
    if not isinstance(raw, dict):
        return None
    pol = raw.get("chat" if chat else "sessions")
    if not isinstance(pol, dict):
        pol = {}
    deny = pol.get("deny") if isinstance(pol.get("deny"), list) else []
    out = []
    for r in deny:
        parsed = perm_rule_parse(r)
        if not parsed:
            continue
        spec = parsed[1]
        if parsed[0] in ("Read", "Edit") + tuple(EDIT_TOOLS) + tuple(READ_TOOLS) and spec and spec.startswith("/") \
                and not spec.startswith("//"):
            r = "%s(/%s)" % (parsed[0], spec)  # the runner's absolute path is «//» for the CLI («/» = project root there)
        out.append(r)
    return out


def perm_rule_parse(rule):
    m = PERM_RULE.match(rule) if isinstance(rule, str) else None
    return (m.group(1), m.group(2)) if m else None


def bash_segments(cmd, strict=True):
    """A command split at ; && || | & and newlines. strict: None when it substitutes or redirects (never auto)."""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if strict:
        bare = re.sub(r"\s*(?:2>&1|[12]?>\s*/dev/null)", "", cmd)
        if any(x in bare for x in ("$(", "`", "<(", ">(", ">", "<")):
            return None
        cmd = bare
    segs = [x.strip() for x in BASH_SPLIT.split(cmd) if x.strip()]
    return segs or None


def _bash_rule(spec, seg):
    if spec is None:
        return True
    if spec.endswith(":*"):
        pre = spec[:-2].strip()
        return seg == pre or seg.startswith(pre + " ")
    return seg == spec.strip()


# deny matching only. Allow rules keep the raw segment, so a policy with no deny decides as before.
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAP = ("env", "command", "sudo", "time", "nohup", "timeout", "nice", "ionice", "setsid", "stdbuf", "exec", "doas", "busybox")
_SHELLS = ("bash", "sh", "dash", "zsh", "ksh", "mksh", "fish", "ash")
_KEYWORDS = ("!", "if", "then", "do", "else", "elif", "while", "until")  # «if true; then rm x; fi»: the command follows
_GIT_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
            "--config-env", "--list-cmds", "--attr-source"}
_XARGS_ARG = {"-a", "-E", "-e", "-I", "-i", "-L", "-l", "-n", "-P", "-s", "-d", "--arg-file", "--eof",
              "--replace", "--max-lines", "--max-args", "--max-procs", "--max-chars", "--delimiter"}
_WRAP_ARG = {
    "env": {"-u", "--unset", "-C", "--chdir", "--argv0", "--block-signal", "--default-signal", "--ignore-signal"},
    "sudo": {"-u", "-g", "-U", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-R", "--user", "--group",
             "--close-from", "--prompt", "--role", "--type", "--other-user", "--command-timeout", "--chroot"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
}


def _sh_words(seg):
    try:
        return shlex.split(seg, posix=True)
    except ValueError:
        return None


def _take_flag(words, i, arg_opts):
    """Index just after a flag at i, or None when a separate argument is missing."""
    tok = words[i]
    base, eq, _ = tok.partition("=")
    if base in arg_opts and not eq and (tok.startswith("--") or len(tok) == 2):
        if i + 1 >= len(words):
            return None
        return i + 2
    return i + 1


def _strip_group(seg):
    """Drop a leading subshell or brace group. Leave «$(…)» alone: that segment is opaque."""
    if "$(" in seg or "`" in seg:
        return seg.strip()
    seg = seg.strip()
    while seg:
        nxt = seg[1:].lstrip() if seg[0] in "({" else seg
        if nxt and nxt[-1] in "});":
            nxt = nxt[:-1].rstrip()
        if nxt == seg:
            return seg
        seg = nxt
    return seg


def _sudo_shell(words):
    """sudo -s / -i starts a shell; the command inside it is not the sudo token."""
    for w in words[1:]:
        if w == "--" or not w.startswith("-"):
            break
        if w.startswith("--"):
            if w in ("--shell", "--login"):
                return True
            continue
        if "s" in w[1:] or "i" in w[1:]:
            return True
    return False


def _xargs_runs(words):
    """True when xargs has a command (not the default echo)."""
    i = 1
    while i < len(words):
        tok = words[i]
        if tok == "--":
            return i + 1 < len(words)
        if not tok.startswith("-"):
            return True
        nxt = _take_flag(words, i, _XARGS_ARG)
        if nxt is None:
            return True
        i = nxt
    return False


def _shell_c(args):
    for w in args:
        if w == "--":
            break
        if w == "-c" or (w.startswith("-") and not w.startswith("--") and "c" in w[1:]):
            return True
    return False


def _drop_wrap(name, words):
    """Drop one env/command/sudo/time/nohup and its flags. -> (rest, opaque)."""
    arg = _WRAP_ARG.get(name, ())
    i = 1
    while i < len(words):
        tok = words[i]
        if tok == "--":
            return words[i + 1:], False
        if name == "env" and _ASSIGN.match(tok):
            i += 1
            continue
        if not tok.startswith("-"):
            break
        base = tok.split("=", 1)[0]
        if name == "env" and base in ("-S", "--split-string"):
            return words, True  # env -S runs a shell string
        nxt = _take_flag(words, i, arg)
        if nxt is None:
            return words, True
        i = nxt
    if name == "timeout":
        i += 1  # its duration comes before the command
    return words[i:], False


def _git_global(words):
    """Drop git's global options so «git -C dir push» is «git push». -> (words, opaque)."""
    i = 1
    while i < len(words):
        tok = words[i]
        if tok == "--":
            return ["git"] + words[i + 1:], False
        if not tok.startswith("-"):
            break
        nxt = _take_flag(words, i, _GIT_ARG)
        if nxt is None:
            return words, True
        i = nxt
    return ["git"] + words[i:], False


def _unwrap(words):
    """-> (command words, opaque). Assignments, wrappers and git globals are gone; the command is a basename."""
    opaque = False
    for _ in range(16):
        while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS):
            words = words[1:]
        if not words:
            return [], opaque
        name = os.path.basename(words[0])
        if name in _SHELLS:  # «sh -c '…'», «… | sh», «sh script»: what it runs is not on this line
            return [name] + words[1:], True
        if name == "find" and any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in words[1:]):
            return [name] + words[1:], True
        if name == "eval":
            return [name] + words[1:], True
        if name == "xargs":
            return [name] + words[1:], _xargs_runs(words)
        if name not in _WRAP:
            words = [name] + words[1:]
            if name == "git":
                words, bad = _git_global(words)
                opaque = opaque or bad
            return words, opaque
        if name == "sudo" and _sudo_shell(words):
            return [name] + words[1:], True
        nxt, bad = _drop_wrap(name, words)
        if bad:
            return [name] + words[1:], True
        words = nxt
    return words, True


def bash_deny_view(cmd):
    """(segments a Bash deny rule can see, opaque).
    Opaque: a substitution, bash -c / sh -c / eval, or xargs with a command. The caller must not mode:all that line
    while any Bash deny rule exists — the hidden command was not read. With no deny rule this view is unused."""
    if not isinstance(cmd, str) or not cmd.strip():
        return [], False
    # single-quoted text and quoted-heredoc bodies are literal: a «$(» there runs nothing
    lit = re.sub(r"<<-?\s*(['\"])(\w+)\1.*?\n\s*\2(?=\n|$)", "", cmd, flags=re.S)
    lit = re.sub(r"'[^']*'", "''", lit)
    opaque = any(x in lit for x in ("$(", "`", "<(", ">("))
    out = []
    for seg in bash_segments(cmd, strict=False) or []:
        raw = " ".join(seg.split())
        if raw:
            out.append(raw)
        body = _strip_group(seg)
        if not body:
            continue
        words = _sh_words(body)
        if words is None:
            continue  # a fragment of a quoted «;» or a heredoc line: the raw segment above was checked
        words, op = _unwrap(words)
        opaque = opaque or op
        if words:
            joined = " ".join(words)
            if joined != raw:
                out.append(joined)
    return out, opaque


def _tool_family(rule_tool, tool):
    if rule_tool == "Edit":
        return tool in EDIT_TOOLS
    if rule_tool == "Read":
        return tool in READ_TOOLS
    return rule_tool == tool


def _inside(real, root):
    try:
        return os.path.commonpath([real, root]) == root
    except ValueError:
        return False


def _edits_hidden(real, cwd, chat):
    """mode:edits must ask before writing git, Claude's settings, or the MCP and envrc files.
    A chat session also stays out of every CHAT_HIDDEN name. real is a realpath, so «..» and symlinks count."""
    root = os.path.realpath(cwd)
    try:
        rel = os.path.relpath(real, root)
    except ValueError:
        return True
    if rel == ".." or rel.startswith(".." + os.sep):
        return True
    parts = rel.split(os.sep)  # every level: «sub/.claude/settings.json» runs hooks in a session opened in sub (N3)
    if any(x in (".git", ".claude") for x in parts) or parts[-1] in (".mcp.json", ".envrc"):
        return True
    # a chat also keeps off CLAUDE.md: its text goes into the owner's own sessions in this clone
    return bool(chat and (any(x in CHAT_HIDDEN for x in parts) or parts[-1] == "CLAUDE.md"))


def _target(inp, cwd):
    p = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or cwd
    return os.path.realpath(os.path.join(cwd, p)) if isinstance(p, str) else None


def _glob(path, spec):
    """fnmatch with Claude Code's «**/»: zero or more folders, so **/x also matches x itself and a/**/x matches a/x."""
    if fnmatch.fnmatchcase(path, spec):
        return True
    alt = spec[3:] if spec.startswith("**/") else spec
    while "/**/" in alt:
        alt = alt.replace("/**/", "/", 1)
        if fnmatch.fnmatchcase(path, alt):
            return True
    return alt != spec and fnmatch.fnmatchcase(path, alt)


def _rule_hit(rule, tool, inp, cwd):
    """Non-Bash rule against a tool call."""
    parsed = perm_rule_parse(rule)
    if not parsed or not _tool_family(parsed[0], tool):
        return False
    spec = parsed[1]
    if spec is None:
        return True
    if tool == "WebFetch" and spec.startswith("domain:"):
        try:
            host = (urllib.parse.urlsplit(str(inp.get("url") or "")).hostname or "").lower()
        except ValueError:
            return False
        dom = spec[7:].strip().lower()
        return bool(dom) and (host == dom or host.endswith("." + dom))
    if tool in EDIT_TOOLS + READ_TOOLS:
        real, root = _target(inp, cwd), os.path.realpath(cwd)
        if real is None:
            return False
        if spec.startswith("/"):
            return _glob(real, spec.replace("//", "/", 1) if spec.startswith("//") else spec)
        return _inside(real, root) and _glob(os.path.relpath(real, root), spec)
    return False


def perm_decide(pol, tool, inp, cwd, chat):
    """-> ("allow" | "deny" | None, the rule or mode that decided). None: ask the owner."""
    try:
        if not isinstance(pol, dict) or tool == "AskUserQuestion" or not isinstance(inp, dict):
            return None, ""
        allow, deny, mode = pol.get("allow", []), pol.get("deny", []), pol.get("mode", "ask")
        if not isinstance(allow, list) or not isinstance(deny, list) or mode not in PERM_MODES:
            return None, ""
        allow = [r for r in allow if perm_rule_parse(r)]
        deny = [r for r in deny if perm_rule_parse(r)]
        bash_opaque = False
        if tool == "Bash":
            seen, bash_opaque = bash_deny_view(inp.get("command"))
            for r in deny:
                t, spec = perm_rule_parse(r)
                if t == "Bash" and any(_bash_rule(spec, seg) for seg in seen):
                    return "deny", r
        else:
            for r in deny:
                if _rule_hit(r, tool, inp, cwd):
                    return "deny", r
        if chat and ((tool == "Bash" and inp.get("dangerouslyDisableSandbox") not in (None, False))
                     or (tool == "SandboxNetworkAccess" and not chat_net_ok(inp.get("host")))):
            return None, ""  # past the sandbox, or a name outside the site: the owner decides
        if tool == "Bash":
            if "Bash" in allow:
                return "allow", "Bash"
            segs = bash_segments(inp.get("command"))
            rules = [perm_rule_parse(r)[1] for r in allow if perm_rule_parse(r)[0] == "Bash"]
            if segs and all(any(_bash_rule(spec, seg) for spec in rules) for seg in segs):
                return "allow", ", ".join(r for r in allow if perm_rule_parse(r)[0] == "Bash"
                                          and any(_bash_rule(perm_rule_parse(r)[1], g) for g in segs))
        else:
            for r in allow:
                if _rule_hit(r, tool, inp, cwd):
                    return "allow", r
        if mode == "all" and not chat:
            # an unparsed command plus any Bash deny: do not pretend the line was read
            if not (tool == "Bash" and bash_opaque and any(perm_rule_parse(r)[0] == "Bash" for r in deny)):
                return "allow", "mode:all"
        if mode in ("edits", "all") and tool in EDIT_TOOLS:
            real = _target(inp, cwd)
            if real and _inside(real, os.path.realpath(cwd)) and not _edits_hidden(real, cwd, chat):
                return "allow", "mode:edits"
    except Exception:  # a rule must never turn into an allow by crashing
        return None, ""
    return None, ""


def perm_what(tool, inp):
    """A short line about the call for the console: the command, the path or the address."""
    if not isinstance(inp, dict):
        return ""
    v = inp.get("command") or inp.get("file_path") or inp.get("notebook_path") or inp.get("url") or inp.get("path") or ""
    return str(v).replace("\n", " ")[:160]


def perm_suggest(tool, inp, cwd):
    """The rule behind «always, everywhere» for this call, shown on the button. None: no sensible rule."""
    if tool == "AskUserQuestion" or not isinstance(inp, dict):
        return None
    if tool == "Bash":
        segs = bash_segments(inp.get("command"))
        if not segs or len(segs) != 1:
            return None
        words = segs[0].split()
        if not words or "=" in words[0]:
            return None
        two = len(words) > 1 and words[0] in BASH_SUBCMD and re.fullmatch(r"[a-z][a-z0-9-]*", words[1])
        return "Bash(%s:*)" % " ".join(words[:2] if two else words[:1])
    if tool in EDIT_TOOLS:
        return "Edit(**)"
    if tool in READ_TOOLS:
        return "Read(**)"
    if tool == "WebFetch":
        try:
            host = urllib.parse.urlsplit(str(inp.get("url") or "")).hostname
        except ValueError:
            return None
        return "WebFetch(domain:%s)" % host if host else None
    return tool if PERM_RULE.match(tool) else None


# secrets that may sit inside the folder of a read-only session
# defence in depth: the folder itself should hold no secrets (a clean clone); Read rules also cover Grep/Glob
CHAT_DENY = ("Read(**/.env*)", "Read(**/*.pem)", "Read(**/*.key)", "Read(**/*.p12)", "Read(**/id_*)",
                 "Read(**/.netrc)", "Read(**/.npmrc)", "Read(**/*.token)", "Read(**/*credentials*)", "Read(**/secrets/**)", "Read(**/.git/config)")


def load_sdk():
    """Lazy import so the module (and the tests) work without the SDK installed."""
    import claude_agent_sdk
    return claude_agent_sdk


def scrub_env():
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "TG_", "ZIDOS_PAGER_", "CC_TGBOT_")):  # CC_TGBOT_ is the legacy prefix
            del os.environ[k]


def read_key(path, var):
    """Return the value of NAME=value (quotes allowed) from the key file, or None."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                name, sep, val = line.partition("=")
                if not sep or name.strip() != var:
                    continue
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1]
                return val or None
    except OSError:
        return None
    return None


class RunnerError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---- killing the CLI child ------------------------------------------------------------------------------

def _ps(*args):
    """stdout of ps (macOS has no /proc); "" when ps fails."""
    try:
        return subprocess.run(("ps",) + args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _children_map():
    kids = {}
    if not os.path.exists("/proc/self"):
        for line in _ps("-A", "-o", "pid=,ppid=,lstart=").splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                kids.setdefault(int(parts[1]), []).append(int(parts[0]))
        return kids
    try:
        names = os.listdir("/proc")
    except OSError:
        return kids
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % n) as f:
                data = f.read()
            ppid = int(data.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(n))
    return kids


def _starttime(pid):
    if not os.path.exists("/proc/self"):
        return _ps("-p", str(pid), "-o", "lstart=").strip() or None  # a string: only compared for equality
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def snapshot_tree(pid):
    """[(pid, starttime)] of a process and all its descendants, the root first."""
    if not pid or pid <= 1 or pid == os.getpid():
        return []
    kids = _children_map()
    order, stack = [], [pid]
    while stack:
        p = stack.pop()
        st = _starttime(p)
        if st is not None:
            order.append((p, st))
        stack.extend(kids.get(p, []))
    return order


def kill_snapshot(snap):
    """SIGKILL (children first) those of the snapshot that are still the same process."""
    for p, st in reversed(snap):
        if _starttime(p) != st:
            continue  # gone, or the PID was reused
        try:
            os.kill(p, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def kill_tree(pid):
    """SIGKILL a process and all its descendants (children first). Safe on dead pids."""
    kill_snapshot(snapshot_tree(pid))


def client_pid(client):
    """PID of the CLI child of an SDK client (transport keeps an anyio Process)."""
    try:
        return int(client._transport._process.pid)
    except Exception:
        return None


def is_our_cli(pid):
    """A leftover CLI of an earlier runner: claude in cmdline and started by the SDK (entrypoint sdk-py)."""
    if not os.path.exists("/proc/self"):
        # macOS: another process's environment is not readable, so the entrypoint marker cannot be checked;
        # decide by the command line only (weaker than on Linux).
        return b"claude" in _ps("-p", str(pid), "-o", "command=").encode()
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmd = f.read()
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read()
    except OSError:
        return False
    return b"claude" in cmd and b"CLAUDE_CODE_ENTRYPOINT=sdk-py" in env


# ---- session --------------------------------------------------------------------------------------------

class Session:
    PUBLIC = ("id", "session_id", "cwd", "name", "model", "state", "detail", "budget_usd",
              "cost_usd", "created", "updated", "always", "chat", "write", "perms", "auto")

    def __init__(self, **kw):
        self.id = kw["id"]
        self.session_id = kw["session_id"]
        self.cwd = kw["cwd"]
        self.name = kw.get("name", "")
        self.model = kw.get("model", "")
        self.state = kw.get("state", "working")
        self.detail = kw.get("detail", "")
        self.budget_usd = kw.get("budget_usd", 1.0)
        self.cost_usd = kw.get("cost_usd", 0.0)
        self.created = kw.get("created", time.time())
        self.updated = kw.get("updated", self.created)
        self.cli_pid = kw.get("cli_pid")
        self.always = [x for x in kw.get("always", []) if isinstance(x, str)]
        self.chat = kw.get("chat") is True
        self.write = kw.get("write") is True  # a chat session whose sandbox may write in the project (the owner's)
        self.perms = kw.get("perms") if isinstance(kw.get("perms"), str) else None  # the owner's policy file
        self.cli_deny = None  # the deny rules the running CLI was started with (not saved: set at each client start)
        self.auto = [x for x in kw.get("auto", []) if isinstance(x, dict)][-20:]  # what the rules decided, newest last
        self.pending = {}      # req -> dict(request fields, future)
        self.client = None
        self.task = None
        self.inbox = None
        self.interrupting = False
        self.epoch = 0

    def to_dict(self):
        d = {k: getattr(self, k) for k in self.PUBLIC}
        d["pending"] = [
            {k: v for k, v in p.items() if not k.startswith("_")} for p in self.pending.values()]
        return d

    def to_state(self):
        d = {k: getattr(self, k) for k in self.PUBLIC}
        d["cli_pid"] = self.cli_pid
        d["cli_st"] = _starttime(self.cli_pid) if self.cli_pid else None  # tells a reused PID from our CLI
        return d


class Runner:
    def __init__(self, socket_path=None, state_path=None):
        scrub_env()
        env = os.environ
        self.socket_path = os.path.expanduser(socket_path or env.get("CC_SDK_SOCKET")
                                              or "~/.local/state/zidos-pager/sdk.sock")
        self.state_path = os.path.expanduser(state_path or env.get("CC_SDK_STATE")
                                             or "~/.local/state/zidos-pager/sdk.json")
        self.key_file = env.get("CC_SDK_KEY_FILE") or "~/.config/zidos-pager/anthropic-api.env"
        self.key_var = env.get("CC_SDK_KEY_VAR") or "ANTHROPIC_API_KEY"
        # login: no key at all, the Claude Code CLI uses its own sign-in on this machine (claude auth login)
        self.login = (env.get("CC_SDK_AUTH") or "").strip().lower() == "login"
        self.model = env.get("CC_SDK_MODEL") or "sonnet"
        # an Anthropic-compatible endpoint (DeepSeek and the like); empty: Anthropic itself, behaviour unchanged
        self.base_url = env.get("CC_SDK_BASE_URL") or ""
        self.strong_model = env.get("CC_SDK_STRONG_MODEL") or self.model
        try:
            self.max_turns = int(env.get("CC_SDK_MAX_TURNS") or 30)
        except ValueError:
            self.max_turns = 30
        # the CLI prices unknown models like Claude, so a third-party endpoint has no USD limit unless CC_SDK_BUDGET is set
        default = None if self.base_url or self.login else 1.0  # a subscription is not billed per dollar
        try:
            self.budget = float(env["CC_SDK_BUDGET"]) if env.get("CC_SDK_BUDGET") else default
        except ValueError:
            self.budget = default
        if self.budget is not None and self.budget <= 0:  # CC_SDK_BUDGET=0: sessions run without a spending limit
            self.budget = None
        self.sessions = {}
        self.subscribers = set()
        self.server = None
        self._load_state()

    # -- persistence
    def _ensure_dir(self, path):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, mode=0o700, exist_ok=True)

    def _save(self):
        try:
            self._ensure_dir(self.state_path)
            tmp = self.state_path + ".tmp"
            data = json.dumps({"sessions": [s.to_state() for s in self.sessions.values()]},
                              ensure_ascii=False)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_path)
        except OSError as e:
            log("state save failed", type(e).__name__)

    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        changed = False
        for d in raw.get("sessions", []) if isinstance(raw, dict) else []:
            try:
                s = Session(**{k: v for k, v in d.items()
                               if k in Session.PUBLIC or k == "cli_pid"})
            except (KeyError, TypeError):
                continue
            if s.state in ("working", "blocked"):
                s.state, s.detail, s.updated = "interrupted", "runner restarted", time.time()
                changed = True
            if s.cli_pid:
                if is_our_cli(s.cli_pid) and d.get("cli_st") in (None, _starttime(s.cli_pid)):  # None: older state
                    kill_tree(s.cli_pid)
                    log("killed leftover cli of", s.id)
                s.cli_pid = None  # no CLI of ours is alive after a restart
                changed = True
            self.sessions[s.id] = s
        if changed:
            self._save()

    # -- events
    def _emit(self, event, data):
        line = (json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n").encode()
        for w in list(self.subscribers):
            try:
                if w.is_closing():
                    raise ConnectionError
                w.write(line)
            except Exception:
                self.subscribers.discard(w)

    def _changed(self, s):
        s.updated = time.time()
        self._save()
        self._emit("session.state", {"session": s.to_dict()})

    def _set_state(self, s, state, detail=""):
        s.state, s.detail = state, detail
        log("state", s.id, state)
        self._changed(s)

    def _task_set(self, s, epoch, state, detail=""):
        """State change from a session task; ignored when the task is stale (stopped/replaced)."""
        if s.epoch == epoch:
            self._set_state(s, state, detail)

    # -- key
    def key_value(self):
        if self.login:
            return "login"  # not a key: the CLI signs in by itself; checked by `claude auth status` when chosen
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.key_var or ""):
            return None
        return read_key(self.key_file, self.key_var)

    # -- methods
    def _get(self, params):
        sid = params.get("id")
        s = self.sessions.get(sid) if isinstance(sid, str) else None
        if s is None:
            raise RunnerError("not_found", "no such session")
        return s

    async def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise RunnerError("bad_request", "params must be an object")
        fn = getattr(self, "m_" + method.replace(".", "_"), None) if isinstance(method, str) else None
        if fn is None:
            raise RunnerError("bad_request", "unknown method")
        return await fn(params)

    async def m_runner_info(self, p):
        try:
            ver = getattr(load_sdk(), "__version__", "unknown")
        except Exception:
            ver = "unavailable"
        return {"version": VERSION, "sdk_version": ver, "key": self.key_value() is not None}

    async def m_session_list(self, p):
        return {"sessions": [s.to_dict() for s in self.sessions.values()]}

    async def m_session_get(self, p):
        return {"session": self._get(p).to_dict()}

    async def m_session_start(self, p):
        cwd, prompt = p.get("cwd"), p.get("prompt")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            raise RunnerError("bad_request", "cwd must be an existing directory")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RunnerError("bad_request", "prompt required")
        budget = self._budget_arg(p.get("budget_usd"), self.budget)
        perms = p.get("perms")
        if perms is not None and not (isinstance(perms, str) and os.path.isabs(perms)):
            raise RunnerError("bad_request", "perms must be an absolute path")
        model = p.get("model") or self.model
        if not isinstance(model, str):
            raise RunnerError("bad_request", "model must be a string")
        if self.key_value() is None:
            raise RunnerError("no_key", "API key is not available")
        try:
            load_sdk()
        except Exception:
            raise RunnerError("unavailable", "claude-agent-sdk is not installed")
        chat = p.get("chat") is True
        if isinstance(perms, str) and perm_cli_deny(perms, chat) is None:
            log("perms unreadable")
            raise RunnerError("unavailable", "permission rules are unreadable")
        s = Session(id="s-" + uuid.uuid4().hex[:8], session_id=str(uuid.uuid4()),
                    cwd=os.path.abspath(cwd), name=prompt.strip().splitlines()[0][:60],
                    model=model, state="working", budget_usd=budget, chat=chat,
                    write=chat and p.get("write") is True,
                    perms=perms)
        self.sessions[s.id] = s
        self._save()
        self._emit("session.created", {"session": s.to_dict()})
        self._spawn(s, prompt, resume=False)
        return {"session": s.to_dict()}

    @staticmethod
    def _budget_arg(v, default):
        if v is None:
            return default
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise RunnerError("bad_request", "budget_usd must be a positive number")
        return float(v)

    async def m_session_send(self, p):
        s = self._get(p)
        text = p.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RunnerError("bad_request", "text required")
        if s.state in ("working", "blocked"):
            raise RunnerError("busy", "a turn is in flight")
        model = p.get("model")
        if model is not None and not isinstance(model, str):
            raise RunnerError("bad_request", "model must be a string")
        perms = p.get("perms")  # sessions started before the console passed a policy pick it up with the next message
        if perms is not None and not (isinstance(perms, str) and os.path.isabs(perms)):
            raise RunnerError("bad_request", "perms must be an absolute path")
        if perms and perms != s.perms:
            s.perms = perms
            self._save()
        live_idle = (s.state == "idle" and s.task is not None and not s.task.done()
                     and s.inbox is not None)
        fresh = perm_cli_deny(s.perms, s.chat)
        if live_idle and fresh is not None and fresh != s.cli_deny:
            # the CLI takes deny rules only at its start: end this client, the turn below resumes the conversation
            s.inbox.put_nowait(None)
            live_idle = False
        if fresh is None and not live_idle:
            # a new client would start with an empty deny list: refuse instead of dropping «never»
            log("perms unreadable", s.id)
            raise RunnerError("unavailable", "permission rules are unreadable")
        if not live_idle and s.task is not None and not s.task.done():
            # the previous task is still in its cleanup: wait, or it would clobber the new one
            try:
                await asyncio.wait_for(asyncio.shield(s.task), TASK_WAIT)
            except asyncio.TimeoutError:
                raise RunnerError("busy", "previous task is still shutting down")
            except Exception:
                pass
            if s.state in ("working", "blocked"):
                raise RunnerError("busy", "a turn is in flight")
        if model is not None:  # this turn and the next ones; "" = back to the default
            s.model = model or self.model
        if s.state == "idle" and s.task is not None and not s.task.done() and s.inbox is not None:
            s.interrupting = False
            self._set_state(s, "working")
            s.inbox.put_nowait(text)
        else:  # budget / error / interrupted (or an idle session whose task is gone)
            if self.key_value() is None:
                raise RunnerError("no_key", "API key is not available")
            self._set_state(s, "working")
            self._spawn(s, text, resume=True)
        return {"session": s.to_dict()}

    async def m_session_answer(self, p):
        s = self._get(p)
        req, decision = p.get("req"), p.get("decision")
        if decision not in ("allow", "always", "deny"):
            raise RunnerError("bad_request", "decision must be allow, always or deny")
        r = s.pending.get(req) if isinstance(req, str) else None
        if r is None:
            return {"ok": True, "already": True}
        sdk = load_sdk()
        if decision == "deny":
            msg = p.get("message")
            result = sdk.PermissionResultDeny(
                message=msg if isinstance(msg, str) and msg else DEFAULT_DENY)
        else:
            kw = {}
            if r["kind"] == "ask":
                ans = p.get("answers")
                if not isinstance(ans, dict) or not ans:
                    raise RunnerError("bad_request", "answers required for a question")
                kw["updated_input"] = {**r["input"], "answers": ans}
            if decision == "always":
                if not r["can_always"]:
                    raise RunnerError("bad_request", "always is not available for this request")
                if r["tool"] not in s.always:
                    s.always.append(r["tool"])
            result = sdk.PermissionResultAllow(**kw)
        self._resolve(s, req, result, decision)
        return {"ok": True}

    def _resolve(self, s, req, result, decision):
        r = s.pending.pop(req, None)
        if r is None:
            return
        fut = r["_future"]
        if not fut.done():
            fut.set_result(result)
        self._emit("request.closed", {"id": s.id, "req": req, "decision": decision})
        if not s.pending and s.state == "blocked":
            self._set_state(s, "working")
        else:
            self._changed(s)

    def _resolve_all_interrupt(self, s):
        if not s.pending:
            return
        sdk = load_sdk()
        for req in list(s.pending):
            self._resolve(s, req, sdk.PermissionResultDeny(message="Interrupted", interrupt=True),
                          "interrupt")

    async def m_session_interrupt(self, p):
        s = self._get(p)
        if s.state in ("working", "blocked"):
            s.interrupting = True
            self._resolve_all_interrupt(s)
            if s.client is not None:
                try:
                    await s.client.interrupt()
                except Exception as e:
                    log("interrupt failed", s.id, type(e).__name__)
        return {"ok": True}

    async def m_session_stop(self, p):
        s = self._get(p)
        await self._stop(s)
        return {"ok": True}

    async def _stop(self, s):
        if s.state == "stopped":
            return
        try:
            self._resolve_all_interrupt(s)
        except Exception:
            pass
        client, task, pid = s.client, s.task, s.cli_pid
        snap = snapshot_tree(pid or client_pid(client))
        s.epoch += 1  # the dying task may not change the state anymore
        self._set_state(s, "stopped")
        s.cli_pid = None
        self._save()
        if s.inbox is not None:
            s.inbox.put_nowait(None)
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), INTERRUPT_WAIT)
            except Exception:
                pass
        kill_snapshot(snap)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), STOP_WAIT)
            except BaseException:
                pass

    async def m_session_budget(self, p):
        s = self._get(p)
        if p.get("budget_usd") is None:
            raise RunnerError("bad_request", "budget_usd required")
        s.budget_usd = self._budget_arg(p.get("budget_usd"), None)
        self._changed(s)
        return {"session": s.to_dict()}

    # -- session task
    def _spawn(self, s, text, resume):
        s.inbox = asyncio.Queue()
        s.interrupting = False
        s.epoch += 1
        s.task = asyncio.get_running_loop().create_task(self._run(s, text, resume, s.epoch))

    def _make_can_use_tool(self, s):
        sdk = load_sdk()

        async def can_use_tool(tool_name, tool_input, ctx):
            verdict, why = perm_decide(perm_load(s.perms, s.chat), tool_name, tool_input, s.cwd, s.chat)
            if verdict == "deny":  # the owner's «never» wins over everything, chat freedoms included
                self._auto(s, tool_name, tool_input, "deny", why)
                return sdk.PermissionResultDeny(message="Denied by the owner's rule %s." % why)
            if s.chat and chat_free(tool_name, tool_input):
                return sdk.PermissionResultAllow()
            if s.chat and tool_name == "AskUserQuestion":
                return sdk.PermissionResultDeny(message="Nobody answers questions here: ask in your reply instead.")
            if tool_name != "AskUserQuestion" and tool_name in s.always and not s.chat:
                log("auto-allow", s.id, tool_name)
                return sdk.PermissionResultAllow()
            if verdict == "allow":
                self._auto(s, tool_name, tool_input, "allow", why)
                return sdk.PermissionResultAllow()
            req = "r-" + uuid.uuid4().hex[:8]
            fut = asyncio.get_running_loop().create_future()
            r = {"req": req, "kind": "ask" if tool_name == "AskUserQuestion" else "perm",
                 "tool": tool_name, "tool_use_id": getattr(ctx, "tool_use_id", None),
                 "input": tool_input, "title": getattr(ctx, "title", None),
                 "can_always": tool_name != "AskUserQuestion" and not s.chat,  # chat: the owner decides each
                 "created": time.time(), "_future": fut}
            s.pending[req] = r
            log("request", s.id, req, tool_name)
            if s.state in ("working", "blocked"):
                self._set_state(s, "blocked")
            self._emit("request.opened",
                       {"id": s.id, "request": {k: v for k, v in r.items() if not k.startswith("_")}})
            try:
                return await fut
            except asyncio.CancelledError:
                s.pending.pop(req, None)
                raise
            finally:
                s.pending.pop(req, None)

        return can_use_tool

    def _auto(self, s, tool, inp, verdict, why):
        """A decision taken by the owner's rules: kept on the session (shown in the console) and logged."""
        log("rule", s.id, verdict, tool, why)
        s.auto = (s.auto + [{"tool": tool, "what": perm_what(tool, inp), "verdict": verdict, "rule": why,
                              "ts": time.time()}])[-20:]
        self._changed(s)

    def _helper_settings(self, deny=None, sandbox=None):
        cmd = shlex.join([sys.executable, os.path.abspath(__file__), "key", "--file",
                          os.path.expanduser(self.key_file), "--var", self.key_var])
        st = {} if self.login else {"apiKeyHelper": cmd}
        if deny:
            st["permissions"] = {"deny": list(deny)}
        if sandbox:
            st["sandbox"] = sandbox
        return json.dumps(st)

    def _options(self, s, resume):
        sdk = load_sdk()
        fresh = perm_cli_deny(s.perms, s.chat)
        if fresh is None:
            log("perms unreadable", s.id)
            raise RunnerError("unavailable", "permission rules are unreadable")
        s.cli_deny = fresh
        kw = dict(cwd=s.cwd, model=s.model, permission_mode="default",
                  setting_sources=["project"], max_budget_usd=s.budget_usd,
                  can_use_tool=self._make_can_use_tool(s), settings=self._helper_settings(s.cli_deny or None))
        if self.base_url and not self.login:  # the key stays in apiKeyHelper only: Bash tools see this env
            kw.pop("max_budget_usd")
            kw.update(max_turns=self.max_turns, env={
                "ANTHROPIC_BASE_URL": self.base_url, "ANTHROPIC_DEFAULT_OPUS_MODEL": self.strong_model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": s.model, "ANTHROPIC_DEFAULT_HAIKU_MODEL": s.model,
                "ANTHROPIC_SMALL_FAST_MODEL": s.model, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        if s.chat:  # no project settings or MCP; secrets are denied by the CLI itself, not by the prompt
            kw.update(setting_sources=[], strict_mcp_config=True, tools=list(CHAT_TOOLS),
                      settings=self._helper_settings(list(CHAT_DENY) + s.cli_deny, chat_sandbox(s.cwd, s.write)))
        kw["resume" if resume else "session_id"] = s.session_id
        return sdk.ClaudeAgentOptions(**kw)

    async def _run(self, s, text, resume, epoch):
        sdk = load_sdk()
        client = None
        try:
            if self.key_value() is None:
                self._task_set(s, epoch, "error", "API key is not available")
                return
            client = sdk.ClaudeSDKClient(self._options(s, resume))
            s.client = client
            await client.connect()
            s.cli_pid = client_pid(client)
            self._save()
            if s.interrupting and s.epoch == epoch:
                # interrupted while the CLI was starting: do not run the turn
                s.interrupting = False
                self._task_set(s, epoch, "idle")
                text = await s.inbox.get()
            model = s.model
            while text is not None:
                if s.model != model:  # session.send asked for another model: the same conversation goes on
                    await client.set_model(s.model)
                    model = s.model
                await client.query(text)
                outcome = await self._drain(s, client, epoch)
                if outcome != "idle":
                    break
                text = await s.inbox.get()
        except asyncio.CancelledError:
            if s.epoch == epoch:
                log("task cancelled", s.id)
            raise
        except Exception as e:
            log("session failed", s.id, type(e).__name__)
            self._task_set(s, epoch, "error", "session failed: " + type(e).__name__)
        finally:
            await self._cleanup(s, client)

    async def _cleanup(self, s, client):
        pid = s.cli_pid or (client_pid(client) if client else None)
        snap = snapshot_tree(pid)  # before disconnect: afterwards the descendants are orphans
        if client is not None:
            try:
                await asyncio.shield(asyncio.wait_for(client.disconnect(), 10))
            except BaseException as e:
                log("disconnect failed", s.id, type(e).__name__)
        kill_snapshot(snap)
        if s.client is client:  # a newer task may already own the session
            s.client = None
            s.cli_pid = None
            for req in list(s.pending):
                r = s.pending.pop(req)
                if not r["_future"].done():
                    r["_future"].cancel()
        self._save()

    async def _drain(self, s, client, epoch):
        """Read the stream to ResultMessage. Returns idle / budget / error."""
        sdk = load_sdk()
        got_result = False
        async for m in client.receive_response():
            if isinstance(m, sdk.SystemMessage):
                if getattr(m, "subtype", None) == "init":
                    src = (getattr(m, "data", None) or {}).get("apiKeySource")
                    if src != ("none" if self.login else "apiKeyHelper"):  # login: the subscription, never a stray key
                        log("auth check failed", s.id, str(src))
                        self._task_set(s, epoch, "error", "not authenticated with the API key")
                        return "error"
            elif isinstance(m, sdk.AssistantMessage):
                pass
            elif isinstance(m, sdk.ResultMessage):
                got_result = True
                cost = getattr(m, "total_cost_usd", None)
                # third-party endpoint: the CLI estimate uses Claude prices, so it stays 0
                if isinstance(cost, (int, float)) and not self.base_url:
                    s.cost_usd = float(cost)
                sub = getattr(m, "subtype", "") or ""
                is_err = bool(getattr(m, "is_error", False))
                log("result", s.id, sub, is_err)
                ev = {"id": s.id, "subtype": sub, "cost_usd": s.cost_usd, "is_error": is_err}
                if s.chat and isinstance(getattr(m, "result", None), str):
                    ev["result"] = m.result[:4000]  # a chat session answers someone outside the console
                self._emit("turn.result", ev)
                if sub == "error_max_budget_usd":
                    self._task_set(s, epoch, "budget", "budget of %.2f USD exhausted" % (s.budget_usd or 0))
                    return "budget"
                if s.interrupting:
                    s.interrupting = False
                    self._task_set(s, epoch, "idle")
                    return "idle"
                if is_err or sub != "success":
                    self._task_set(s, epoch, "error", sub or "error")
                    return "error"
                self._task_set(s, epoch, "idle")
                return "idle"
        if not got_result:
            self._task_set(s, epoch, "error", "stream ended without a result")
        return "error"

    # -- server
    async def _handle(self, reader, writer):
        sub = False
        try:
            try:
                line = await reader.readline()
            except (ValueError, asyncio.LimitOverrunError):
                await self._send(writer, {"id": None, "error": {"code": "too_big", "message": "line too long"}})
                return
            if not line:
                return
            rid = None
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
                rid = req.get("id")
            except ValueError:
                await self._send(writer, {"id": None, "error": {"code": "bad_request", "message": "invalid JSON"}})
                return
            method = req.get("method")
            if method == "events.subscribe":
                await self._send(writer, {"id": rid, "result": {"subscribed": True}})
                self.subscribers.add(writer)
                sub = True
                try:
                    while await reader.read(65536):  # discard input until the peer closes
                        pass
                finally:
                    self.subscribers.discard(writer)
                return
            try:
                res = await self.dispatch(method, req.get("params") or {})
                out = {"id": rid, "result": res}
            except RunnerError as e:
                out = {"id": rid, "error": {"code": e.code, "message": e.message}}
            except Exception as e:
                log("internal error", str(method), type(e).__name__)
                out = {"id": rid, "error": {"code": "internal", "message": type(e).__name__}}
            await self._send(writer, out)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _send(writer, obj):
        writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await writer.drain()

    async def start(self):
        self._ensure_dir(self.socket_path)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        old = os.umask(0o177)
        try:
            self.server = await asyncio.start_unix_server(self._handle, path=self.socket_path,
                                                          limit=MAX_LINE)
        finally:
            os.umask(old)
        os.chmod(self.socket_path, 0o600)

    async def close(self):
        if self.server is not None:
            self.server.close()
        for s in list(self.sessions.values()):
            kill_snapshot(snapshot_tree(s.cli_pid or (client_pid(s.client) if s.client else None)))
        tasks = [s.task for s in self.sessions.values() if s.task and not s.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for w in list(self.subscribers):
            try:
                w.close()
            except Exception:
                pass
        self.subscribers.clear()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass


async def serve(socket_path=None, state_path=None):
    runner = Runner(socket_path, state_path)
    await runner.start()
    log("listening")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sdk_runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    kp = sub.add_parser("key")
    kp.add_argument("--file", required=True)
    kp.add_argument("--var", required=True)
    sp = sub.add_parser("serve")
    sp.add_argument("--socket")
    sp.add_argument("--state")
    a = ap.parse_args(argv)
    if a.cmd == "key":  # apiKeyHelper: print the key, nothing else, no logging
        val = read_key(a.file, a.var) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", a.var) else None
        if not val:
            sys.exit(1)
        sys.stdout.write(val)
        sys.stdout.flush()
        return
    scrub_env()
    asyncio.run(serve(a.socket, a.state))


if __name__ == "__main__":
    main()
