"""Collect compact daily observations and publish one weekly six-crop card."""
import base64
import hashlib
import html
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from PIL import Image, ImageDraw
import market_cards as common

OUT = Path('weekly-preview')
CROPS = {
    'Пшеница': ['Пшеница'],
    'Подсолнечник': ['Подсолнечник'],
    'Чечевица': ['Чечевица', 'Чечевица красная', 'Чечевица зеленая', 'Чечевица зелёная'],
    'Нут': ['Нут'],
    'Кукуруза': ['Кукуруза'],
    'Лён': ['Лён'],
}
COLLECT_CRON = '47 14 * * *'
WEEKLY_CRON = '17 15 * * 5'


def normalize(payload, expected, today):
    groups = payload if isinstance(payload, list) else payload.get('results')
    if not isinstance(groups, list):
        raise ValueError('Изменилась структура данных площадки.')
    observations = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('data'), list):
            raise ValueError('Нет массива data в ответе.')
        for row in group['data']:
            culture = row.get('culture') or {}
            if culture.get('name') not in expected:
                continue
            if row.get('market') is not False or row.get('auto') is not True:
                continue
            try:
                stamp = datetime.fromisoformat(row['dateTime']).date()
                price = Decimal(str(row['price']))
                if not price.is_finite() or not 0 < price <= 1_000_000:
                    continue
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
            if not 0 <= (today-stamp).days <= 3:
                continue
            warehouse = row.get('warehouseObj') or {}
            company = common.clean(row.get('companyInn') or row.get('company'))
            terminal = common.clean(warehouse.get('id') or row.get('warehouse'))
            if not company or not terminal:
                continue
            # A grade, quality requirements or transport change creates a new series.
            identity = [culture.get('name'), common.clean(culture.get('subName')),
                        company, terminal, common.clean(row.get('specs')),
                        row.get('railway'), 'CPT', 'VAT unknown']
            key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            value = [float(price), stamp.isoformat()]
            old = observations.get(key)
            if old and old[1] == value[1] and old[0] != value[0]:
                raise ValueError('Конфликт цен одной позиции за одну дату.')
            if old is None or value[1] > old[1]:
                observations[key] = value
    return observations


def collect(today):
    snapshot = {}
    for label, aliases in CROPS.items():
        merged = {}
        failed = False
        for alias in aliases:
            url = 'https://api.silkagro.ru/api/prices/calc/?' + urlencode(
                {'culture': alias, 'sub_name': '', 'basis': 'CPT'})
            try:
                values = normalize(common.request_json(url), aliases, today)
                for key, value in values.items():
                    old = merged.get(key)
                    if old and old[1] == value[1] and old[0] != value[0]:
                        raise ValueError('Конфликт цен вариантов названия культуры.')
                    if old is None or value[1] > old[1]:
                        merged[key] = value
            except Exception as error:
                failed = True
                print(f'{label}: запрос не выполнен ({type(error).__name__}).')
        # Failed requests are distinct from a successful empty result.
        snapshot[label] = {'status': 'error' if failed else 'ok', 'rows': merged}
        print(f'{label}: свежих наблюдений {len(merged)}, статус {snapshot[label]["status"]}.')
    return snapshot


def compare(old, current):
    result = {}
    for label in CROPS:
        before, after = old.get(label, {}), current.get(label, {})
        if before.get('status') != 'ok' or after.get('status') != 'ok':
            result[label] = {'status': 'Ошибка получения данных'}
            continue
        left, right = before.get('rows', {}), after.get('rows', {})
        keys = set(left) & set(right)
        if not keys:
            result[label] = {'status': 'Нет сопоставимых позиций'}
            continue
        changes = [round(right[key][0] - left[key][0], 2) for key in keys]
        result[label] = {'status': 'ok', 'up': sum(x > 0 for x in changes),
                         'down': sum(x < 0 for x in changes), 'same': sum(x == 0 for x in changes),
                         'total': len(changes)}
    return result


