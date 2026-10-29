
"""Pricing Copilot - financial PDF reports.

Generate a report only from authenticated, authorized SQL records. Never accept
client-supplied financial totals, arbitrary SQL, or an output filesystem path.
The API endpoint invoking this function must authenticate and authorize too.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, Flowable,
)
from backend import data_engine as de

NAVY = colors.HexColor('#17253c')
BLUE = colors.HexColor('#2463a6')
MUTED = colors.HexColor('#5c697a')
PALE = colors.HexColor('#eff4fa')
RED = colors.HexColor('#ad3444')
MAX_REPORT_RECORDS = 10000


class ReportGenerationError(ValueError):
    """Invalid or unsupported report request."""


def _dec(value):
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ReportGenerationError('Invalid financial amount in the data.') from exc
    if not result.is_finite():
        raise ReportGenerationError('Non-finite financial amount in the data.')
    return result


def _money(value, symbol='INR '):
    return 'N/A' if value is None else f'{symbol}{value:,.2f}'


def _date_str(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value or '')[:10]


def _para(text, style):
    return Paragraph(escape(str(text)), style)


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name='ReportTitleCustom', fontName='Helvetica-Bold',
                         fontSize=19, leading=24, textColor=NAVY, spaceAfter=10))
    s.add(ParagraphStyle(name='ReportSectionCustom', fontName='Helvetica-Bold',
                         fontSize=11, leading=15, textColor=NAVY,
                         spaceBefore=14, spaceAfter=7))
    s.add(ParagraphStyle(name='ReportBodyCustom', fontName='Helvetica',
                         fontSize=9, leading=14, textColor=NAVY, spaceAfter=7))
    s.add(ParagraphStyle(name='ReportSmallCustom', fontName='Helvetica',
                         fontSize=7.5, leading=11, textColor=MUTED))
    return s


def _table(data, widths=None):
    t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('LEADING', (0, 0), (-1, -1), 11),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, PALE]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, -1), (-1, -1), 0.4, colors.HexColor('#dce3ed')),
    ]))
    return t


class HorizontalBars(Flowable):
    """Compact reportlab-native horizontal bar chart, no external images."""
    def __init__(self, points, width=480):
        super().__init__()
        self.points = points[:8]
        self.width = width
        self.height = 28 * len(self.points) + 8

    def draw(self):
        c = self.canv
        max_val = max((abs(float(v)) for _, v in self.points), default=0) or 1
        for i, (label, value) in enumerate(self.points):
            y = self.height - 24 - i * 28
            c.setFillColor(NAVY)
            c.setFont('Helvetica', 7.5)
            c.drawString(0, y + 11, str(label)[:40])
            c.setFillColor(PALE)
            c.roundRect(0, y - 2, self.width - 115, 9, 3, fill=1, stroke=0)
            c.setFillColor(BLUE if value >= 0 else RED)
            c.roundRect(0, y - 2, (self.width - 115) * abs(float(value)) / max_val,
                        9, 3, fill=1, stroke=0)
            c.setFillColor(NAVY)
            c.drawRightString(self.width - 4, y + 1, f'{value:,.0f}')


def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor('#dce3ed'))
    canvas.line(18 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 11 * mm, 'Pricing Copilot | Financial report')
    canvas.drawRightString(A4[0] - 18 * mm, 11 * mm, f'Page {doc.page}')
    canvas.restoreState()


def generate_financial_pdf(user, dataset='payroll', filters=None, title=None):
    """Return a PDF as bytes. Requires Admin; no raw rows leave the server.

    The caller must be an authenticated backend user object. Filters use the
    existing chatbot/de validation path. Raises PermissionError/ValueError on
    denied access, bad filters, mixed currencies, or incomplete numeric data.
    """
    if not isinstance(user, dict) or user.get('role') != 'Admin':
        raise PermissionError('Only administrators can generate SQL financial reports.')
    if dataset not in de.DATASETS:
        raise ReportGenerationError('Unsupported financial dataset.')
    problem = de.dataset_access_problem(user, dataset)
    if problem:
        raise PermissionError(problem)

    from backend import chatbot  # lazy to avoid circular imports
    cfg = de.DATASETS[dataset]
    incoming = chatbot._clean_filters(filters or {})
    access_problem = chatbot._filter_access_problem(dataset, incoming, user)
    if access_problem:
        raise PermissionError(access_problem)
    clean, warnings, errors = de.validate_filters(
        dataset, incoming, de.allowed_departments(user)
    )
    if errors:
        raise ReportGenerationError(' '.join(errors))

    # Enforce a bounded result set. Fetch one extra row to detect truncation.
    rows = de.search_records(
        dataset, filters=clean, allowed_departments=de.allowed_departments(user),
        limit=MAX_REPORT_RECORDS + 1
    )
    if len(rows) > MAX_REPORT_RECORDS:
        raise ReportGenerationError('Report exceeds the safe record limit; narrow the date range.')
    if not rows:
        raise ReportGenerationError('No records match the selected filters.')

    main, budget_col = cfg['amount'], cfg['budget']
    primary = [r for r in rows if de.row_kind(dataset, r) == cfg['primary_kind']]
    if not primary:
        raise ReportGenerationError('No relevant financial records match this selection.')
    if any(r.get(main) in (None, '') or r.get(budget_col) in (None, '') for r in primary):
        raise ReportGenerationError('Missing financial amounts or budgets; resolve data quality issues before export.')
    currencies = sorted({str(r.get('Currency', '')).strip().upper() for r in rows})
    if len(currencies) != 1 or not currencies[0]:
        raise ReportGenerationError('A report requires one known currency; apply a currency filter first.')
    # Do not silently label non-INR values with the rupee symbol.
    currency = currencies[0] + ' '
    actual = de.calculate_total(primary, main)
    budget = de.calculate_total(primary, budget_col)
    variance = de._variance(cfg, budget, actual)
    variance_pct = (variance / abs(budget) * 100) if budget else None
    dates = [_date_str(r.get(cfg['date'])) for r in rows if r.get(cfg['date'])]
    if not dates:
        raise ReportGenerationError('The selected records do not contain usable dates.')

    grouped = de.group_summary(dataset, rows, cfg['default_group'])[:8]
    points = []
    for r in grouped:
        amount = r.get(de.primary_column(dataset))
        number = _dec(amount)
        if number is not None:
            points.append((str(r.get(cfg['default_group'], 'Unknown')), number))

    s = _styles()
    story = []
    report_title = str(title or f'{cfg["label"]} Financial Report')[:110]
    story.append(_para(report_title, s['ReportTitleCustom']))
    story.append(_para(
        f'Period: {min(dates)} to {max(dates)} | Currency: {currencies[0]} | '
        f'Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        s['ReportSmallCustom']
    ))
    story.append(Spacer(1, 10))
    story.append(_para('Executive summary', s['ReportSectionCustom']))
    status = 'favourable' if variance > 0 else ('unfavourable' if variance < 0 else 'on target')
    story.append(_para(
        f'{len(rows):,} {cfg["noun_plural"]} were selected. The recorded '
        f'{cfg["amount_label"].lower()} was {_money(actual, currency)}, compared with '
        f'{cfg["budget_label"].lower()} of {_money(budget, currency)}. '
        f'The variance was {_money(variance, currency)} ({status}). '
        'This is a descriptive comparison of the recorded data, not a compliance determination.',
        s['ReportBodyCustom']
    ))
    story.append(_para('Key financial metrics', s['ReportSectionCustom']))
    metrics = [
        ['Metric', 'Value'],
        ['Selected records', f'{len(rows):,}'],
        [cfg['amount_label'], _money(actual, currency)],
        [cfg['budget_label'], _money(budget, currency)],
        ['Favourable variance', _money(variance, currency)],
        ['Variance %', f'{variance_pct:,.2f}%' if variance_pct is not None else 'N/A'],
        ['Average recorded amount', _money(de.calculate_average(primary, main), currency)],
    ]
    story.append(_table(metrics, [225, 255]))
    story.append(_para(f'Breakdown by {cfg["default_group"]}', s['ReportSectionCustom']))
    story.append(_table(
        [[cfg['default_group'], cfg['amount_label'], 'Records']] +
        [[str(r.get(cfg['default_group'], 'Unknown'))[:42],
          _money(_dec(r.get(de.primary_column(dataset))), currency),
          str(r.get('Count', ''))] for r in grouped],
        [205, 190, 85]
    ))
    if points:
        story.append(_para('Financial distribution', s['ReportSectionCustom']))
        story.append(HorizontalBars(points, width=480))
    story.append(_para('Interpretation and limitations', s['ReportSectionCustom']))
    story.append(_para(
        'Positive variance is favourable under the dataset convention: costs use '
        'budget minus actual; sales use actual minus target. Figures may include '
        'cancelled or returned records unless explicitly excluded in the filters. '
        'This report does not establish recognized revenue, payment completion, or policy compliance.',
        s['ReportBodyCustom']
    ))
    for warning in warnings:
        story.append(_para('Filter note: ' + str(warning), s['ReportSmallCustom']))
    story.append(_para('Source: authorized Pricing Copilot SQL dataset; '
                       'figures calculated by the deterministic data engine.',
                       s['ReportSmallCustom']))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=20*mm, bottomMargin=22*mm,
                            title=report_title, author='Pricing Copilot')
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()


def generate_payroll_pdf(user, filters=None):
    """Convenience entry point for the requested payroll PDF report."""
    return generate_financial_pdf(user, dataset='payroll', filters=filters)
