"""Daily market cards. Manual runs prepare previews without publishing."""
import base64
import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

OUT = Path('market-preview')
ZONE = ZoneInfo('Europe/Saratov')
CROPS = {'wheat': ('Пшеница', '12.5'), 'sunflower': ('Подсолнечник', ''), 'barley': ('Ячмень', '')}
SLOTS = {'17 6 * * *': 'wheat', '17 10 * * *': 'sunflower', '17 14 * * *': 'barley'}


def request_json(url, method='GET', headers=None, payload=None):
    headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=data, headers=headers, method=method), timeout=35) as response:
        raw = response.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('Ответ источника слишком большой.')
    return json.loads(raw)


def clean(value):
    return ' '.join(str(value or '').split())


def parse_rows(data, crop, today):
    groups = data if isinstance(data, list) else data.get('results')
    if not isinstance(groups, list):
        raise ValueError('Изменилась структура ответа площадки.')
    name, quality = CROPS[crop]
    rows = {}
    count = 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('data'), list):
            raise ValueError('Нет списка предложений в ответе.')
        for item in group['data']:
            count += 1
            culture = item.get('culture') or {}
            if culture.get('name') != name or clean(culture.get('subName')).replace(',', '.') != quality:
                continue
            if item.get('market') is not False or item.get('auto') is not True:
                continue
            try:
                price = Decimal(str(item['price']))
                stamp = datetime.fromisoformat(item['dateTime']).date()
                if not price.is_finite() or not 0 < price <= 1_000_000:
                    continue
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
            if not 0 <= (today - stamp).days <= 3:
                continue
            warehouse = item.get('warehouseObj') or {}
            company = clean(item.get('company'))
            terminal = clean(warehouse.get('title') or item.get('warehouse'))
            region = clean(warehouse.get('region') or group.get('city'))
            specs = clean(item.get('specs'))
            if not company or not terminal or not region:
                continue
            identity = [name, quality, item.get('companyInn') or company,
                        warehouse.get('id') or terminal, specs, item.get('railway'), 'CPT', 'VAT unknown']
            key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            row = dict(key=key, company=company, terminal=terminal, region=region,
                       specs=specs, price=float(price), date=stamp.isoformat())
            old = rows.get(key)
            if old and old['date'] == row['date'] and old['price'] != row['price']:
                raise ValueError('В источнике конфликтующие цены одной позиции за одну дату.')
            if old is None or row['date'] > old['date']:
                rows[key] = row
    result = sorted(rows.values(), key=lambda x: (-datetime.fromisoformat(x['date']).toordinal(), x['company'], x['terminal']))
    print(f'{name}: получено {count}, прошло фильтр {len(result)}.')
    return result


def select_rows(rows):
    selected, companies = [], set()
    for row in rows:
        if row['company'] not in companies:
            selected.append(row)
            companies.add(row['company'])
        if len(selected) == 3:
            break
    for row in rows:
        if len(selected) == 3:
            break
        if row not in selected:
            selected.append(row)
    return selected


def fmt(value):
    return f'{value:,.2f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',')


def font(size, bold=False):
    suffix = '-Bold' if bold else ''
    return ImageFont.truetype(f'/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf', size)


def label(draw, xy, text, width, size, color, bold=False):
    f = font(size, bold)
    if draw.textlength(text, font=f) > width:
        while text and draw.textlength(text + '…', font=f) > width:
            text = text[:-1]
        text = text.rstrip() + '…'
    draw.text(xy, text, font=f, fill=color)