def render(stats, start, end, provisional=False):
    OUT.mkdir(exist_ok=True)
    image = Image.new('RGB', (1080, 1440), '#0D2722')
    draw = ImageDraw.Draw(image)
    gold, white, muted = '#E6C477', '#F4F2E8', '#ABC0B5'
    draw.text((48, 46), 'ПЕРВЫЙ ЗЕРНОВОЙ', font=common.font(38, True), fill=gold)
    draw.text((48, 120), 'КТО МЕНЯЕТ ЗАКУПКУ', font=common.font(43, True), fill=white)
    draw.text((50, 190), f'{start} — {end}', font=common.font(27), fill=muted)
    draw.text((50, 235), 'Наблюдаемые цены площадки · CPT · авто', font=common.font(24), fill=muted)
    parts = ['🌾 <b>Кто меняет закупку</b>', f'{start} — {end}', '']
    for index, (label, value) in enumerate(stats.items()):
        top = 300 + index * 160
        draw.rounded_rectangle((48, top, 1032, top+140), radius=18, fill='#183A31')
        draw.text((72, top+14), label.upper(), font=common.font(28, True), fill=white)
        if value['status'] == 'ok':
            up, down, same = value['up'], value['down'], value['same']
            draw.text((74, top+65), f'↑ {up}', font=common.font(32, True), fill=gold)
            draw.text((295, top+65), f'↓ {down}', font=common.font(32, True), fill='#F0A78E')
            draw.text((518, top+65), f'= {same}', font=common.font(32, True), fill=muted)
            draw.text((750, top+76), f'Всего {value["total"]}', font=common.font(22), fill=muted)
            parts.append(f'{label}: ↑ {up} · ↓ {down} · без изменений {same}.')
        else:
            common.label(draw, (74, top+72), value['status'], 920, 25, muted)
            parts.append(f'{label}: {value["status"].lower()}.')
    draw.text((50, 1280), '↑ рост · ↓ снижение · = без изменений', font=common.font(24), fill=muted)
    draw.text((50, 1325), 'Позиции с одинаковыми полями качества. НДС уточняется.', font=common.font(21), fill=muted)
    draw.text((50, 1380), '@worldgrain', font=common.font(29, True), fill=gold)
    if provisional:
        draw.text((690, 1388), 'НАКАПЛИВАЕМ ИСТОРИЮ', font=common.font(19), fill=muted)
    parts += ['', 'Одна позиция — покупатель, пункт приёмки и качество. По пшенице учитывается белок.',
              'Статистика выборки площадки. НДС уточняется.', '',
              '🌾 <a href="https://t.me/worldgrain">Первый Зерновой</a> — следите за рынком']
    caption = '\n'.join(parts)
    if len(html.unescape(re.sub('<[^>]+>', '', caption)).encode('utf-16-le')) // 2 > 1024:
        raise ValueError('Подпись превысила лимит Telegram.')
    path = OUT/'weekly.png'
    image.save(path, optimize=True)
    (OUT/'caption.html').write_text(caption, encoding='utf-8')
    (OUT/'statistics.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    return path, caption


def main():
    now = datetime.now(common.ZONE)
    today = now.date()
    event = os.environ.get('GITHUB_EVENT_NAME')
    cron = os.environ.get('SCHEDULE', '')
    if event not in ('schedule', 'workflow_dispatch'):
        raise ValueError('Запускай через GitHub Actions.')
    preview = event == 'workflow_dispatch'
    if not preview and cron not in (COLLECT_CRON, WEEKLY_CRON):
        raise ValueError('Неизвестное расписание.')
    if not preview and os.environ.get('GITHUB_RUN_ATTEMPT') != '1':
        print('Повтор запуска пропущен.'); return
    url = f'{os.environ["GITHUB_API_URL"]}/repos/{os.environ["GITHUB_REPOSITORY"]}/contents/.worldgrain-weekly-history.json'
    headers = {'Authorization': 'Bearer '+os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'}
    branch = os.environ['GITHUB_REF_NAME']
    state = {'days': {}, 'sent': {}, 'pending': None}
    sha = None
    try:
        stored = common.request_json(url+'?'+urlencode({'ref': branch}), headers=headers)
        sha = stored['sha']
        state = json.loads(base64.b64decode(stored['content']))
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f'GitHub HTTP {error.code}.') from None
    if not isinstance(state.get('days'), dict) or not isinstance(state.get('sent'), dict):
        raise ValueError('Файл истории повреждён.')

    def save():
        body = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()
        if len(body) > 900_000:
            raise ValueError('История превысила лимит. Нужна настройка хранения.')
        payload = {'branch': branch, 'message': 'Update weekly crop observations',
                   'content': base64.b64encode(body).decode()}
        if sha:
            payload['sha'] = sha
        return common.request_json(url, 'PUT', headers, payload)['content']['sha']

    # Always fetch today's data, even if the daily collection job was delayed.
    snapshot = collect(today)
    if not any(x['status'] == 'ok' and x['rows'] for x in snapshot.values()):
        raise RuntimeError('Свежих данных нет ни по одной культуре. История не записана.')
    day = today.isoformat()
    if not preview:
        existing = state['days'].get(day, {})
        for label, value in snapshot.items():
            if value['status'] == 'error' and existing.get(label, {}).get('status') == 'ok':
                snapshot[label] = existing[label]
        state['days'][day] = snapshot
        cutoff = (today-timedelta(days=14)).isoformat()
        state['days'] = {key: value for key, value in state['days'].items() if key >= cutoff}
        sha = save()
        print('Ежедневный архив сохранён.')
        if cron == COLLECT_CRON:
            return
    candidates = []
    for day_key in state['days']:
        age = (today-datetime.fromisoformat(day_key).date()).days
        if 6 <= age <= 8:
            candidates.append((abs(age-7), day_key))
    baseline = min(candidates)[1] if candidates else None
    if baseline:
        stats = compare(state['days'][baseline], snapshot)
        start = datetime.fromisoformat(baseline).strftime('%d.%m.%Y')
    else:
        stats = {label: {'status': 'Накапливаем историю'} for label in CROPS}
        start = 'Начало наблюдений'
    path, caption = render(stats, start, today.strftime('%d.%m.%Y'), not baseline)
    if preview:
        print('Предпросмотр готов. Канал и архив не изменены.')
        return
    if not baseline or not any(x['status'] == 'ok' for x in stats.values()):
        print('Недостаточно сопоставимой истории за 6–8 дней. Недельный выпуск пропущен.')
        return
    week = f'{today.isocalendar().year}-W{today.isocalendar().week:02}'
    if state.get('pending'):
        raise RuntimeError('Есть pending — проверь канал перед следующей отправкой.')
    if week in state['sent']:
        print('Недельный выпуск уже отправлен.'); return
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise ValueError('Нет TELEGRAM_BOT_TOKEN.')
    state['pending'] = {'week': week, 'run_id': os.environ.get('GITHUB_RUN_ID')}
    sha = save()
    message_id = common.send(path, caption, token)
    print(f'Опубликован недельный пост №{message_id}', flush=True)
    state['pending'] = None
    state['sent'][week] = message_id
    state['sent'] = dict(sorted(state['sent'].items())[-20:])
    try:
        save()
    except Exception:
        raise RuntimeError(f'Пост №{message_id} отправлен. Сохранение истории не подтверждено, проверь pending.') from None


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, (ValueError, RuntimeError)) else f'Ошибка {type(error).__name__}. Проверь источник и GitHub.')
        raise SystemExit(1)
