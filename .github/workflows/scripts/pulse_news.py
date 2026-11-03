
    
  
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

