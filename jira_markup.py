import re

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"
DIM = "\x1b[2m"

NAMED_COLORS = {
    "red": (255, 86, 86),
    "blue": (76, 154, 255),
    "green": (87, 204, 130),
    "yellow": (230, 200, 60),
    "orange": (240, 150, 60),
    "purple": (170, 110, 230),
    "grey": (150, 150, 150),
    "gray": (150, 150, 150),
    "white": (255, 255, 255),
    "black": (60, 60, 60),
}

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Builders: generan marcado wiki de Jira para usar en --set / --file / comment
# ---------------------------------------------------------------------------

def heading(text, level=1):
    level = min(max(int(level), 1), 6)
    return f"h{level}. {text}"


def bold(text):
    return f"*{text}*"


def italic(text):
    return f"_{text}_"


def underline(text):
    return f"+{text}+"


def strikethrough(text):
    return f"-{text}-"


def monospace(text):
    return f"{{{{{text}}}}}"


def color(text, color_value):
    return f"{{color:{color_value}}}{text}{{color}}"


def link(text, url):
    if text:
        return f"[{text}|{url}]"
    return f"[{url}]"


def code_block(text, language=None):
    if language:
        return f"{{code:{language}}}\n{text}\n{{code}}"
    return f"{{noformat}}\n{text}\n{{noformat}}"


def quote_block(text):
    return f"{{quote}}\n{text}\n{{quote}}"


def panel(text, title=None, bg_color=None, border_color=None, title_bg_color=None):
    params = []
    if title:
        params.append(f"title={title}")
    if bg_color:
        params.append(f"bgColor={bg_color}")
    if border_color:
        params.append(f"borderColor={border_color}")
    if title_bg_color:
        params.append(f"titleBGColor={title_bg_color}")
    header = "{panel" + (":" + "|".join(params) if params else "") + "}"
    return f"{header}\n{text}\n{{panel}}"


def expand(text, title=None):
    header = f"{{expand:title={title}}}" if title else "{expand}"
    return f"{header}\n{text}\n{{expand}}"


def divider():
    return "----"


def bullet_list(items):
    return "\n".join(f"* {item}" for item in items)


def numbered_list(items):
    return "\n".join(f"# {item}" for item in items)


def table(headers, rows):
    lines = ["||" + "||".join(headers) + "||"]
    for row in rows:
        lines.append("|" + "|".join(row) + "|")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Renderer: convierte el marcado wiki que devuelve Jira a texto legible
# en terminal (para el comando `get`)
# ---------------------------------------------------------------------------

def _strip_ansi(s):
    return ANSI_RE.sub("", s)


def _to_ansi_fg(color_value):
    value = color_value.strip()
    rgb = None
    if value.startswith("#"):
        hexv = value.lstrip("#")
        if len(hexv) == 3:
            hexv = "".join(c * 2 for c in hexv)
        if len(hexv) == 6:
            try:
                rgb = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                rgb = None
    else:
        rgb = NAMED_COLORS.get(value.lower())

    if not rgb:
        return ""
    r, g, b = rgb
    return f"\x1b[38;2;{r};{g};{b}m"


def _apply_basic_inline(s):
    # Los enlaces se procesan antes de negrita/cursiva/etc. porque los códigos
    # ANSI que estos insertan contienen literalmente "[" y confundirían al
    # regex de enlaces si se ejecutara después.
    s = re.sub(r"\[([^\|\]]+)\|([^\]]+)\]", lambda m: f"{UNDERLINE}{m.group(1)}{RESET} ({m.group(2)})", s)
    s = re.sub(r"\[([^\]\|]+)\]", lambda m: f"{UNDERLINE}{m.group(1)}{RESET}", s)
    s = re.sub(r"\{\{(.+?)\}\}", lambda m: f"{DIM}{m.group(1)}{RESET}", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"{BOLD}{m.group(1)}{RESET}", s)
    s = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", lambda m: f"{ITALIC}{m.group(1)}{RESET}", s)
    s = re.sub(r"(?<!\+)\+([^+\n]+)\+(?!\+)", lambda m: f"{UNDERLINE}{m.group(1)}{RESET}", s)
    return s


def _inline_format(s):
    placeholders = {}

    def color_repl(m):
        fg = _to_ansi_fg(m.group(1))
        inner = _apply_basic_inline(m.group(2))
        result = f"{fg}{inner}{RESET}" if fg else inner
        token = f"\x00{len(placeholders)}\x00"
        placeholders[token] = result
        return token

    s = re.sub(r"\{color:([^}]+)\}(.*?)\{color\}", color_repl, s, flags=re.DOTALL)
    s = _apply_basic_inline(s)
    for token, value in placeholders.items():
        s = s.replace(token, value)
    return s


