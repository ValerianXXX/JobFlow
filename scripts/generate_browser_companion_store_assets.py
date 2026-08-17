from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ICON_ROOT = ROOT / "browser-companion" / "icons"
ASSET_ROOT = ROOT / "docs" / "store-assets"
GREEN = "#2f6f5f"
GREEN_DARK = "#214f45"
GREEN_SOFT = "#e9f2ef"
INK = "#17212b"
MUTED = "#64748b"
LINE = "#cad8d3"
CREAM = "#fbfaf6"


def font(size: int, *, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = (
        ("georgiab.ttf" if bold else "georgia.ttf")
        if serif
        else ("arialbd.ttf" if bold else "arial.ttf")
    )
    try:
        return ImageFont.truetype(str(windows / names), size=size)
    except OSError:
        return ImageFont.load_default()


def fit_text(draw: ImageDraw.ImageDraw, text: str, maximum: int, *, start: int, bold: bool = False, serif: bool = False):
    size = start
    while size > 10:
        candidate = font(size, bold=bold, serif=serif)
        if draw.textbbox((0, 0), text, font=candidate)[2] <= maximum:
            return candidate
        size -= 1
    return font(10, bold=bold, serif=serif)


def logo(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = box
    size = min(right - left, bottom - top)
    x = left + ((right - left) - size) // 2
    y = top + ((bottom - top) - size) // 2
    radius = max(1, size // 10)
    draw.rounded_rectangle((x, y, x + size, y + size), radius=radius, fill=GREEN)
    mark = font(max(9, int(size * 0.58)), bold=True, serif=True)
    mark_box = draw.textbbox((0, 0), "J", font=mark)
    mark_width = mark_box[2] - mark_box[0]
    mark_height = mark_box[3] - mark_box[1]
    draw.text(
        (x + (size - mark_width) / 2, y + (size - mark_height) / 2 - mark_box[1]),
        "J",
        font=mark,
        fill="white",
    )


def build_icons() -> None:
    ICON_ROOT.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        padding = max(1, round(size * 0.125))
        logo(image, (padding, padding, size - padding, size - padding))
        image.save(ICON_ROOT / f"icon-{size}.png", optimize=True)


def build_promo() -> None:
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (440, 280), CREAM)
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, 220, 178), fill=LINE, width=3)
    draw.line((440, 0, 220, 178), fill=LINE, width=3)
    logo(image, (36, 35, 112, 111))
    draw.text((128, 39), "JobFlow", font=font(35, bold=True, serif=True), fill=INK)
    draw.text((128, 79), "Browser Companion", font=font(17, bold=True), fill=GREEN_DARK)
    draw.text((36, 147), "Local, reviewed, user-present", font=font(20, bold=True), fill=INK)
    draw.text((36, 181), "Prefill and attach materials.", font=font(15), fill=MUTED)
    draw.text((36, 205), "Final Submit always stays with you.", font=font(15), fill=MUTED)
    draw.rounded_rectangle((36, 239, 404, 249), radius=5, fill=GREEN)
    image.save(ASSET_ROOT / "small-promo-440x280.png", optimize=True)


def draw_popup(image: Image.Image, *, x: int, y: int, title: str, status: str, button: str) -> None:
    draw = ImageDraw.Draw(image)
    width, height = 330, 500
    draw.rounded_rectangle((x + 8, y + 10, x + width + 8, y + height + 10), radius=12, fill="#d8dedc")
    draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill="white", outline=LINE, width=2)
    logo(image, (x + 20, y + 18, x + 60, y + 58))
    draw.text((x + 72, y + 17), "JobFlow", font=font(23, bold=True, serif=True), fill=INK)
    draw.text((x + 72, y + 45), "Browser Companion v0.9.1", font=font(11), fill=MUTED)
    draw.line((x, y + 76, x + width, y + 76), fill=LINE, width=1)
    draw.text((x + 240, y + 94), "EN", font=font(12, bold=True), fill=GREEN_DARK)
    draw.rounded_rectangle((x + 18, y + 126, x + width - 18, y + 226), radius=4, fill=GREEN_SOFT)
    draw.rectangle((x + 18, y + 126, x + 22, y + 226), fill=GREEN)
    draw.text((x + 35, y + 140), title, font=fit_text(draw, title, 255, start=16, bold=True), fill=INK)
    draw.multiline_text((x + 35, y + 174), status, font=font(12), fill=GREEN_DARK, spacing=5)
    draw.rounded_rectangle((x + 18, y + 246, x + width - 18, y + 296), radius=4, fill=GREEN)
    action_font = fit_text(draw, button, width - 70, start=15, bold=True)
    action_box = draw.textbbox((0, 0), button, font=action_font)
    draw.text((x + (width - action_box[2]) / 2, y + 262), button, font=action_font, fill="white")
    draw.multiline_text(
        (x + 18, y + 321),
        "JobFlow reads only the active page you choose.\nApproved values and files remain local.\nFinal Submit is never clicked.",
        font=font(12), fill=MUTED, spacing=9,
    )
    draw.rounded_rectangle((x + 18, y + 425, x + width - 18, y + 467), radius=4, outline=LINE, width=1)
    draw.text((x + 38, y + 438), "Final Submit: USER ONLY", font=font(13, bold=True), fill=GREEN_DARK)


