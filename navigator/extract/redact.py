"""Credential scrubbing. Applied to every string before it reaches a file, the
terminal or an error message. The pipeline never reads, stores or echoes a key on
purpose; this is the net for the accidental case (for example a provider error
message that quotes part of a key)."""
import os
import re

ENV_NAMES = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
MASK = "[REDACTED]"
_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
)


class Redactor(object):
    def __init__(self):
        self.count = 0

    def text(self, s):
        if not isinstance(s, str) or not s:
            return s
        out = s
        for name in ENV_NAMES:
            val = os.environ.get(name)
            if val and len(val) >= 8 and val in out:
                self.count += out.count(val)
                out = out.replace(val, MASK)
        for pat in _PATTERNS:
            out, n = pat.subn(MASK, out)
            self.count += n
        return out

    def obj(self, o):
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, tuple):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {self.text(k) if isinstance(k, str) else k: self.obj(v) for k, v in o.items()}
        return o


DEFAULT = Redactor()


def redact(s):
    return DEFAULT.text(s)
