from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

OUTPUT_DIR = Path(r"E:\Prismatica\PrismaticaUI\output\poster")
WIDTH = 1080
HEIGHT = 1440

TEAL = "#00B09C"
TEAL_DARK = "#007D70"
INK = "#102827"
MUTED = "#526665"
WHITE = "#FFFFFF"
RED = "#FF6B62"

FONT_REGULAR = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_UI_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def roundedImage(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    image = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255
    )
    image.putalpha(mask)
    return image


def addShadow(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 28,
    blur: int = 24,
    offset: tuple[int, int] = (0, 14),
    opacity: int = 58,
) -> None:
    x, y, width, height = box
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    offsetX, offsetY = offset
    draw.rounded_rectangle(
        (
            x + offsetX,
            y + offsetY,
            x + width + offsetX,
            y + height + offsetY,
        ),
        radius=radius,
        fill=(13, 71, 65, opacity),
    )
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


background = Image.open(OUTPUT_DIR / "prismatica-poster-background.png").convert("RGB")
background = ImageOps.fit(background, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
canvas = background.convert("RGBA")

veil = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 0))
veilDraw = ImageDraw.Draw(veil)
veilDraw.rectangle((0, 0, WIDTH, 520), fill=(255, 255, 255, 28))
veilDraw.rounded_rectangle((40, 390, 1040, 1212), radius=44, fill=(255, 255, 255, 186))
canvas.alpha_composite(veil)
draw = ImageDraw.Draw(canvas)

# 品牌栏
logo = Image.open(
    r"E:\Prismatica\PrismaticaUI\app\view\resource\images\logo.png"
).convert("RGBA")
logo = ImageOps.contain(logo, (62, 62), Image.Resampling.LANCZOS)
canvas.alpha_composite(logo, (58, 54))
draw.text((134, 59), "棱溯", font=font(FONT_BOLD, 30), fill=INK)
draw.text((222, 66), "Prismatica", font=font(FONT_UI_BOLD, 22), fill=TEAL_DARK)
draw.rounded_rectangle((808, 60, 1022, 108), radius=24, fill=RED)
draw.text((852, 69), "内测招募", font=font(FONT_BOLD, 22), fill=WHITE)

# 主标题
draw.text((58, 148), "让中文语料研究，", font=font(FONT_BOLD, 61), fill=INK)
draw.text((58, 226), "更清晰一步", font=font(FONT_BOLD, 74), fill=TEAL_DARK)
draw.text(
    (58, 330),
    "HSK 作文检索 × 本地语料分析 × 可视化研究",
    font=font(FONT_BOLD, 27),
    fill=INK,
)
draw.text(
    (58, 373),
    "为中文国际教育研究者、教师与语言学学习者而做",
    font=font(FONT_REGULAR, 22),
    fill=MUTED,
)

# 语料分析实机截图
mainBox = (58, 438, 914, 515)
addShadow(canvas, mainBox, radius=28, blur=23, offset=(0, 15), opacity=50)
draw.rounded_rectangle(
    (58, 438, 972, 953),
    radius=28,
    fill=(255, 255, 255, 255),
    outline=(199, 229, 225, 255),
    width=2,
)
analysis = Image.open(OUTPUT_DIR / "analysis.png").convert("RGB")
analysisCrop = analysis.crop((0, 0, analysis.width, 1080))
canvas.alpha_composite(roundedImage(analysisCrop, (886, 475), 20), (72, 452))
draw.rounded_rectangle((84, 466, 244, 506), radius=20, fill=(0, 176, 156, 232))
draw.text((112, 472), "语料分析总览", font=font(FONT_BOLD, 18), fill=WHITE)

# HSK 作文检索实机截图
subBox = (396, 832, 576, 326)
addShadow(canvas, subBox, radius=24, blur=22, offset=(0, 12), opacity=64)
draw.rounded_rectangle(
    (396, 832, 972, 1158),
    radius=24,
    fill=(255, 255, 255, 255),
    outline=(193, 225, 220, 255),
    width=2,
)
hsk = Image.open(OUTPUT_DIR / "hsk-corpus.png").convert("RGB")
hskCrop = hsk.crop((70, 44, 1920, 1048))
canvas.alpha_composite(roundedImage(hskCrop, (548, 274), 16), (410, 846))
draw.rounded_rectangle((422, 860, 598, 900), radius=20, fill=(16, 40, 39, 226))
draw.text((449, 866), "HSK 作文检索", font=font(FONT_BOLD, 18), fill=WHITE)

# 功能说明
features = [
    ("01", "HSK 作文检索与导出"),
    ("02", "本地语料导入与清洗"),
    ("03", "词频 · KWIC · 搭配 · 共现"),
    ("04", "偏误 · 句法 · 情感分析"),
]
startY = 986
for index, (number, label) in enumerate(features):
    y = startY + index * 52
    draw.ellipse((58, y, 94, y + 36), fill=TEAL)
    numberFont = font(FONT_UI_BOLD, 14)
    numberBox = draw.textbbox((0, 0), number, font=numberFont)
    draw.text(
        (76 - (numberBox[2] - numberBox[0]) / 2, y + 8),
        number,
        font=numberFont,
        fill=WHITE,
    )
    draw.text((110, y + 3), label, font=font(FONT_BOLD, 22), fill=INK)


def chip(x: int, y: int, label: str) -> int:
    labelFont = font(FONT_BOLD, 17)
    textWidth = draw.textbbox((0, 0), label, font=labelFont)[2]
    draw.rounded_rectangle(
        (x, y, x + textWidth + 46, y + 40), radius=20, fill=(232, 248, 245, 238)
    )
    draw.ellipse((x + 14, y + 15, x + 24, y + 25), fill=TEAL)
    draw.text((x + 32, y + 8), label, font=labelFont, fill=TEAL_DARK)
    return x + textWidth + 60


chipX = chip(58, 1214, "Windows 桌面端")
chip(chipX, 1214, "语料分析本地进行")

# 行动区，不放二维码或预留编辑区域
draw.rounded_rectangle((40, 1284, 1040, 1402), radius=34, fill=(11, 59, 55, 246))
draw.text(
    (70, 1305),
    "内测期间 · 功能免费开放",
    font=font(FONT_BOLD, 26),
    fill=(201, 255, 247),
)
draw.text(
    (70, 1348), "内测时间2026年8月20日-30日(暂定)", font=font(FONT_BOLD, 32), fill=WHITE
)
draw.rounded_rectangle((808, 1310, 1007, 1376), radius=33, fill=TEAL)
draw.text((852, 1326), "立即体验  →", font=font(FONT_BOLD, 22), fill=WHITE)

finalImage = canvas.convert("RGB")
finalImage.save(OUTPUT_DIR / "prismatica-xhs-beta-poster.png", quality=96)
finalImage.save(
    OUTPUT_DIR / "prismatica-xhs-beta-poster.jpg",
    quality=96,
    subsampling=0,
)
