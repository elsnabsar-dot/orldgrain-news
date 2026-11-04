"""Worldgrain oilseed cards. Manual runs only create preview artifacts."""
import base64
import copy
import html
import json
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

SOURCE = "https://astontd.ru/"
POSITIONS = {
    "podsolnechnik_2026-desc-aston_povolzhe_buzuluk": "Подсолнечник · Бузулук",
    "raps-desc-aston_g_morozovsk": "Рапс · Морозовск",
}
OUTPUT = Path("oilseed-preview")


class PricePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.header = []
        self.header_depth = 0
        self.row = None
        self.cell = None
        self.cell_id = ""
        self.prices = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div":
            classes = (attributes.get("class") or "").split()
            if self.header_depth or "pricelist-header-text" in classes:
                self.header_depth += 1
        if tag == "tr":
            self.row = []
        if tag == "td" and self.row is not None:
            self.cell = []
            self.cell_id = attributes.get("data-id") or ""

    def handle_data(self, text):
        if self.header_depth:
            self.header.append(text)
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag == "div" and self.header_depth:
            self.header_depth -= 1
        if tag == "td" and self.cell is not None:
            self.row.append((self.cell_id, " ".join("".join(self.cell).split())))
            self.cell = None
        if tag == "tr" and self.row is not None:
            if any(value.casefold() == "без ндс" for _, value in self.row):
                for key, value in self.row:
                    if key in POSITIONS and re.fullmatch(r"\d{1,3}(?:[,.]\d{1,3})?", value):
                        amount = Decimal(value.replace(",", ".")) * 1000
                        self.prices.setdefault(key, set()).add(int(amount))
            self.row = None


def fetch_json(url, method="GET", headers=None, payload=None):
    headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=body, headers=headers, method=method), timeout=30) as response:
        return json.load(response)


def parse_prices(raw, today):
    parser = PricePage()
    parser.feed(raw)
    parser.close()
    header = " ".join(" ".join(parser.header).split())
    stamp = re.search(r"Прайс[-‑–—\s]*лист\s+действует\s+с\s+(\d{2}\.\d{2}\.\d{4})\s+с\s+(\d{2}:\d{2})", header, re.I)
    terms = re.search(r"Базис поставки\s*[-–—:]\s*CPT\b.*?Цены без НДС\s*[-–—:]\s*в руб\.?,?\s*за 1 кг\b", header, re.I)
    if not stamp or not terms:
        raise ValueError("Не удалось подтвердить дату, CPT или единицы прайса.")
    date_label, time_label = stamp.groups()
    price_date = datetime.strptime(date_label, "%d.%m.%Y").date()
    datetime.strptime(time_label, "%H:%M")
    if price_date > today:
        print("Прайс вступает в силу позже. Выпуск пропущен.")
        return None
    prices = {}
    for key in POSITIONS:
        values = parser.prices.get(key, set())
        if len(values) > 1:
            raise ValueError(f"Для {POSITIONS[key]} найдены разные цены.")
        if values:
            amount = next(iter(values))
            if amount <= 0:
                raise ValueError("Получена нулевая цена.")
            prices[key] = amount
        else:
            print(f"Цена не найдена: {POSITIONS[key]}.")
    if not prices:
        raise ValueError("Ни одна цена не распознана.")
    return prices, date_label, time_label


def number(value, signed=False):
    return (f"{value:+,}" if signed else f"{value:,}").replace(",", " ")


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/" + name, size)


def make_card(prices, state, date_label, time_label):
    OUTPUT.mkdir(exist_ok=True)
    image = Image.new("RGB", (1080, 1080), "#0D2722")
    draw = ImageDraw.Draw(image)
    gold, cream, muted = "#E6C477", "#F4F2E8", "#ABC0B5"
    draw.rounded_rectangle((48, 48, 112, 112), radius=15, fill=gold)
    draw.text((68, 53), "1", font=font(43, True), fill="#0D2722")
    draw.text((134, 52), "ПЕРВЫЙ ЗЕРНОВОЙ", font=font(36, True), fill=cream)
    draw.text((136, 98), "ЦЕНЫ • ИЗМЕНЕНИЯ • РАСЧЁТ", font=font(19), fill=muted)
    draw.text((48, 155), "МАСЛИЧНЫЕ", font=font(58, True), fill=cream)
    draw.text((50, 234), "АСТОН  /  CPT  /  БЕЗ НДС", font=font(25), fill=gold)
    draw.text((50, 277), f"Прайс с {date_label}, {time_label}", font=font(25), fill=muted)
    lines = ["🌻 <b>Масличные — цены и изменения</b>", "", f"Прайс с {date_label}, {time_label}.", ""]
    for index, (key, amount) in enumerate(prices.items()):
        top = 342 + index * 270
        draw.rounded_rectangle((48, top, 1032, top + 246), radius=22, fill="#183A31")
        draw.text((76, top + 20), POSITIONS[key], font=font(32, True), fill=cream)
        draw.text((72, top + 65), number(amount), font=font(65, True), fill=cream)
        draw.text((365, top + 98), "₽/т", font=font(27), fill=muted)
        previous = state.get(key)
        lines.append(f"<b>{html.escape(POSITIONS[key])}</b> — {number(amount)} ₽/т")
        if isinstance(previous, int):
            delta = amount - previous
            change = "Без изменений" if delta == 0 else f'{"↑" if delta > 0 else "↓"} {number(delta, True)} ₽/т'
            color = gold if delta >= 0 else "#F0A78E"
            draw.text((570, top + 83), change, font=font(30, True), fill=color)
            draw.text((76, top + 153), f"Предыдущая цена {number(previous)} ₽/т", font=font(23), fill=muted)
            draw.text((76, top + 191), f"Разница на 20 т   {number(delta * 20, True)} ₽", font=font(25, True), fill=color)
            lines.append(f"Изменение {number(delta, True)} ₽/т · на 20 т {number(delta * 20, True)} ₽.")
        else:
            draw.text((570, top + 83), "Первая отметка", font=font(27), fill=gold)
            draw.text((76, top + 165), "Сохраняем цену для следующих сравнений", font=font(26), fill=muted)
            lines.append("Первая отметка в истории.")
        lines.append("")
    draw.line((48, 916, 1032, 916), fill="#416050", width=2)
    draw.text((50, 938), "Сравнение с предыдущим выпуском", font=font(25), fill=muted)
    draw.text((50, 984), "@worldgrain", font=font(33, True), fill=gold)
    draw.text((670, 996), "РАСЧЁТ НА ПАРТИЮ 20 Т", font=font(20), fill=cream)
    lines.extend(["CPT · без НДС. Разница рассчитана при одинаковых условиях.", "", "#Масличные #Цены", "", '🌾 Следите за изменениями цен — <a href="https://t.me/worldgrain">Первый Зерновой</a>'])
    caption = "\n".join(lines)
    if len(re.sub("<[^>]+>", "", caption)) > 1024:
        raise ValueError("Подпись превысила лимит Telegram.")
    image.save(OUTPUT / "oilseed-card.png", optimize=True)
    (OUTPUT / "caption.html").write_text(caption, encoding="utf-8")
    print(caption)
    return caption


