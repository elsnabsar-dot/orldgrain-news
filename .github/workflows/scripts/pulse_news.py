import base64
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from decimal import Decimal
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FEED = "https://www.zerno.ru/rss.xml"
MONTH = (
    r"январе|феврале|марте|апреле|мае|июне|июле|августе|"
    r"сентябре|октябре|ноябре|декабре"
)
PERIOD = (
    rf"(?:в (?:{MONTH}) \d{{4}} года|"
    r"за (?:[1-9]|1[0-2]) (?:месяц|месяца|месяцев) \d{4} года)"
)
AMOUNT = r"(?P<amount>\d+(?:[,.]\d+)?)\s*(?P<scale>тыс\.?|млн)?"
CROP = r"(?P<crop>нута|чечевицы)"
TONNES = r"тонн?(?:ы)?"
EXPORT = re.compile(
    rf"Россия (?:экспортировала|отгрузила на экспорт) "
    rf"{AMOUNT}\s+{TONNES} {CROP} (?P<period>{PERIOD})",
    re.IGNORECASE,
)
HARVEST = re.compile(
    rf"В (?:России|РФ) (?:собрано|намолочено) "
    rf"{AMOUNT}\s+{TONNES} {CROP}",
    re.IGNORECASE,
)
SOWING = re.compile(
    rf"В (?:России|РФ) (?:посеяно|засеяно) "
    rf"{AMOUNT}\s+(?:га|гектаров) {CROP}",
    re.IGNORECASE,
)

class TitleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

def clean_title(raw):
    parser = TitleText()
    parser.feed(raw)
    parser.close()
    return " ".join("".join(parser.parts).split()).rstrip(".")

def editorial_post(title, published):
    for topic, pattern in (
        ("Экспорт", EXPORT),
        ("Урожай", HARVEST),
        ("Посевная", SOWING),
    ):
        match = pattern.fullmatch(title)
        if match is None:
            continue

        crop = match["crop"].casefold()
        tag = "#Нут" if crop == "нута" else "#Чечевица"
        amount = Decimal(match["amount"].replace(",", "."))
        scale = (match["scale"] or "").casefold().rstrip(".")
        factor = {"": 1, "тыс": 1000, "млн": 1000000}[scale]
        total = amount * factor
        if total <= 0 or total != total.to_integral_value():
            return None

        number = f"{int(total):,}".replace(",", " ")
        date_label = published.strftime("%d.%m.%Y")
        if topic == "Экспорт":
            period = match["period"].casefold()
            headline = f"🚢 Экспорт {crop} из России"
            body = f"Россия вывезла {number} т {crop} {period}."
        elif topic == "Урожай":
            headline = f"🌱 Уборка {crop} в России"
            body = f"В сводке от {date_label} указан сбор {number} т {crop}."
        else:
            headline = f"🌱 Посевная {crop} в России"
            body = (
                f"В сводке от {date_label} площадь сева "
                f"{crop} составила {number} га."
            )

        message = f"{headline}\n\n{body}\n\n#Бобовые {tag} #{topic}"
        return message

    return None

def request_json(url, *, method="GET", headers=None, payload=None):
    headers = dict(headers or {})
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, headers=headers, data=data, method=method)
    with urlopen(request, timeout=25) as response:
        return json.load(response)

def read_candidates(today, preview):
    earliest = today - timedelta(days=14 if preview else 6)
    request = Request(
        FEED, headers={"User-Agent": "WorldgrainNews/1.0 (RSS reader)"}
    )
    with urlopen(request, timeout=30) as response:
        xml = response.read(2_000_001)
    if len(xml) > 2_000_000:
        raise SystemExit("RSS превысил лимит размера.")

    candidates = {}
    for item in ET.fromstring(xml).findall("./channel/item"):
        try:
            published = datetime.strptime(
                item.findtext("pubDate", "").strip(), "%d.%m.%Y"
            ).date()
        except ValueError:
            continue
        if not earliest <= published <= today:
            continue

        parsed = urlsplit(item.findtext("link", "").strip())
        if parsed.scheme != "https" or parsed.hostname not in {
            "zerno.ru", "www.zerno.ru"
        }:
            continue
        if not re.fullmatch(r"/node/\d+/?", parsed.path):
            continue

        title = clean_title(item.findtext("title", ""))
        message = editorial_post(title, published)
        if message is None:
            continue

        url = "https://www.zerno.ru" + parsed.path.rstrip("/")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        candidates.setdefault(key, (published, url, message))

    return candidates