def build(crop, rows, previous, now):
    OUT.mkdir(exist_ok=True)
    name, quality = CROPS[crop]
    title = name + (' 12,5' if quality else '')
    image = Image.new('RGB', (1080, 1350), '#0D2722')
    draw = ImageDraw.Draw(image)
    white, gold, muted = '#F4F2E8', '#E6C477', '#ABC0B5'
    draw.rounded_rectangle((48, 48, 110, 110), radius=15, fill=gold)
    draw.text((66, 53), '1', font=font(42, True), fill='#0D2722')
    draw.text((135, 60), 'ПЕРВЫЙ ЗЕРНОВОЙ', font=font(36, True), fill=white)
    label(draw, (48, 147), title.upper(), 984, 53, white, True)
    draw.text((50, 220), 'ПРЕДЛОЖЕНИЯ ПОКУПАТЕЛЕЙ', font=font(25), fill=gold)
    draw.text((50, 269), f'Проверка {now:%d.%m.%Y, %H:%M} · Саратов', font=font(23), fill=muted)
    draw.text((50, 310), 'CPT · автодоставка · НДС уточняется', font=font(24), fill=muted)
    parts = [f'🌾 <b>{html.escape(title)} — предложения покупателей</b>', '',
             f'Проверено {now:%d.%m.%Y, %H:%M} по Саратову.', '']
    for index, row in enumerate(rows):
        top = 365 + 267 * index
        draw.rounded_rectangle((48, top, 1032, top + 248), radius=20, fill='#183A31')
        company = re.sub(r'^(ООО|ПАО|АО)\s*', '', row['company']).strip('"«» ')
        label(draw, (72, top+17), company, 930, 29, white, True)
        label(draw, (72, top+61), row['terminal'], 930, 23, muted)
        label(draw, (72, top+94), row['region'], 930, 22, muted)
        draw.text((70, top+129), fmt(row['price'])+' ₽/т', font=font(48, True), fill=gold)
        old = previous.get(row['key'])
        change = 'Первая отметка'
        if old is not None:
            delta = row['price'] - old
            change = 'Без изменений' if delta == 0 else ('+' if delta > 0 else '−') + fmt(abs(delta)) + ' ₽/т'
        label(draw, (590, top+145), change, 412, 25, white, True)
        stamp = datetime.fromisoformat(row['date'])
        draw.text((72, top+207), f'Дата записи {stamp:%d.%m.%Y}', font=font(21), fill=muted)
        parts += [f'{index+1}. <b>{html.escape(company[:70])}</b> · {fmt(row["price"])} ₽/т',
                  html.escape((row['terminal']+' · '+row['region'])[:100]),
                  f'{change}. Запись {stamp:%d.%m.%Y}.', '']
    draw.line((48, 1191, 1032, 1191), fill='#416050', width=2)
    draw.text((50, 1210), 'Требования к качеству и оплату уточняйте.', font=font(24), fill=muted)
    draw.text((50, 1250), 'Подборка предложений · изменение с прошлой проверки', font=font(21), fill=muted)
    draw.text((50, 1292), '@worldgrain', font=font(30, True), fill=gold)
    parts += ['CPT · авто. НДС, качество и оплату уточняйте. Цены из записей площадки.', '',
              '🌾 <a href="https://t.me/worldgrain">Первый Зерновой</a> — следите за ценами']
    caption = '\n'.join(parts)
    length = len(html.unescape(re.sub('<[^>]+>', '', caption)).encode('utf-16-le')) // 2
    if length > 1024:
        raise ValueError('Подпись превысила лимит Telegram.')
    path = OUT / f'{crop}.png'
    image.save(path, optimize=True)
    (OUT/f'{crop}.html').write_text(caption, encoding='utf-8')
    (OUT/f'{crop}-details.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    return path, caption


def send(path, caption, token):
    boundary = 'Worldgrain' + uuid.uuid4().hex
    chunks = []
    fields = {'chat_id':'@worldgrain', 'caption':caption, 'parse_mode':'HTML', 'disable_notification':'true'}
    for key, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="card.png"\r\nContent-Type: image/png\r\n\r\n'.encode(),
               path.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()]
    try:
        request = Request(f'https://api.telegram.org/bot{token}/sendPhoto', data=b''.join(chunks),
                          headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
        with urlopen(request, timeout=45) as response:
            result = json.load(response)
        if result.get('ok'):
            return result['result']['message_id']
    except Exception:
        pass
    raise RuntimeError('Отправка не подтверждена. Проверь канал. pending блокирует повтор до ручной сверки.')


def main():
    now = datetime.now(ZONE)
    event = os.environ.get('GITHUB_EVENT_NAME')
    if event not in ('schedule', 'workflow_dispatch'):
        raise ValueError('Запускай через GitHub Actions.')
    preview = event == 'workflow_dispatch'
    if not preview and os.environ.get('GITHUB_RUN_ATTEMPT') != '1':
        print('Повтор запуска пропущен.')
        return
    crop = SLOTS.get(os.environ.get('SCHEDULE', ''))
    if not preview and crop is None:
        raise ValueError('Неизвестное расписание.')
    url = f'{os.environ["GITHUB_API_URL"]}/repos/{os.environ["GITHUB_REPOSITORY"]}/contents/.worldgrain-market-cards.json'
    headers = {'Authorization':'Bearer '+os.environ['GH_TOKEN'], 'Accept':'application/vnd.github+json'}
    branch = os.environ['GITHUB_REF_NAME']
    state = {'previous':{}, 'sent':{}, 'pending':None}
    sha = None
    try:
        stored = request_json(url+'?'+urlencode({'ref':branch}), headers=headers)
        sha = stored['sha']
        state = json.loads(base64.b64decode(stored['content']))
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f'GitHub HTTP {error.code}.') from None
    if not isinstance(state, dict) or not isinstance(state.get('previous'), dict) or not isinstance(state.get('sent'), dict):
        raise ValueError('Повреждён файл истории.')
    if not preview and state.get('pending'):
        raise RuntimeError('Есть незавершённая отправка pending. Требуется сверка с каналом.')
    for selected_crop in (list(CROPS) if preview else [crop]):
        slot = f'{now.date()}:{selected_crop}'
        if not preview and slot in state['sent']:
            print('Этот выпуск уже опубликован.')
            return
        name, quality = CROPS[selected_crop]
        source = 'https://api.silkagro.ru/api/prices/calc/?'+urlencode({'culture':name, 'sub_name':quality, 'basis':'CPT'})
        print('Источник для сверки:', source)
        rows = parse_rows(request_json(source), selected_crop, now.date())
        selected = select_rows(rows)
        if not selected:
            print(f'{name}: свежих подходящих записей нет. Выпуск пропущен.')
            continue
        path, caption = build(selected_crop, selected, state['previous'], now)
        print(caption)
        if preview:
            continue
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if not token:
            raise ValueError('Нет TELEGRAM_BOT_TOKEN.')

        def save(current_sha):
            body = {'branch':branch, 'message':'Update market card history',
                    'content':base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode()}
            if current_sha:
                body['sha'] = current_sha
            return request_json(url, 'PUT', headers, body)['content']['sha']

        state['pending'] = {'slot':slot, 'run_id':os.environ.get('GITHUB_RUN_ID'), 'selected':selected}
        sha = save(sha)
        message_id = send(path, caption, token)
        print(f'Опубликован пост №{message_id}', flush=True)
        state['pending'] = None
        state['sent'][slot] = message_id
        state['sent'] = dict(sorted(state['sent'].items())[-180:])
        by_crop = state.setdefault('keys_by_crop', {})
        for old_key in by_crop.get(selected_crop, []):
            state['previous'].pop(old_key, None)
        by_crop[selected_crop] = [row['key'] for row in rows]
        state['previous'].update({row['key']:row['price'] for row in rows})
        try:
            save(sha)
        except Exception:
            raise RuntimeError(f'Пост №{message_id} отправлен, история не подтверждена. Проверь pending.') from None
    if preview:
        print('Предпросмотр готов в Artifacts → market-preview. Отправки не было.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else f'Ошибка {type(error).__name__}. Проверь источник и GitHub.')
        raise SystemExit(1)
