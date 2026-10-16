"""Affichage et saisie dans le terminal (messages en français)."""

import shutil
import sys

YES = {"o", "oui", "y", "yes"}


def out(msg=""):
    print(msg)


def err(msg):
    print("Erreur : " + msg, file=sys.stderr)


def warn(msg):
    print("⚠ " + msg)


def interactive():
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_yes(question, default=False):
    """Question oui/non. Hors terminal interactif, renvoie la valeur par défaut."""
    if not interactive():
        return default
    suffix = " [O/n] " if default else " [o/N] "
    try:
        r = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not r:
        return default
    return r in YES


def ask(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def human_size(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            if unit == "o":
                return "{} o".format(int(n))
            return "{:.1f} {}".format(n, unit).replace(".", ",")
        n /= 1024.0
    return str(n)


def fmt_dt(dt):
    if dt is None:
        return "-"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def clip(s, n):
    s = (s or "").replace("\n", " ")
    if n <= 1:
        return s[:n]
    return s if len(s) <= n else s[: n - 1] + "…"


def clip_left(s, n):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else "…" + s[len(s) - n + 1:]


def table(headers, rows, flex_col=None, left_clip=()):
    """Tableau texte aligné ; les colonnes `flex_col` sont tronquées pour tenir dans le terminal
    (par la gauche pour celles de `left_clip`, utile pour des chemins)."""
    width = shutil.get_terminal_size((120, 20)).columns
    cols = len(headers)
    w = [len(h) for h in headers]
    for r in rows:
        for i in range(cols):
            w[i] = max(w[i], len(str(r[i])))
    flex = [] if flex_col is None else (flex_col if isinstance(flex_col, (list, tuple)) else [flex_col])
    excess = sum(w) + 2 * (cols - 1) - width
    for i in flex:  # on rogne la première colonne souple, puis la suivante
        if excess <= 0:
            break
        cut = min(excess, max(0, w[i] - 16))
        w[i] -= cut
        excess -= cut
    lines = ["  ".join(clip(h, w[i]).ljust(w[i]) for i, h in enumerate(headers)).rstrip()]
    for r in rows:
        lines.append("  ".join((clip_left if i in left_clip else clip)(str(r[i]), w[i]).ljust(w[i])
                               for i in range(cols)).rstrip())
    return "\n".join(lines)