def read_agroexport(today, preview):
    feed = "https://aemcx.ru/feed/"
    field = "{http://purl.org/rss/1.0/modules/content/}encoded"
    forecast = re.compile(
        r"\bIGC\s+ожидает\s+сокращение\s+мирового\s+"
        r"производства\s+чечевицы\b",
        re.IGNORECASE,
    )
    request = Request(feed, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())
    items = root.findall("./channel/item")
    if not items:
        raise ValueError("Пустая лента")
    print(f"Агроэкспорт: записей в RSS — {len(items)}")

    key = "agroexport:igc:lentil:world-production-decline:2026"
    message = (
        "🌱 Мировой рынок чечевицы\n\n"
        "Международный совет по зерну (IGC) ожидает сокращения "
        "мирового производства чечевицы.\n\n"
        "#Бобовые #Чечевица #МировойРынок"
    )
    candidates = {}
    for item in items:
        parser = TitleText()
        parser.feed(item.findtext(field) or "")
        parser.close()
        if not forecast.search(" ".join(" ".join(parser.parts).split())):
            continue
        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate") or "")
            if stamp.tzinfo is None:
                continue
            published = stamp.astimezone(ZoneInfo("Europe/Saratov")).date()
        except (ValueError, TypeError, OverflowError):
            continue
        if published.year != 2026:
            continue
        if not 0 <= (today - published).days < (15 if preview else 7):
            continue
        url = (item.findtext("link") or "").strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "aemcx.ru":
            continue
        if key not in candidates or published > candidates[key][0]:
            candidates[key] = (published, url, message)
    return candidates


def main():
    event = os.environ["GITHUB_EVENT_NAME"]
    preview = event == "workflow_dispatch"
    if not preview and (
        event != "schedule" or os.environ["GITHUB_RUN_ATTEMPT"] != "1"
    ):
        return

    today = datetime.now(ZoneInfo("Europe/Saratov")).date()
    candidates = {}
    for name, reader in (
        ("Zerno.ru", read_candidates),
        ("Агроэкспорт", read_agroexport),
    ):
        try:
            candidates.update(reader(today, preview))
        except (URLError, OSError, ET.ParseError, ValueError) as error:
            print(f"::warning::{name}: {type(error).__name__}")

    branch = os.environ["GITHUB_REF_NAME"]
    contents = (
        f'{os.environ["GITHUB_API_URL"]}/repos/'
        f'{os.environ["GITHUB_REPOSITORY"]}/contents/'
    )
    headers = {
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
    }
    query = "?" + urlencode({"ref": branch})
    state_url = contents + ".worldgrain-pulse-news.json"

    def read_history(url):
        try:
            record = request_json(url + query, headers=headers)
            return (
                json.loads(base64.b64decode(record["content"])),
                record["sha"],
            )
        except HTTPError as error:
            if error.code == 404:
                return {}, None
            raise SystemExit(
                f"GitHub не прочитал журнал: HTTP {error.code}."
            ) from None

    history, sha = read_history(state_url)
    general_history, _ = read_history(contents + ".worldgrain-history.json")
    available = [
        (published, key, url, message)
        for key, (published, url, message) in candidates.items()
        if key not in history and key not in general_history
    ]
    if not available:
        print("Свежих подходящих новостей по нуту и чечевице нет.")
        return

    if preview:
        for _, _, url, message in sorted(available, reverse=True):
            print(f"Для сверки: {url}\n\n{message}\n")
        return

    published, key, url, message = max(
        available, key=lambda item: item[0]
    )
    print(f"Для сверки: {url}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Отсутствует секрет TELEGRAM_BOT_TOKEN.")

    cutoff = (today - timedelta(days=30)).isoformat()
    history = {key: day for key, day in history.items() if day >= cutoff or key.startswith("agroexport:")}
    history[key] = published.isoformat()
    update = {
        "branch": branch,
        "message": "Update Worldgrain pulse news history",
        "content": base64.b64encode(
            json.dumps(history, indent=2).encode("utf-8")
        ).decode("ascii"),
    }
    if sha:
        update["sha"] = sha
    request_json(state_url, method="PUT", headers=headers, payload=update)

    try:
        result = request_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            method="POST",
            payload={
                "chat_id": "@worldgrain",
                "text": message,
                "disable_notification": True,
            },
        )
    except HTTPError as error:
        raise SystemExit(f"Telegram вернул HTTP {error.code}.") from None
    except Exception:
        raise SystemExit(
            "Telegram не подтвердил отправку. Проверь канал."
        ) from None

    if not result.get("ok"):
        raise SystemExit("Telegram отклонил публикацию.")
    print(f'Опубликован пост №{result["result"]["message_id"]}')

main()