def build_local_app_screenshot() -> None:
    source = Image.open(ROOT / "docs" / "screenshots" / "jobflow-demo-en.png").convert("RGB")
    source = source.resize((1152, 800), Image.Resampling.LANCZOS)
    image = Image.new("RGB", (1280, 800), "white")
    image.paste(source, (0, 0))
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    ImageDraw.Draw(overlay).rectangle((0, 0, 1280, 800), fill=(255, 255, 255, 22))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw_popup(
        image, x=930, y=125,
        title="Paired with JobFlow",
        status="Local secure channel ready\nNo account or browser data imported",
        button="Review current job page",
    )
    image.save(ASSET_ROOT / "screenshot-1-local-workflow-1280x800.png", optimize=True)


def build_application_screenshot() -> None:
    image = Image.new("RGB", (1280, 800), CREAM)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1280, 74), fill="white")
    logo(image, (46, 16, 88, 58))
    draw.text((98, 16), "Example Careers", font=font(24, bold=True, serif=True), fill=INK)
    draw.text((98, 45), "Synthetic demonstration — no real applicant data", font=font(11), fill=MUTED)
    draw.text((70, 112), "Apply for Risk Operations Analyst", font=font(31, bold=True), fill=INK)
    draw.text((70, 157), "Contact information", font=font(18, bold=True), fill=GREEN_DARK)
    fields = [
        (70, 205, 470, "First name", "Approved private value"),
        (500, 205, 900, "Last name", "Approved private value"),
        (70, 310, 470, "Email", "Approved private value"),
        (500, 310, 900, "Phone", "Approved private value"),
        (70, 415, 900, "Resume", "Approved tailored resume attached"),
        (70, 520, 900, "Work authorization", "Requires user confirmation"),
    ]
    for left, top, right, label, value in fields:
        draw.text((left, top - 24), label, font=font(13, bold=True), fill=INK)
        draw.rounded_rectangle((left, top, right, top + 62), radius=5, fill="white", outline=LINE, width=2)
        color = GREEN_DARK if "Approved" in value else "#9a5b00"
        draw.text((left + 16, top + 20), value, font=font(14), fill=color)
    draw.rounded_rectangle((70, 630, 900, 711), radius=5, fill=GREEN_SOFT, outline=GREEN, width=2)
    draw.text((94, 648), "Ready for your review", font=font(18, bold=True), fill=GREEN_DARK)
    draw.text((94, 681), "JobFlow stops before final Submit.", font=font(14), fill=MUTED)
    draw_popup(
        image, x=930, y=128,
        title="Approved application",
        status="Page 1 of 3 verified\nChanged or unknown fields stop safely",
        button="Fill approved fields",
    )
    image.save(ASSET_ROOT / "screenshot-2-approved-prefill-1280x800.png", optimize=True)


def build_marquee() -> None:
    image = Image.new("RGB", (1400, 560), CREAM)
    draw = ImageDraw.Draw(image)
    draw.line((0, 0, 700, 560), fill=LINE, width=4)
    draw.line((1400, 0, 700, 560), fill=LINE, width=4)
    logo(image, (90, 80, 220, 210))
    draw.text((260, 85), "JobFlow Browser Companion", font=font(51, bold=True, serif=True), fill=INK)
    draw.text((260, 155), "Local job-page understanding and reviewed application assistance", font=font(24), fill=GREEN_DARK)
    draw.rounded_rectangle((90, 285, 1310, 420), radius=12, fill="white", outline=LINE, width=2)
    draw.text((130, 320), "Read the page you choose", font=font(21, bold=True), fill=INK)
    draw.text((495, 320), "Fill only approved values", font=font(21, bold=True), fill=INK)
    draw.text((890, 320), "Keep final Submit yours", font=font(21, bold=True), fill=INK)
    draw.text((130, 365), "No passwords", font=font(16), fill=MUTED)
    draw.text((495, 365), "No hidden retries", font=font(16), fill=MUTED)
    draw.text((890, 365), "No automatic final submission", font=font(16), fill=MUTED)
    image.save(ASSET_ROOT / "marquee-1400x560.png", optimize=True)


def main() -> None:
    build_icons()
    build_promo()
    build_local_app_screenshot()
    build_application_screenshot()
    build_marquee()
    print("Browser Companion store assets generated.")


if __name__ == "__main__":
    main()
