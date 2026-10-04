#!/usr/bin/env python3
"""Build the hackathon one-page report PDF (submission item 4)."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)

INK = HexColor('#0B0B12')
BLUE = HexColor('#1F5BFF')
VIOLET = HexColor('#7B2CFF')
MUTED = HexColor('#55555F')
YELLOW = HexColor('#8A6B00')

body = ParagraphStyle('body', fontName='Helvetica', fontSize=8.6, leading=11.6,
                      textColor=INK, spaceAfter=3)
bullet = ParagraphStyle('bullet', parent=body, leftIndent=10, bulletIndent=2,
                        spaceAfter=2.2)
h = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=10.2, leading=13,
                   textColor=BLUE, spaceBefore=7, spaceAfter=2.5)
title = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=17,
                       leading=20, textColor=INK)
sub = ParagraphStyle('sub', fontName='Helvetica', fontSize=9, leading=12.5,
                     textColor=MUTED, spaceAfter=2)

doc = SimpleDocTemplate('submission/RentalLawNavigator_OnePager.pdf',
                        pagesize=letter, leftMargin=0.62 * inch,
                        rightMargin=0.62 * inch, topMargin=0.5 * inch,
                        bottomMargin=0.45 * inch,
                        title='Rental Housing Law Navigator, One-Page Report',
                        author='MD S. Rana')
s = []
s.append(Paragraph('Rental Housing Law Navigator', title))
s.append(Spacer(1, 2))
s.append(Paragraph(
    'RealPage x Hack-Nation, Challenge 2 &nbsp;|&nbsp; '
    'Live: <b>navigator.mdsrana.com</b> &nbsp;|&nbsp; '
    'Code: <b>github.com/Sheru01/rental-law-navigator</b>', sub))
s.append(Paragraph(
    '<i>"It tells you what it cannot tell you, and exactly why."</i>', sub))
s.append(HRFlowable(width='100%', thickness=1.4, color=INK, spaceAfter=4))

s.append(Paragraph('1 &nbsp;Challenge Tackled', h))
s.append(Paragraph(
    'Which housing rules apply at a given address, on a given date? Housing law is layered '
    '(state over city), each layer with its own coverage tests and effective dates, and the '
    'public record often lacks the one building fact a rule turns on. Users: housing agencies, '
    'tenant advocates, housing providers, and anyone deploying AI on law. We built an engine '
    'that answers by address and date over a 500-address, three-state sample, and that treats '
    'an honest "unknown" as a first-class result rather than a failure.', body))

s.append(Paragraph('2 &nbsp;Tools / ML Models Used', h))
for t in [
    '<b>LLM extraction pipeline</b> (Python, swappable OpenAI / Anthropic / replay transports): turns corpus legal text into structured rule records with per-field epistemics. Demo data uses replayed extraction fixtures, not a live model run, and the site header says so.',
    '<b>Claude agents</b> (Opus, Sonnet, Fable via Claude Code) built, audited and reviewed pipeline, engine, site and submission; a second blind reader (GPT) cross-checked extractions and disagreements were published, not reconciled.',
    '<b>US Census Geocoder</b> (live, two passes: address batch, then per-row coordinates lookup) resolves each address to its incorporated place, because the batch endpoint does not return it and the mailing city is not the governing jurisdiction.',
    '<b>Deterministic three-valued engine</b> (Python stdlib): coverage, precedence (yields_to / stacks / preempts_pending), as-of dates, withheld-status projection. No LLM decides applicability.',
    '<b>Static site generator + Playwright</b>: one self-contained page from engine output; an 11-check browser smoke suite gates every push; deployed on Vercel.']:
    s.append(Paragraph(t, bullet, bulletText='•'))

s.append(Paragraph('3 &nbsp;What Worked Well', h))
for t in [
    'Live geocoding of all 500 addresses: 479 resolved to an incorporated place, 21 reported unresolved rather than guessed; the project validator passes with 0 warnings.',
    'Span discipline: every shipped rule carries a quoted span that exact-matches its source document after whitespace normalization, re-verified in the browser at render time (7 of 7 pass).',
    'Two kinds of silence kept separate: <b>unknown</b> (a building fact is missing) vs <b>indeterminate</b> (the legal record cannot establish the rule is operative). Aggregated, they form the Evidence Debt Ledger: a sourced map of what the public record cannot answer.',
    'Change tracking as a parameter, not a feature: moving the as-of date flips NJ\'s FAIR Act from not-yet-effective to applies on its derived 2027-07-01 date, shown as a diff.']:
    s.append(Paragraph(t, bullet, bulletText='•'))

s.append(Paragraph('4 &nbsp;What Was Challenging', h))
for t in [
    'Geocoder and engine disagreed on a key name (legal_city vs jurisdiction), so every address read as unresolved the first time real data flowed. Caught by binding real output instead of fixtures; fixed with the full 273-test suite green.',
    'The Census batch endpoint omits the incorporated place entirely; we added a second live call per matched row.',
    'Documents we could not source as legal text (fetch-tool summaries) are hatched, down-weighted, and never quoted as law; two rules (Berkeley ordinance, CA AB 325) ship as withheld because their operative status is not establishable from the corpus.']:
    s.append(Paragraph(t, bullet, bulletText='•'))

s.append(Paragraph('5 &nbsp;How We Spent the Time (solo + AI agents)', h))
s.append(Paragraph(
    'H0-4: corpus survey, frozen contract (ids, coverage schema, precedence, span rules). '
    'H4-10: parallel agent builds (extraction pipeline, geocode module, engine) plus an eight-board demo design under a design freeze: no verdict hard-coded for the mockup. '
    'H10-14: live Census run, read-only audit, engine fix, 273 tests green. '
    'H14-18: site generated from validated output only, deployed to navigator.mdsrana.com. '
    'H18-20: visual redesign, smoke suite, submission package.', body))
s.append(Spacer(1, 2))
s.append(Paragraph(
    '<font color="#7B2CFF"><b>If we had 24 more hours:</b></font> run the live model extraction across all 74 documents '
    'and publish the two-reader disagreement table as a first-class page.', body))
doc.build(s)

from pypdf import PdfReader
r = PdfReader('submission/RentalLawNavigator_OnePager.pdf')
print('pages:', len(r.pages))
assert len(r.pages) == 1, 'MUST BE ONE PAGE'
print('OK: submission/RentalLawNavigator_OnePager.pdf')
