"""Procedural agricultural posters. No external image or text generation services."""
import base64
import hashlib
import json
import math
import os
import random
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from PIL import Image, ImageDraw, ImageFilter
import market_cards as common

OUT = Path('grain-art-preview')
W, H = 1080, 1350
CROPS = [
    ('ПШЕНИЦА', 'Пшеница', (222,174,77), 'Золотой ритм полей.'),
    ('ПОДСОЛНЕЧНИК', 'Подсолнечник', (69,70,56), 'От солнечного поля до зернового терминала.'),
    ('ЧЕЧЕВИЦА', 'Чечевица', (193,105,56), 'Маленькое зерно — выразительная фактура.'),
    ('НУТ', 'Нут', (215,187,128), 'Своя форма. Свой характер.'),
    ('КУКУРУЗА', 'Кукуруза', (234,177,51), 'Янтарные оттенки урожая.'),
    ('ЛЁН', 'Лён', (141,83,44), 'Тёплый блеск семян и голубой цвет поля.'),
]
SERIES = ['КУЛЬТУРА КРУПНЫМ ПЛАНОМ', 'ПУТЬ ЗЕРНА', 'ЗЕРНОВОЙ ХАРАКТЕР']


def tone(color, multiplier):
    return tuple(max(0,min(255,int(c*multiplier))) for c in color)


def grain(canvas, rng, crop, x, y, size, angle):
    color = CROPS[crop][2]
    tile = Image.new('RGBA', (int(size*2.5)+8, int(size*2.5)+8))
    d = ImageDraw.Draw(tile)
    cx, cy = tile.width/2, tile.height/2
    rx, ry = size*.42, size*.69
    if crop in (2,3):
        rx, ry = size*.61, size*.54
    if crop == 4:
        rx, ry = size*.54, size*.59
    box = (cx-rx,cy-ry,cx+rx,cy+ry)
    d.ellipse((box[0]+5,box[1]+8,box[2]+5,box[3]+8),fill=(0,0,0,100))
    d.ellipse(box,fill=(*tone(color,rng.uniform(.85,1.12)),255))
    for i in range(8):
        inset=i*size*.025
        d.arc((box[0]+inset,box[1]+inset,box[2]-inset,box[3]-inset),200,290,
              fill=(*tone(color,1.35-i*.035),180),width=max(1,int(size*.025)))
    if crop in (0,1,5):
        d.line((cx,cy-ry*.73,cx-2,cy+ry*.76),fill=(*tone(color,.48),230),width=max(1,int(size*.045)))
    if crop == 1:
        for shift in [-.19,.19]:
            d.line((cx+size*shift,cy-ry*.7,cx+size*shift,cy+ry*.65),fill=(170,164,133,180),width=max(1,int(size*.025)))
    if crop == 3:
        d.polygon([(cx+rx*.5,cy-ry*.65),(cx+rx*1.16,cy-ry*.5),(cx+rx*.8,cy-ry*.1)],fill=(*color,255))
        d.arc((cx-rx*.7,cy-ry*.6,cx+rx*.7,cy+ry*.7),-70,75,fill=(*tone(color,.67),220),width=max(1,int(size*.035)))
    if crop == 4:
        d.rounded_rectangle((cx-rx*.65,cy-ry*.65,cx+rx*.65,cy+ry*.7),radius=max(2,int(size*.17)),outline=(*tone(color,.7),150),width=max(1,int(size*.025)))
    tile=tile.rotate(angle,resample=Image.Resampling.BICUBIC,expand=True)
    canvas.alpha_composite(tile,(int(x-tile.width/2),int(y-tile.height/2)))


def sky(rng):
    image=Image.new('RGBA',(W,H))
    draw=ImageDraw.Draw(image)
    top=(12,37,33)
    bottom=(45,77,61)
    for y in range(H):
        t=y/H
        draw.line((0,y,W,y),fill=tuple(int(a*(1-t)+b*t) for a,b in zip(top,bottom))+(255,))
    glow=Image.new('RGBA',(W,H))
    gd=ImageDraw.Draw(glow)
    gx=rng.randint(200,880)
    gd.ellipse((gx-300,240,gx+300,840),fill=(224,183,89,45))
    image=Image.alpha_composite(image,glow.filter(ImageFilter.GaussianBlur(100)))
    return image


def macro(image,rng,crop):
    for i in range(135):
        x=rng.uniform(-40,W+40)
        y=rng.uniform(340,1100)
        size=rng.uniform(25,92)
        grain(image,rng,crop,x,y,size,rng.uniform(-180,180))
    grain(image,rng,crop,550+rng.randint(-90,90),710,310,rng.uniform(-35,35))