def send_photo(token, caption):
    boundary = "Worldgrain" + uuid.uuid4().hex
    chunks = []
    for key, value in {"chat_id": "@worldgrain", "caption": caption, "parse_mode": "HTML", "disable_notification": "true"}.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="oilseed-card.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
    chunks.append((OUTPUT / "oilseed-card.png").read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    request = Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urlopen(request, timeout=45) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError("Telegram не подтвердил отправку. Проверь канал и _pending в файле истории. Автоповтор остановлен.") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram отклонил публикацию. Проверь _pending в файле истории.")
    return result["result"]["message_id"]


def main():
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event not in ("workflow_dispatch", "schedule"):
        raise ValueError("Ожидается ручной запуск или расписание GitHub.")
    preview = event == "workflow_dispatch"
    if not preview and os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        print("Повтор автоматического запуска пропущен.")
        return
    request = Request(SOURCE, headers={"User-Agent": "WorldgrainPrices/2.0"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(6_000_001)
        encoding = response.headers.get_content_charset() or "utf-8"
    if len(raw) > 6_000_000:
        raise ValueError("Страница прайса превысила лимит.")
    parsed = parse_prices(raw.decode(encoding), datetime.now(ZoneInfo("Europe/Saratov")).date())
    if parsed is None:
        return
    prices, date_label, time_label = parsed
    print(f"Прайс для сверки: {SOURCE}#price")
    branch = os.environ["GITHUB_REF_NAME"]
    state_url = f'{os.environ["GITHUB_API_URL"]}/repos/{os.environ["GITHUB_REPOSITORY"]}/contents/.worldgrain-oilseed-prices.json'
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"}
    state, sha = {}, None
    try:
        stored = fetch_json(state_url + "?" + urlencode({"ref": branch}), headers=headers)
        sha = stored["sha"]
        state = json.loads(base64.b64decode(stored["content"]))
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f"GitHub вернул HTTP {error.code}.") from None
    if not isinstance(state, dict):
        raise ValueError("Файл истории повреждён.")
    caption = make_card(prices, state, date_label, time_label)
    if preview:
        print("ПРЕДПРОСМОТР ГОТОВ. Открой artifact oilseed-preview. История и канал не изменены.")
        return
    if state.get("_pending"):
        raise RuntimeError("Есть незавершённая отправка _pending. Сверь канал и восстанови историю перед следующим выпуском.")
    if all(state.get(key) == value for key, value in prices.items()):
        print("Цены совпадают с предыдущим выпуском.")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Отсутствует TELEGRAM_BOT_TOKEN.")

    def save(data, current_sha):
        payload = {"branch": branch, "message": "Update Worldgrain oilseed price history", "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()}
        if current_sha:
            payload["sha"] = current_sha
        return fetch_json(state_url, "PUT", headers, payload)["content"]["sha"]

    pending = {"prices": prices, "price_date": date_label, "price_time": time_label, "run_id": os.environ.get("GITHUB_RUN_ID")}
    reserved = copy.deepcopy(state)
    reserved["_pending"] = pending
    sha = save(reserved, sha)
    message_id = send_photo(token, caption)
    print(f"Опубликован пост №{message_id}", flush=True)
    state.update(prices)
    state.pop("_pending", None)
    history = state.setdefault("_history", [])
    history.append({**pending, "message_id": message_id, "published_at": datetime.now(ZoneInfo("Europe/Saratov")).isoformat()})
    state["_history"] = history[-400:]
    try:
        save(state, sha)
    except Exception:
        raise RuntimeError(f"Пост №{message_id} отправлен, но история не сохранена. _pending блокирует повтор. Нужна сверка.") from None


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid exposing request URLs containing a bot token in tracebacks.
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error))
        else:
            print(f"Ошибка {type(error).__name__}. Проверь источник и доступ к GitHub.")
        raise SystemExit(1)
