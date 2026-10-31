"""Build separate synthetic page-content regressions; never alter the 36-material corpus."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from workbench_fixtures import text_pdf

FACTS = [
    'SYNTHETIC MATERIAL - NO REAL APPLICANT',
    'Cedar Example Lab - analyst - 2024 to 2025',
    'The team served 12 stores.',
    'My contribution saved 120 hours.',
    'The team budget was USD 3000.',
    'I had no spending authority.',
]


def make_pdf(lines, rectangles=(), *, rotate=False, invisible_text=False, decoration=False):
    writer = PdfWriter()
    page = writer.add_page(PdfReader(io.BytesIO(text_pdf(lines))).pages[0])
    bitmap = Image.new('RGB', (960, 600), 'white')
    draw = ImageDraw.Draw(bitmap)
    if decoration:
        draw.rectangle((30, 30, 930, 570), outline='#123456', width=30)
        draw.text((190, 200), 'EXAMPLE', fill='#123456', font_size=130)
    else:
        for index, fact in enumerate(FACTS):
            draw.text((30, 35 + index * 80), fact, fill='black', font_size=25)
    raster = DecodedStreamObject()
    raster.set_data(bitmap.tobytes())
    raster.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                   NameObject('/Width'): NumberObject(bitmap.width), NameObject('/Height'): NumberObject(bitmap.height),
                   NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
    page['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Scan'): writer._add_object(raster.flate_encode())})
    # Image first, text overlay last. Rectangles are PDF bottom-left coordinates.
    commands = [f'q {w} 0 0 {h} {x} {y} cm /Scan Do Q' for x, y, w, h in rectangles]
    content = DecodedStreamObject()
    text_stream = page.get_contents().get_data()
    if invisible_text:
        text_stream = text_stream.replace(b'BT /F1', b'BT 3 Tr /F1')
    content.set_data(('\n'.join(commands) + '\n').encode('ascii') + text_stream)
    page[NameObject('/Contents')] = writer._add_object(content)
    if rotate:
        page.rotate(90)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build():
    destination = ROOT / 'tests/fixtures/pdf-content-probes-v1'
    destination.mkdir(exist_ok=False)
    body = [(48, 700 - i * 40, fact) for i, fact in enumerate(FACTS)]
    footer = [(260, 24, 'Page 1 of 1')]
    raster = [(40, 130, 532, 550)]
    cases = [
        ('scan-footer', make_pdf(footer, raster), True),
        ('scan-header', make_pdf([(48, 740, 'Synthetic portfolio: project evidence follows')], raster), True),
        ('pagination-only', make_pdf(footer), True),
        ('tiled-scan', make_pdf(footer, [(40 + (i % 3) * 175, 130 + (i // 3) * 135, 175, 135) for i in range(12)]), True),
        ('rotated-scan', make_pdf(footer, raster, rotate=True), True),
        ('text-only', make_pdf(body + footer), False),
        ('small-logo', make_pdf(body + footer, [(520, 720, 40, 40)], decoration=True), False),
        ('searchable-raster', make_pdf(body + footer, [(35, 430, 540, 300)], invisible_text=True), False),
        ('overlapping-stamps', make_pdf(body + footer, [(460, 100, 100, 100)] * 12, decoration=True), False),
        ('off-page-raster', make_pdf(body + footer, [(-480, 100, 500, 500)]), False),
    ]
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(cases[5][1])).pages[0])
    writer.add_page(PdfReader(io.BytesIO(cases[0][1])).pages[0])
    output = io.BytesIO()
    writer.write(output)
    cases.append(('mixed-pages', output.getvalue(), True))
    manifest = {'synthetic_only': True, 'purpose': 'Text extraction coverage regression, not model quality or whole-material acceptance',
                'expected_visible_raster_facts': FACTS, 'cases': []}
    for name, raw, blocked in cases:
        filename = name + '.pdf'
        (destination / filename).write_bytes(raw)
        manifest['cases'].append({'id': name, 'file': filename, 'sha256': hashlib.sha256(raw).hexdigest(), 'must_block_text_only': blocked})
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'synthetic_cases': len(cases), 'directory': destination.name}))


if __name__ == '__main__':
    build()