def journey(image,rng,crop):
    d=ImageDraw.Draw(image)
    horizon=610+rng.randint(-40,40)
    sunx=rng.randint(190,850)
    d.ellipse((sunx-90,horizon-230,sunx+90,horizon-50),fill=(225,185,108,255))
    d.polygon([(0,horizon),(W,horizon-45),(W,1140),(0,1140)],fill=(113,115,68,255))
    for i in range(-9,19):
        d.polygon([(500+i*47,horizon),(520+i*47,horizon),(i*130+60,1150),(i*130-15,1150)],fill=(159,143,77,255))
    # Three logistical scenes rotate independently of the crop.
    scene=rng.randrange(3)
    if scene==0:
        d.polygon([(0,1030),(W,700),(W,815),(0,1160)],fill=(53,65,56,255))
        d.line((0,1094,W,760),fill=(213,198,145,255),width=4)
        tx=rng.randint(350,650)
        d.rounded_rectangle((tx-250,795,tx+80,919),radius=12,fill=(204,174,104,255))
        for x in range(tx-240,tx+80,30):d.line((x,808,x,909),fill=(139,117,70,255),width=3)
        d.polygon([(tx+90,825),(tx+170,825),(tx+210,872),(tx+210,935),(tx+90,935)],fill=(45,81,68,255))
        d.polygon([(tx+115,837),(tx+161,837),(tx+189,872),(tx+115,872)],fill=(169,191,168,255))
        for x in [tx-190,tx+30,tx+160]:
            d.ellipse((x-29,904,x+29,962),fill=(22,34,30,255));d.ellipse((x-12,921,x+12,945),fill=(166,170,151,255))
    elif scene==1:
        for i in range(5):
            x=170+i*140
            d.rectangle((x,horizon-105,x+112,horizon+180),fill=(156+i*6,164+i*4,144+i*3,255))
            d.polygon([(x,horizon-105),(x+56,horizon-150),(x+112,horizon-105)],fill=(211,196,143,255))
            for yy in range(horizon-80,horizon+180,25):d.line((x+3,yy,x+109,yy),fill=(104,125,110,255),width=2)
        d.line((150,horizon-155,910,horizon-155),fill=(207,188,133,255),width=12)
    else:
        d.rectangle((0,850,W,1130),fill=(39,85,81,255))
        for yy in range(870,1130,28):d.line((0,yy,W,yy-12),fill=(68,116,105,255),width=2)
        d.polygon([(170,900),(890,900),(820,1000),(260,1000)],fill=(24,44,39,255))
        for i in range(5):d.rectangle((270+i*95,820,355+i*95,900),fill=(175+i*8,146+i*7,80+i*4,255))
        for x in (270,730):
            d.line((x,795,x,540),fill=(189,184,145,255),width=10)
            d.line((x,540,x+170,620),fill=(189,184,145,255),width=8)
            d.line((x+170,620,x+170,800),fill=(189,184,145,255),width=3)
    for i in range(12):grain(image,rng,crop,rng.uniform(50,1030),rng.uniform(1050,1140),rng.uniform(18,35),rng.uniform(0,180))


def character(image,rng,crop):
    d=ImageDraw.Draw(image)
    cx=540+rng.randint(-60,60)
    cy=730
    turns=rng.uniform(2.5,4)
    for i in range(165):
        t=i/164
        angle=t*math.pi*2*turns
        radius=40+320*t
        x=cx+math.cos(angle)*radius
        y=cy+math.sin(angle)*radius*.87
        grain(image,rng,crop,x,y,24+37*t,math.degrees(angle)+80)
    d.ellipse((cx-70,cy-70,cx+70,cy+70),outline=(231,195,116,255),width=3)
    d.text((cx-22,cy-45),'1',font=common.font(70,True),fill=(231,195,116,255))
    if crop==5:
        for x,y in [(170,430),(870,950),(810,420)]:
            for a in range(5):
                xx=x+math.cos(a*math.pi*2/5)*23;yy=y+math.sin(a*math.pi*2/5)*23
                d.ellipse((xx-18,yy-18,xx+18,yy+18),fill=(124,169,202,255))
            d.ellipse((x-7,y-7,x+7,y+7),fill=(231,195,116,255))