def _render_table(table_lines):
    rows = []
    for raw in table_lines:
        stripped = raw.strip()
        is_header = stripped.startswith("||")
        sep = "||" if is_header else "|"
        content = stripped.strip("|")
        cells = [c.strip() for c in content.split(sep)]
        rows.append((is_header, cells))

    ncols = max(len(cells) for _, cells in rows)
    widths = [0] * ncols
    for _, cells in rows:
        for idx in range(ncols):
            value = cells[idx] if idx < len(cells) else ""
            widths[idx] = max(widths[idx], len(_strip_ansi(_inline_format(value))))

    out = []
    for is_header, cells in rows:
        padded = []
        for idx in range(ncols):
            raw_value = cells[idx] if idx < len(cells) else ""
            formatted = _inline_format(raw_value)
            pad = widths[idx] - len(_strip_ansi(formatted))
            padded.append(formatted + " " * max(pad, 0))
        row_str = " | ".join(padded)
        out.append(row_str)
        if is_header:
            out.append("-" * len(_strip_ansi(row_str)))
    return out


def render_for_terminal(text):
    if not text:
        return "-"

    lines = text.splitlines()
    out = []
    i = 0
    n = len(lines)

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        code_open_match = re.match(r"^\{code(:([a-zA-Z0-9+#]*))?\}", stripped)
        is_noformat = stripped.startswith("{noformat}")
        if is_noformat or code_open_match:
            if is_noformat:
                open_tag, close_tag, lang = "{noformat}", "{noformat}", None
            else:
                open_tag = code_open_match.group(0)
                close_tag = "{code}"
                lang = code_open_match.group(2) or None

            title = f" código ({lang}) " if lang else " código "
            out.append(f"{DIM}┌{title}{'─' * max(2, 30 - len(title))}{RESET}")

            remainder = stripped[len(open_tag):]
            if close_tag in remainder:
                out.append(f"{DIM}│{RESET} {remainder[:remainder.index(close_tag)]}")
            else:
                block = []
                i += 1
                while i < n and lines[i].strip() != close_tag:
                    block.append(lines[i])
                    i += 1
                for bl in block:
                    out.append(f"{DIM}│{RESET} {bl}")

            out.append(f"{DIM}└{'─' * 30}{RESET}")
            i += 1
            continue

        if stripped.startswith("{panel"):
            params = dict(re.findall(r"(\w+)=([^|}]+)", stripped))
            title = params.get("title", "Panel")
            block = []
            i += 1
            while i < n and lines[i].strip() != "{panel}":
                block.append(lines[i])
                i += 1
            i += 1
            out.append(f"{BOLD}┌─ {title} {'─' * max(2, 28 - len(title))}{RESET}")
            for bl in block:
                out.append(f"{BOLD}│{RESET} {_inline_format(bl)}" if bl.strip() else "")
            out.append(f"{BOLD}└{'─' * 30}{RESET}")
            continue

        if stripped == "{quote}":
            block = []
            i += 1
            while i < n and lines[i].strip() != "{quote}":
                block.append(lines[i])
                i += 1
            i += 1
            for bl in block:
                out.append(f"{DIM}┃{RESET} {_inline_format(bl)}")
            continue

        if stripped.startswith("{expand"):
            params = dict(re.findall(r"(\w+)=([^|}]+)", stripped))
            title = params.get("title", "Ver más")
            block = []
            i += 1
            while i < n and lines[i].strip() != "{expand}":
                block.append(lines[i])
                i += 1
            i += 1
            out.append(f"{BOLD}▸ {title}{RESET}")
            for bl in block:
                out.append(f"    {_inline_format(bl)}" if bl.strip() else "")
            continue

        if stripped == "----":
            out.append("─" * 50)
            i += 1
            continue

        if stripped.startswith("||") or stripped.startswith("|"):
            table_lines = []
            while i < n and (lines[i].strip().startswith("||") or lines[i].strip().startswith("|")):
                table_lines.append(lines[i])
                i += 1
            out.extend(_render_table(table_lines))
            continue

        heading_match = re.match(r"^h([1-6])\.\s*(.*)$", stripped)
        if heading_match:
            level = int(heading_match.group(1))
            htext = heading_match.group(2)
            style = f"{BOLD}{UNDERLINE}" if level <= 2 else BOLD
            out.append(f"{style}{htext}{RESET}")
            i += 1
            continue

        bullet_match = re.match(r"^(\*+)\s+(.*)$", stripped)
        if bullet_match:
            depth = len(bullet_match.group(1))
            out.append("  " * depth + "• " + _inline_format(bullet_match.group(2)))
            i += 1
            continue

        number_match = re.match(r"^(#+)\s+(.*)$", stripped)
        if number_match:
            depth = len(number_match.group(1))
            out.append("  " * depth + "# " + _inline_format(number_match.group(2)))
            i += 1
            continue

        out.append(_inline_format(raw))
        i += 1

    return "\n".join(out)
