"""Load corpus documents and decide which text is quotable.

Two kinds of files:
  corpus/text/*.txt     supplied plain-text copies, header then body
  corpus/fetched/*.txt  WebFetch captures. A line reading exactly
                        [SUMMARY, NOT SOURCE TEXT] marks a paraphrase. There is no
                        end marker, so everything from that line to the end of the
                        file is treated as summary. That is deliberately strict: the
                        quotation marks inside those regions were produced by a
                        model-mediated extractor and are not byte-verified.

Quotable text is the body before the first marker. The SOURCE:/RETRIEVED: header
lines are never quotable. Provenance hash: sha256 of the file AS DISTRIBUTED, stored
under text_file_sha256_local. The manifest sha256 is carried only as an unverified
value with scope "unknown" (see navigator/out/provenance_hash_finding.json).
"""
import csv
import hashlib
import re
from pathlib import Path

SUMMARY_MARKER = "[SUMMARY, NOT SOURCE TEXT]"
_HEADER_RE = re.compile(r"^(SOURCE|RETRIEVED):\s*(.*)$")
SUBDIRS = ("text", "fetched")


def collapse(s):
    """Collapse every whitespace run (including newlines and NBSP) to one space."""
    return " ".join(str(s).split())


class Document(object):
    def __init__(self, doc_id, rel_path, raw_bytes):
        self.doc_id = doc_id
        self.rel_path = rel_path
        self.text_file_sha256_local = hashlib.sha256(raw_bytes).hexdigest()
        self.raw_text = raw_bytes.decode("utf-8", errors="replace")
        self.url = None
        self.retrieved_at = None
        header, quotable, summary = [], [], []
        lines = self.raw_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _HEADER_RE.match(line.strip())
            if m:
                header.append(line)
                if m.group(1) == "SOURCE":
                    self.url = m.group(2).strip() or None
                else:
                    self.retrieved_at = m.group(2).strip() or None
                i += 1
            elif not line.strip():
                i += 1
            else:
                break
        in_summary = False
        for line in lines[i:]:
            if line.strip() == SUMMARY_MARKER:
                in_summary = True
                continue
            (summary if in_summary else quotable).append(line)
        self.header_norm = collapse("\n".join(header))
        self.quotable_norm = collapse("\n".join(quotable))
        self.summary_norm = collapse("\n".join(summary))
        self.has_summary_region = in_summary
        self.manifest_sha256 = None
        self.manifest_url = None

    @property
    def has_quotable_text(self):
        return bool(self.quotable_norm)

    @property
    def source_doc_id(self):
        """doc_id from corpus_manifest.csv, or None for supplementary captures."""
        return self.doc_id if re.fullmatch(r"D\d{3}", self.doc_id) else None


def load_manifest(corpus_root):
    path = Path(corpus_root) / "corpus_manifest.csv"
    out = {}
    if not path.is_file():
        return out
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            did = (row.get("doc_id") or "").strip()
            if did:
                out[did] = {"sha256": (row.get("sha256") or "").strip() or None,
                            "url": (row.get("url") or "").strip() or None}
    return out


def list_documents(corpus_root, only=None):
    """Documents in corpus/text then corpus/fetched, each sorted by file name.

    only: optional iterable of doc ids (file stems). Unknown ids raise ValueError so a
    typo cannot silently shrink a run.
    """
    root = Path(corpus_root)
    manifest = load_manifest(root)
    docs = []
    for sub in SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.txt")):
            rel = "%s/%s/%s" % (root.name, sub, p.name)
            doc = Document(p.stem, rel, p.read_bytes())
            m = manifest.get(p.stem)
            if m:
                doc.manifest_sha256 = m["sha256"]
                doc.manifest_url = m["url"]
            docs.append(doc)
    if only:
        want = [x.strip() for x in only if x.strip()]
        have = {d.doc_id for d in docs}
        missing = [w for w in want if w not in have]
        if missing:
            raise ValueError("unknown document id(s): %s" % ", ".join(missing))
        docs = [d for d in docs if d.doc_id in set(want)]
    return docs