def render(day,series,crop):
    seed=int(hashlib.sha256(f'{day}:{series}:{crop}:worldgrain-v1'.encode()).hexdigest()[:16],16)
    rng=random.Random(seed)
    image=sky(rng)
    [macro,journey,character][series](image,rng,crop)
    d=ImageDraw.Draw(image)
    # Stable text areas keep the artwork readable at phone size.
    d.rectangle((0,0,W,315),fill=(11,33,29,255))
    d.rectangle((0,1170,W,H),fill=(11,33,29,255))
    gold=(230,196,119,255);white=(244,242,232,255)
    d.rounded_rectangle((48,45,110,107),radius=14,fill=gold)
    d.text((67,49),'1',font=common.font(43,True),fill=(11,33,29,255))
    d.text((135,54),'ПЕРВЫЙ ЗЕРНОВОЙ',font=common.font(35,True),fill=white)
    common.label(d,(50,145),CROPS[crop][0],980,62,white,True)
    common.label(d,(52,245),SERIES[series],970,25,gold)
    d.line((50,1200,1030,1200),fill=(70,95,76,255),width=2)
    d.text((50,1228),'@worldgrain',font=common.font(35,True),fill=gold)
    d.text((50,1290),'АВТОРСКАЯ ИЛЛЮСТРАЦИЯ',font=common.font(20),fill=(163,185,170,255))
    OUT.mkdir(exist_ok=True)
    path=OUT/f'grain-art-{series+1}.png'
    image.convert('RGB').save(path,optimize=True)
    caption=(f'🌾 <b>{CROPS[crop][1]} · {SERIES[series].capitalize()}</b>\n\n'
             f'{CROPS[crop][3]}\n\nАвторская иллюстрация «Первого Зернового».\n\n'
             '🌾 <a href="https://t.me/worldgrain">Первый Зерновой</a> — цены, рынок и характер зерна')
    (OUT/f'grain-art-{series+1}.html').write_text(caption,encoding='utf-8')
    return path,caption


def main():
    now=datetime.now(common.ZONE)
    day=now.date().isoformat()
    event=os.environ.get('GITHUB_EVENT_NAME')
    if event not in ('schedule','workflow_dispatch'):
        raise ValueError('Запускай через GitHub Actions.')
    preview=event=='workflow_dispatch'
    if not preview and os.environ.get('GITHUB_RUN_ATTEMPT')!='1':
        print('Повтор запуска пропущен.');return
    index=(now.date()-datetime(2026,1,1).date()).days
    series=index%3
    crop=(index//3)%6
    if preview:
        for selected in range(3):render(day,selected,crop)
        print('Три примера готовы: Artifacts → grain-art-preview. Отправки не было.')
        return
    token=os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
    if not token:raise ValueError('Нет TELEGRAM_BOT_TOKEN.')
    url=f'{os.environ["GITHUB_API_URL"]}/repos/{os.environ["GITHUB_REPOSITORY"]}/contents/.worldgrain-art-state.json'
    headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'}
    branch=os.environ['GITHUB_REF_NAME']
    state={'sent':{},'pending':None}
    sha=None
    try:
        stored=common.request_json(url+'?'+urlencode({'ref':branch}),headers=headers)
        sha=stored['sha'];state=json.loads(base64.b64decode(stored['content']))
    except HTTPError as error:
        if error.code!=404:raise RuntimeError(f'GitHub HTTP {error.code}.') from None
    if not isinstance(state,dict) or not isinstance(state.get('sent'),dict):raise ValueError('Файл истории повреждён.')
    if state.get('pending'):raise RuntimeError('Есть pending. Проверь канал перед следующей публикацией.')
    if day in state['sent']:
        print('Сегодняшний пост уже опубликован.');return
    path,caption=render(day,series,crop)

    def save():
        payload={'branch':branch,'message':'Update generated art history',
                 'content':base64.b64encode(json.dumps(state,ensure_ascii=False,indent=2).encode()).decode()}
        if sha:payload['sha']=sha
        return common.request_json(url,'PUT',headers,payload)['content']['sha']

    state['pending']={'day':day,'series':series,'crop':crop,'run_id':os.environ.get('GITHUB_RUN_ID')}
    sha=save()
    message_id=common.send(path,caption,token)
    print(f'Опубликована иллюстрация №{message_id}',flush=True)
    state['pending']=None
    state['sent'][day]=message_id
    state['sent']=dict(sorted(state['sent'].items())[-90:])
    try:save()
    except Exception:raise RuntimeError(f'Пост №{message_id} отправлен, история не подтверждена. Проверь pending.') from None


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error) if isinstance(error,(ValueError,RuntimeError)) else f'Ошибка {type(error).__name__}. Проверь файлы и GitHub.')
        raise SystemExit(1)
