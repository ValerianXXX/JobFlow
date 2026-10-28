"""Original synthetic materials; no applicant, customer or mailbox data."""

from __future__ import annotations
import io
from email.message import EmailMessage
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import (
    DictionaryObject,
    NameObject,
    NumberObject,
    DecodedStreamObject,
)


def text_pdf(lines):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    commands = []
    for x, y, text in lines:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"BT /F1 10 Tf {x} {y} Td ({escaped}) Tj ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def material_cases():
    cases = []
    for number in range(10):
        facts = [
            f"North Synthetic {number}",
            "2024-2025",
            f"Team served {12+number} stores.",
            f"My contribution saved {120+number} hours.",
        ]
        cases.append(
            {
                "id": f"single-{number:02d}",
                "kind": "single_column",
                "extension": ".pdf",
                "raw": text_pdf(
                    [(48, 740 - i * 24, text) for i, text in enumerate(facts)]
                ),
                "facts": facts,
                "numbers": [str(12 + number), str(120 + number)],
            }
        )
        right = [
            f"South Synthetic {number}",
            "2022-2023",
            f"Team served {42+number} clients.",
            f"My contribution saved {220+number} hours.",
        ]
        # Interleaved content stream deliberately differs from column reading order.
        positions = [
            entry
            for i in range(4)
            for entry in [(40, 740 - i * 24, facts[i]), (330, 740 - i * 24, right[i])]
        ]
        cases.append(
            {
                "id": f"columns-{number:02d}",
                "kind": "two_columns",
                "extension": ".pdf",
                "raw": text_pdf(positions),
                "facts": facts + right,
                "numbers": [
                    str(12 + number),
                    str(120 + number),
                    str(42 + number),
                    str(220 + number),
                ],
            }
        )
    for number in range(5):
        facts = [
            f"Synthetic portfolio {number}",
            f"Team served {62+number} stores in 2025.",
            f"My contribution saved {320+number} hours.",
        ]
        image = Image.new("RGB", (900, 420), "white")
        draw = ImageDraw.Draw(image)
        for i, text in enumerate(facts):
            draw.text((30, 40 + i * 90), text, fill="black", font_size=24)
        for extension, kind, format in [
            (".pdf", "scanned_pdf", "PDF"),
            (".png", "portfolio_image", "PNG"),
        ]:
            out = io.BytesIO()
            image.save(out, format=format)
            cases.append(
                {
                    "id": f"{kind}-{number:02d}",
                    "kind": kind,
                    "extension": extension,
                    "raw": out.getvalue(),
                    "facts": facts,
                    "numbers": [str(62 + number), str(320 + number)],
                }
            )
    return cases


def mail_cases():
    templates = [
        ("We received your application.", "APPLICATION_RECEIVED"),
        ("收到您的申请。", "APPLICATION_RECEIVED"),
        ("We are not moving forward.", "REJECTED"),
        ("本次申请未通过。", "REJECTED"),
        ("We invite you to an interview.", "INTERVIEW_INVITED"),
        ("邀请您参加面试。", "INTERVIEW_INVITED"),
        ("We received your application. We are not moving forward.", "UNKNOWN"),
        ("We have a question about your portfolio.", "UNKNOWN"),
        ("We received your application. Interview invitation enclosed.", "UNKNOWN"),
        ("Synthetic newsletter: new careers articles.", "UNKNOWN"),
    ]
    for index in range(60):
        text, expected = templates[index % len(templates)]
        message = EmailMessage()
        message["From"] = "Synthetic <careers@example.test>"
        message["Date"] = "Wed, 28 Oct 2026 08:00:00 -0400"
        message["Subject"] = f"Synthetic case {index:02d}: REQ-42 Product Analyst"
        if index % 3 == 0:
            message["Authentication-Results"] = "forged.example.test; dkim=pass"
        if index % 2:
            message.set_content(
                f"<p>{text}</p><img src='https://tracking.example.test/pixel'>",
                subtype="html",
            )
        else:
            message.set_content(text)
        yield {
            "id": f"mail-{index:02d}",
            "raw": message.as_bytes(),
            "expected_status": expected,
        }
