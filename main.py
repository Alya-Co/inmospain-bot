import os, io, re, json, requests, shutil
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

app = Flask(__name__)

VERIFY_TOKEN      = os.environ.get("VERIFY_TOKEN", "inmospain2025")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT     = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
SCRAPER_KEY       = os.environ.get("SCRAPER_API_KEY", "")

# ─── ШРИФТЫ ───────────────────────────────────────────────────────────────────
F  = "MyFont"
FB = "MyFont-Bold"

def setup_fonts():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_dir  = os.path.join(base_dir, "fonts")
    paths = {
        "reg":  os.path.join(font_dir, "reg.ttf"),
        "bold": os.path.join(font_dir, "bold.ttf"),
        "ital": os.path.join(font_dir, "reg.ttf"),
        "bi":   os.path.join(font_dir, "bold.ttf"),
    }
    if not os.path.exists(paths["reg"]):
        sys_base = "/usr/share/fonts/truetype/liberation/LiberationSans"
        paths = {
            "reg":  f"{sys_base}-Regular.ttf",
            "bold": f"{sys_base}-Bold.ttf",
            "ital": f"{sys_base}-Italic.ttf",
            "bi":   f"{sys_base}-BoldItalic.ttf",
        }
    try:
        pdfmetrics.registerFont(TTFont(F,           paths["reg"]))
        pdfmetrics.registerFont(TTFont(FB,          paths["bold"]))
        pdfmetrics.registerFont(TTFont(F+"-Italic", paths["ital"]))
        pdfmetrics.registerFont(TTFont(FB+"-BI",    paths["bi"]))
        registerFontFamily(F, normal=F, bold=FB, italic=F+"-Italic", boldItalic=FB+"-BI")
        print(f"Fonts OK")
    except Exception as e:
        print(f"Font error: {e}")

setup_fonts()

# ─── СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ ──────────────────────────────────────────────────
# state = {
#   "lang": "ru/en/es",
#   "step": "await_link|processing|after_report|collect_type|collect_budget|collect_area|collect_name|talking_to_agent|done",
#   "reports_today": 0,
#   "last_report_date": "2026-05-26",
#   "report_count": 0,
#   "criteria": {"type": None, "budget": None, "area": None, "name": None}
# }
user_states = {}

def get_state(sid):
    return user_states.get(sid, {})

def set_state(sid, **kwargs):
    if sid not in user_states:
        user_states[sid] = {}
    user_states[sid].update(kwargs)

def get_reports_today(sid):
    state = get_state(sid)
    today = date.today().isoformat()
    if state.get("last_report_date") != today:
        set_state(sid, reports_today=0, last_report_date=today)
        return 0
    return state.get("reports_today", 0)

def increment_reports(sid):
    today = date.today().isoformat()
    count = get_reports_today(sid) + 1
    set_state(sid, reports_today=count, last_report_date=today,
              report_count=get_state(sid).get("report_count", 0) + 1)
    return count

# ─── ТЕКСТЫ ───────────────────────────────────────────────────────────────────
T = {
    "ask": {
        "en": "Send me an Idealista link or property address and I will generate a full AI report in 2-3 minutes.\n\nExample:\nhttps://www.idealista.com/inmueble/...\nOr: Calle Mayor 5, Torrevieja",
        "ru": "Пришлите ссылку с Idealista или адрес объекта — подготовлю полный AI-отчёт за 2-3 минуты.\n\nПример:\nhttps://www.idealista.com/inmueble/...\nИли: Calle Mayor 5, Torrevieja",
        "es": "Enviame un enlace de Idealista o la direccion del inmueble — informe completo en 2-3 minutos.\n\nEjemplo:\nhttps://www.idealista.com/inmueble/...",
    },
    "wait": {
        "en": "Analyzing the property... Please wait 2-3 minutes",
        "ru": "Анализирую объект... Подождите 2-3 минуты",
        "es": "Analizando el inmueble... Por favor espera 2-3 minutos",
    },
    "after_report": {
        "en": "Your AI report is ready! What would you like to do next?",
        "ru": "Ваш AI-отчёт готов! Что дальше?",
        "es": "Tu informe esta listo! Que deseas hacer?",
    },
    "limit_reached": {
        "en": "You have used your 3 free reports today.\n\nThe best properties in Spain sell in 2-3 days and don't always make it to Idealista. Our agent can send you hot deals personally.\n\nWould you like to be the first to know?",
        "ru": "Вы использовали 3 бесплатных отчёта сегодня.\n\nЛучшие объекты уходят за 2-3 дня и не всегда попадают на Idealista. Агент может присылать вам горячие предложения лично.\n\nХотите получать их первыми?",
        "es": "Has usado tus 3 informes gratuitos hoy.\n\nLos mejores inmuebles se venden en 2-3 dias y no siempre llegan a Idealista. Nuestro agente puede enviarte ofertas exclusivas.\n\n¿Quieres recibirlas?",
    },
    "hot_offer": {
        "en": "The best properties in the area sell in 2-3 days and don't always make it to Idealista. Our agent can send you hot deals personally.\n\nWould you like to be the first to know?",
        "ru": "Лучшие объекты в районе уходят за 2-3 дня и не всегда попадают на Idealista. Агент может присылать вам горячие предложения лично.\n\nХотите получать их первыми?",
        "es": "Los mejores inmuebles de la zona se venden en 2-3 dias y no siempre llegan a Idealista. Nuestro agente puede enviarte ofertas exclusivas.\n\n¿Quieres recibirlas?",
    },
    "yes_connect": {
        "en": "We will connect you to our daily hot deals newsletter after our agent contacts you.\n\nMeanwhile, please describe your property criteria:",
        "ru": "Мы подключим вас к ежедневной рассылке горячих объектов после того, как агент свяжется с вами.\n\nА пока опишите критерии выбора недвижимости:",
        "es": "Te conectaremos al boletin diario de inmuebles despues de que el agente te contacte.\n\nMientras tanto, describe tus criterios:",
    },
    "ask_type": {
        "en": "What are you looking for?",
        "ru": "Что ищете?",
        "es": "Que tipo de inmueble buscas?",
    },
    "ask_budget": {
        "en": "What is your budget?",
        "ru": "Ваш бюджет?",
        "es": "Cual es tu presupuesto?",
    },
    "ask_area": {
        "en": "Preferred area?",
        "ru": "Предпочтительный район?",
        "es": "Zona preferida?",
    },
    "ask_name": {
        "en": "And finally — what is your name?",
        "ru": "И последнее — как вас зовут?",
        "es": "Y por ultimo — como te llamas?",
    },
    "thanks": {
        "en": "Thank you! Our agent will contact you within 2 hours.",
        "ru": "Спасибо! Агент свяжется с вами в течение 2 часов.",
        "es": "Gracias! Nuestro agente te contactara en 2 horas.",
    },
    "wait_24h": {
        "en": "No problem! Your next 3 free reports will be available in 24 hours. See you tomorrow!",
        "ru": "Хорошо! Следующие 3 бесплатных отчёта будут доступны через 24 часа. До завтра!",
        "es": "Sin problema! Tus proximos 3 informes gratuitos estaran disponibles en 24 horas.",
    },
    "invalid": {
        "en": "Please send an Idealista link or a full property address",
        "ru": "Пришлите ссылку с Idealista или полный адрес объекта",
        "es": "Por favor enviame un enlace de Idealista o la direccion completa",
    },
    "agent_talking": {
        "en": "Our agent will be in touch with you shortly!",
        "ru": "Наш агент скоро свяжется с вами!",
        "es": "Nuestro agente se pondra en contacto contigo pronto!",
    },
}

TYPES = {
    "en": ["House", "Apartment", "Duplex", "Land", "Garage"],
    "ru": ["Дом", "Квартира", "Дуплекс", "Земля", "Гараж"],
    "es": ["Casa", "Piso", "Duplex", "Terreno", "Garaje"],
}

BUDGETS = {
    "en": ["up to €100k", "€100-150k", "€150-250k", "€250k+"],
    "ru": ["до €100k", "€100-150k", "€150-250k", "€250k+"],
    "es": ["hasta €100k", "€100-150k", "€150-250k", "€250k+"],
}

AREAS = {
    "en": ["Torrevieja", "Orihuela Costa", "Alicante", "Other"],
    "ru": ["Торревьеха", "Ориуэла Коста", "Аликанте", "Другой"],
    "es": ["Torrevieja", "Orihuela Costa", "Alicante", "Otro"],
}

# ─── MESSENGER ────────────────────────────────────────────────────────────────
def send_msg(rid, text):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"text":text}},
        timeout=10)

def send_buttons(rid, text, buttons):
    """Отправляем сообщение с кнопками (postback)"""
    payload_buttons = [{"type":"postback","title":b["title"],"payload":b["payload"]} for b in buttons[:3]]
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"attachment":{"type":"template","payload":{
            "template_type":"button",
            "text":text,
            "buttons":payload_buttons
        }}}},
        timeout=10)

def send_quick_replies(rid, text, replies):
    """Quick reply кнопки"""
    qr = [{"content_type":"text","title":r,"payload":r} for r in replies[:11]]
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"text":text,"quick_replies":qr}},
        timeout=10)

def send_lang_buttons(rid):
    send_buttons(rid,
        "Hi! / Privet! / Hola!\n\nPlease choose your language:",
        [{"title":"English","payload":"LANG_EN"},
         {"title":"Русский","payload":"LANG_RU"},
         {"title":"Español","payload":"LANG_ES"}])

def send_after_report_buttons(rid, lang):
    send_buttons(rid,
        T["after_report"][lang],
        [{"title":"Ещё объект" if lang=="ru" else "Check another" if lang=="en" else "Otro inmueble",
          "payload":"ANOTHER_REPORT"},
         {"title":"Хочу консультацию" if lang=="ru" else "Talk to agent" if lang=="en" else "Hablar con agente",
          "payload":"WANT_AGENT"}])

def send_hot_offer_buttons(rid, lang):
    send_buttons(rid,
        T["hot_offer"][lang],
        [{"title":"Да, хочу первым!" if lang=="ru" else "Yes, send me!" if lang=="en" else "Si, quiero!",
          "payload":"HOT_YES"},
         {"title":"Нет, подожду 24ч" if lang=="ru" else "No, wait 24h" if lang=="en" else "No, espero",
          "payload":"HOT_NO"}])

def send_limit_buttons(rid, lang):
    send_buttons(rid,
        T["limit_reached"][lang],
        [{"title":"Да, хочу первым!" if lang=="ru" else "Yes, send me!" if lang=="en" else "Si, quiero!",
          "payload":"HOT_YES"},
         {"title":"Подожду 24 часа" if lang=="ru" else "Wait 24 hours" if lang=="en" else "Esperar 24h",
          "payload":"HOT_NO"}])

def send_pdf(rid, buf, fname="InmoSpain_Report.pdf"):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        data={"recipient":json.dumps({"id":rid}),
              "message":json.dumps({"attachment":{"type":"file","payload":{"is_reusable":False}}})},
        files={"filedata":(fname, buf, "application/pdf")},
        timeout=30)

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def notify_tg(sender_id, name, msg, lang, is_hot=False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    emoji = {"en":"🇬🇧","ru":"🇷🇺","es":"🇪🇸"}.get(lang,"🌍")
    prefix = "🔥 *ГОРЯЧИЙ ЛИД*" if is_hot else "🏠 *Новый лид*"
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT,
        "text": f"{prefix} InmoSpain\n\n👤 {name}\n{emoji} {lang}\n💬 {msg}\n\n👉 https://m.me/{sender_id}",
        "parse_mode": "Markdown"}, timeout=10)

def notify_hot_lead(sender_id, name, lang, criteria):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    emoji = {"en":"🇬🇧","ru":"🇷🇺","es":"🇪🇸"}.get(lang,"🌍")
    text = (
        f"🔥 *ГОРЯЧИЙ ЛИД* InmoSpain\n\n"
        f"👤 {criteria.get('name', name)}\n"
        f"{emoji} Язык: {lang}\n"
        f"🏠 Тип: {criteria.get('type','N/A')}\n"
        f"💰 Бюджет: {criteria.get('budget','N/A')}\n"
        f"📍 Район: {criteria.get('area','N/A')}\n\n"
        f"👉 https://m.me/{sender_id}\n\n"
        f"После звонка нажмите кнопку ниже:"
    )
    # Отправляем с инлайн кнопкой подтверждения
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Подтвердить рассылку", "callback_data": f"confirm_{sender_id}"},
            {"text": "❌ Отклонить", "callback_data": f"reject_{sender_id}"}
        ]]}
    }, timeout=10)

# ─── APIs ─────────────────────────────────────────────────────────────────────
def _get(url, params=None, timeout=12):
    hdrs = {"User-Agent":"InmoSpainBot/1.0 (experthomespain@gmail.com)"}
    try:
        r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        if r.status_code == 200: return r
    except: pass
    return None

def get_coordinates(address):
    r = _get("https://nominatim.openstreetmap.org/search",
             {"q":address+", Spain","format":"json","limit":1})
    if r:
        d = r.json()
        if d: return float(d[0]["lat"]), float(d[0]["lon"])
    return 37.9792, -0.6840

def get_catastro(address):
    hdrs = {"User-Agent":"Mozilla/5.0"}
    try:
        r = requests.get(
            "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCallejero.svc/json/Consulta_DNPPP",
            params={"situ":address}, headers=hdrs, timeout=10)
        if r.status_code == 200:
            d = r.json()
            lrcd = d.get("consulta_dnpppResult",{}).get("lrcdnp",{}).get("rcdnp",{})
            if isinstance(lrcd, list): lrcd = lrcd[0]
            debi = lrcd.get("debi",{})
            return {"ref":lrcd.get("rc",{}).get("pc1","")[:8]+"...",
                    "ano":debi.get("ant","N/A"),"sup":debi.get("sfc","N/A"),
                    "uso":debi.get("luso","N/A")}
    except: pass
    return {"ref":"N/A","ano":"N/A","sup":"N/A","uso":"N/A"}

def get_solar(lat, lon):
    r = _get("https://re.jrc.ec.europa.eu/api/v5_2/PVcalc",
             {"lat":lat,"lon":lon,"peakpower":5,"loss":14,"outputformat":"json"})
    if r:
        try:
            kwh = round(r.json()["outputs"]["totals"]["fixed"]["E_y"])
            return {"kwh":kwh,"savings":round(kwh*0.15),
                    "rating":"Отличный" if kwh>7000 else "Хороший"}
        except: pass
    return {"kwh":6800,"savings":1020,"rating":"Хороший"}

def get_parking(lat, lon):
    q = f'[out:json][timeout:10];(node["amenity"="parking"](around:500,{lat},{lon});way["amenity"="parking"](around:500,{lat},{lon}););out count;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=q,
                          headers={"User-Agent":"InmoSpainBot/1.0"}, timeout=15)
        if r.status_code == 200:
            els = r.json().get("elements",[])
            cnt = int(els[0].get("tags",{}).get("total",len(els))) if els else 0
            return {"count":cnt,"status":"Есть рядом" if cnt>0 else "Не найдено"}
    except: pass
    return {"count":"N/A","status":"Данные недоступны"}

def get_distance_sea(lat, lon):
    q = f'[out:json][timeout:10];node["natural"="beach"](around:5000,{lat},{lon});out 1;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=q,
                          headers={"User-Agent":"InmoSpainBot/1.0"}, timeout=12)
        if r.status_code == 200:
            els = r.json().get("elements",[])
            if els:
                import math
                d = math.sqrt((lat-els[0]["lat"])**2+(lon-els[0]["lon"])**2)*111
                return f"{round(d*1000)} м"
    except: pass
    return "N/A"

def parse_idealista(url):
    empty = {"precio":None,"area":None,"area_util":None,"parcela":None,
             "habitaciones":None,"banos":None,"ano":None,"orientacion":None,
             "garaje":False,"piscina":False,"jardin":False,"comunidad":None,"barrio":"N/A"}
    html = ""
    try:
        if SCRAPER_KEY:
            r = requests.get(f"http://api.scraperapi.com?api_key={SCRAPER_KEY}&url={url}&render=true", timeout=30)
        else:
            hdrs = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language":"es-ES,es;q=0.9"}
            r = requests.get(url, headers=hdrs, timeout=15)
        if r.status_code == 200: html = r.text
    except Exception as e:
        print(f"Fetch error: {e}")

    d = dict(empty)
    if html:
        try:
            patterns = {
                "precio":       r'"price"\s*:\s*(\d+)',
                "area":         r'(\d+)\s*m²\s*construidos',
                "area_util":    r'(\d+)\s*m²\s*[uú]tiles',
                "parcela":      r'[Pp]arcela\s+de\s+(\d+)\s*m²',
                "habitaciones": r'(\d+)\s+habitacion',
                "banos":        r'(\d+)\s+ba[nñ]o',
                "ano":          r'[Cc]onstruido en (\d{4})',
                "orientacion":  r'[Oo]rientaci[oó]n\s+(norte|sur|este|oeste|noreste|noroeste|sureste|suroeste)',
                "comunidad":    r'[Gg]astos.*?(\d+).*?mes',
            }
            for key, pat in patterns.items():
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    val = m.group(1)
                    d[key] = val.capitalize() if key=="orientacion" else int(val)
            d["garaje"]  = bool(re.search(r'garaje|plaza.*garage', html, re.IGNORECASE))
            d["piscina"] = bool(re.search(r'piscina', html, re.IGNORECASE))
            d["jardin"]  = bool(re.search(r'jard[ií]n', html, re.IGNORECASE))
            m = re.search(r'"neighborhood"\s*:\s*"([^"]{3,50})"', html)
            if m: d["barrio"] = m.group(1).strip()
        except Exception as e:
            print(f"Regex error: {e}")

    # Claude Vision если данных нет
    if ANTHROPIC_KEY and html and (not d.get("precio") or not d.get("area")):
        try:
            prompt = f"""Из HTML страницы Idealista извлеки данные. Верни ТОЛЬКО JSON:
{{"precio":число,"area":число,"area_util":число,"parcela":число,"habitaciones":число,"banos":число,"ano":число,"orientacion":"Sur/Norte/etc","garaje":true/false,"piscina":true/false,"jardin":true/false,"comunidad":число,"barrio":"название"}}
HTML (первые 8000 символов):
{html[:8000]}"""
            resp = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":"claude-haiku-4-5-20251001","max_tokens":500,
                      "messages":[{"role":"user","content":prompt}]}, timeout=30)
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"]
                start = text.find("{"); end = text.rfind("}")+1
                if start >= 0 and end > start:
                    parsed = json.loads(text[start:end])
                    for k, v in parsed.items():
                        if k in d and (d[k] is None or d[k] == "N/A" or d[k] is False):
                            d[k] = v
                    print(f"Claude: precio={d.get('precio')}, area={d.get('area')}")
        except Exception as e:
            print(f"Claude error: {e}")
    return d

def calc_income(precio, area_util):
    rate = 9.5
    area = area_util or 70
    monthly = area * rate
    gross = monthly * 12 * 0.78
    p = precio or 200000
    ibi = p * 0.018
    com = 1200
    maint = gross * 0.05
    ins = 500
    total_exp = ibi + com + maint + ins
    net = gross - total_exp
    roi = (net/p)*100
    payback = p/net if net>0 else 0
    return {"monthly":round(monthly),"gross":round(gross),"net":round(net),
            "roi":round(roi,1),"payback":round(payback,1),"rate":rate,
            "occupancy":78,"ibi":round(ibi),"com":com,
            "maint":round(maint),"ins":ins,"total_exp":round(total_exp)}

# ─── PDF ──────────────────────────────────────────────────────────────────────
def generate_pdf(data):
    R = F; B = FB

    def S(nm, bold=False, size=10, color="#212121", align=TA_LEFT, **kw):
        return ParagraphStyle(nm, fontName=B if bold else R, fontSize=size,
                              textColor=colors.HexColor(color), alignment=align, **kw)
    def P(txt, sty): return Paragraph(str(txt), sty)

    def row(label, value, story):
        t = Table([[P(label,lbl_s), P(str(value),val_s)]], colWidths=[9*cm,7*cm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),0),
            ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#eceff1"))]))
        story.append(t)

    def section(title, story):
        story.append(HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#bbdefb"),spaceAfter=2))
        story.append(P(title, sec_s))

    def risk_row(level, color_hex, title, text, story):
        lvl_s = S(f"rk{level}",bold=True,size=8,color="#ffffff",align=TA_CENTER)
        badge = Table([[P(level,lvl_s)]],colWidths=[1.8*cm])
        badge.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor(color_hex)),
                                   ("PADDING",(0,0),(-1,-1),3)]))
        cont = Table([[P(title,rlbl_s)],[P(text,rtxt_s)]],colWidths=[14*cm])
        t = Table([[badge,cont]],colWidths=[2*cm,14*cm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
                               ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
        story.append(t)

    title_s = S("ti",bold=True,size=17,color="#1a237e",align=TA_CENTER,spaceAfter=2)
    addr_s  = S("ad",size=10,color="#37474f",align=TA_CENTER,spaceAfter=2)
    meta_s  = S("me",size=8,color="#78909c",align=TA_CENTER,spaceAfter=6)
    stat_s  = S("st",bold=True,size=10,color="#ffffff",align=TA_CENTER)
    sec_s   = S("se",bold=True,size=11,color="#1a237e",spaceBefore=10,spaceAfter=3)
    lbl_s   = S("lb",size=9,color="#546e7a")
    val_s   = S("va",bold=True,size=9,color="#212121")
    kpi_l   = S("kl",size=8,color="#78909c",align=TA_CENTER)
    kpi_v   = S("kv",bold=True,size=15,color="#1a237e",align=TA_CENTER)
    kpi_s   = S("ks",size=8,color="#90a4ae",align=TA_CENTER)
    note_s  = S("no",size=7.5,color="#9e9e9e",leftIndent=5,spaceAfter=3)
    rlbl_s  = S("rl",bold=True,size=9,color="#37474f")
    rtxt_s  = S("rt",size=8.5,color="#546e7a",spaceAfter=3)
    score_s = S("sc",bold=True,size=34,color="#1a237e",align=TA_CENTER)
    ssub_s  = S("ss",size=9,color="#78909c",align=TA_CENTER)
    svrd_s  = S("sv",bold=True,size=10,color="#37474f",align=TA_CENTER)
    srec_s  = S("sr",size=9,color="#546e7a",align=TA_CENTER)
    disc_s  = S("di",size=7.5,color="#9e9e9e",align=TA_JUSTIFY)

    idal = data.get("idealista",{})
    cat  = data.get("catastro",{})
    sol  = data.get("solar",{})
    park = data.get("parking",{})
    inc  = data.get("income",{})
    dist_sea = data.get("dist_sea","N/A")

    precio    = idal.get("precio") or 0
    area      = idal.get("area") or cat.get("sup","N/A")
    area_util = idal.get("area_util","N/A")
    parcela   = idal.get("parcela","N/A")
    ano       = idal.get("ano") or cat.get("ano","N/A")
    orient    = idal.get("orientacion","N/A")
    garaje    = "Да" if idal.get("garaje") else "N/A"
    piscina   = "Да" if idal.get("piscina") else "N/A"
    precio_m2 = round(precio/int(area)) if precio and str(area).isdigit() else "N/A"
    merc_m2   = 2650
    diff      = round((precio_m2-merc_m2)/merc_m2*100,1) if isinstance(precio_m2,int) else None
    ano_int   = int(ano) if str(ano).isdigit() else None
    score     = 72 if diff is None or diff<15 else 60

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=1.8*cm, leftMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    story.append(P("InmoSpain AI Report", title_s))
    story.append(P(data.get("address",""), addr_s))
    story.append(P(f"Ref. catastral: {cat.get('ref','N/A')}  |  Отчёт: {data.get('fecha','')}  |  Источник: Idealista + Catastro + PVGIS + OSM", meta_s))
    st_tbl = Table([[P("Статус: Сформирован автоматически | Требует проверки агентом", stat_s)]],colWidths=[16*cm])
    st_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#37474f")),("PADDING",(0,0),(-1,-1),6)]))
    story.append(st_tbl)
    story.append(Spacer(1,0.3*cm))

    age_str = f"{2026-ano_int} лет" if ano_int else "N/A"
    kpi_tbl = Table([
        [P("Цена продажи",kpi_l),P("Кадастр. стоимость",kpi_l),P("Площадь",kpi_l),P("Год постройки",kpi_l)],
        [P(f"€{precio:,}" if precio else "N/A",kpi_v),P(cat.get("valor","N/A"),kpi_v),P(f"{area} м²",kpi_v),P(str(ano),kpi_v)],
        [P(f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A",kpi_s),P("—",kpi_s),P("по кадастру",kpi_s),P(age_str,kpi_s)],
    ],colWidths=[4*cm,4*cm,4*cm,4*cm])
    kpi_tbl.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#e3f2fd")),
        ("LINEBELOW",(0,1),(-1,1),0.3,colors.HexColor("#e3f2fd")),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    story.append(kpi_tbl)
    story.append(Spacer(1,0.2*cm))

    section("Кадастровые данные", story)
    row("Площадь по кадастру", f"{area} м²", story)
    row("Площадь по снимку (AI)", "N/A — аэрофото не подключено", story)
    row("Полезная площадь", f"{area_util} м²" if area_util!="N/A" else "N/A", story)
    row("Площадь участка", f"{parcela} м²" if parcela!="N/A" else "N/A", story)
    row("Тип объекта", idal.get("tipo","N/A"), story)
    row("Использование", cat.get("uso","Residencial"), story)
    row("Незарегистрированные постройки", "N/A — требует аэрофото PNOA", story)

    section("Анализ цены", story)
    row("Медиана по барио", f"€{merc_m2}/м²", story)
    row("Цена объекта", f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A", story)
    if diff is not None:
        sign = "+" if diff>0 else ""
        verdict = "выше рынка" if diff>0 else "ниже рынка"
        rec = f"€{round(precio*0.94):,} – €{round(precio*0.96):,}" if precio else "N/A"
        row("Отклонение от рынка", f"{sign}{diff}% ({verdict})", story)
        row("Рекомендуемый торг", rec, story)
    row("Аналогичных объектов рядом", "N/A", story)
    row("Среднее время на рынке", "N/A", story)

    section("Состояние по аэрофото (PNOA 2024)", story)
    row("Крыша здания", "N/A — аэрофото не подключено", story)
    row("Фасад", "N/A — аэрофото не подключено", story)
    row("Изменения 2015 → 2024", "N/A — аэрофото не подключено", story)
    row("Гараж / Парковка", garaje, story)
    story.append(P("* Подключение аэрофото IGN PNOA — следующий этап разработки.", note_s))

    section("Окружение", story)
    row("До моря", dist_sea, story)
    row("До центра города", "N/A", story)
    row("Рядом", "Бары, рестораны, супермаркеты, аптеки", story)
    row("Промышленные объекты рядом", "N/A", story)
    row("Шумовые источники", "N/A", story)

    section("Солнечный потенциал (PVGIS)", story)
    row("Ориентация крыши", f"{orient}", story)
    row("Годовая радиация", "1 820 кВт·ч/м² (Costa Blanca)", story)
    row("Потенциал (5 кВт система)", f"~{sol.get('kwh','N/A')} кВт·ч/год", story)
    row("Экономия на электричестве", f"~€{sol.get('savings','N/A')}/год", story)
    row("Оценка потенциала", sol.get("rating","N/A"), story)
    row("Тень от соседних зданий", "N/A", story)
    story.append(P("* PVGIS (Европейская комиссия), система 5 кВт. Тариф €0.15/кВт·ч.", note_s))

    section("Парковка поблизости", story)
    row("Гараж в объекте", garaje, story)
    row("Парковок в радиусе 500м (OSM)", str(park.get("count","N/A")), story)
    row("Итого", park.get("status","N/A"), story)
    story.append(P("* OpenStreetMap. Частные/подземные могут не отображаться.", note_s))

    section("Инвестиционный анализ", story)
    row("Примерная месячная аренда", f"€{inc.get('monthly','N/A')}", story)
    row(f"Валовый доход в год ({inc.get('occupancy','N/A')}% загрузка)", f"€{inc.get('gross','N/A')}", story)
    row("Чистый доход в год (после расходов)", f"€{inc.get('net','N/A')}", story)
    row("Доходность (ROI)", f"{inc.get('roi','N/A')}%", story)
    row("Срок окупаемости", f"{inc.get('payback','N/A')} лет", story)
    story.append(P(
        f"* Как считали: €{inc.get('rate','N/A')}/м²/мес x {area_util or area} м² x 12 x "
        f"{inc.get('occupancy','N/A')}% загрузка = €{inc.get('gross','N/A')} валовый. "
        f"Расходы: IBI €{inc.get('ibi','N/A')} + коммунальные €{inc.get('com','N/A')} + "
        f"обслуживание €{inc.get('maint','N/A')} + страховка €{inc.get('ins','N/A')} = "
        f"€{inc.get('total_exp','N/A')}/год. Приблизительный расчёт.", note_s))

    section("Риски", story)
    story.append(Spacer(1,0.15*cm))
    if diff is not None and diff>5:
        risk_row("Средний","#f57c00","Цена выше рынка",f"+{diff}% выше медианы. Рекомендуем {rec}.", story)
    else:
        risk_row("Низкий","#388e3c","Цена","Цена соответствует рынку или ниже.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Расхождение площади","N/A — нет аэрофото для сравнения.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Крыша требует осмотра","N/A — нет аэрофото PNOA.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Зона затопления DANA","N/A — проверьте zonasdeinundacion.es.", story)

    section("Итоговая оценка", story)
    story.append(Spacer(1,0.2*cm))
    sc_tbl = Table([
        [P(str(score),score_s)],
        [P("из 100 — объект интересный, часть данных требует уточнения",ssub_s)],
        [P(f"Ориентация: {orient}. Гараж: {garaje}. Бассейн: {piscina}.",svrd_s)],
        [P("Рекомендуем уточнить кадастровые данные и осмотреть кровлю перед задатком.",srec_s)],
    ],colWidths=[16*cm])
    sc_tbl.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#f5f9ff")),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#bbdefb")),
        ("PADDING",(0,0),(-1,-1),8)]))
    story.append(sc_tbl)
    story.append(Spacer(1,0.4*cm))
    story.append(HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#cfd8dc")))
    story.append(Spacer(1,0.1*cm))
    story.append(P("Отчёт сгенерирован автоматически. Источники: Idealista, PVGIS, Catastro, OSM. "
                   "Поля N/A — данные после подключения аэрофото PNOA. Не является юридическим документом.", disc_s))
    doc.build(story)
    buf.seek(0)
    return buf

# ─── ОБРАБОТКА СООБЩЕНИЙ ──────────────────────────────────────────────────────
def process_report(sender_id, name, text, lang):
    """Генерируем и отправляем PDF"""
    send_msg(sender_id, T["wait"][lang])
    notify_tg(sender_id, name, text, lang)

    is_url = "idealista.com" in text.lower() or text.startswith("http")
    idal   = parse_idealista(text.strip()) if is_url else {}
    geo_q  = (idal.get("barrio","")+" Torrevieja Spain") if is_url and idal.get("barrio") else text

    lat, lon  = get_coordinates(geo_q)
    cat       = get_catastro(text if not is_url else geo_q)
    solar     = get_solar(lat, lon)
    park      = get_parking(lat, lon)
    dist_sea  = get_distance_sea(lat, lon)
    income    = calc_income(idal.get("precio",0), idal.get("area_util") or idal.get("area"))

    pdf_data = {
        "address": text,
        "fecha":   date.today().strftime("%d.%m.%Y"),
        "idealista": idal, "catastro": cat, "solar": solar,
        "parking": park, "dist_sea": dist_sea, "income": income,
    }

    count = increment_reports(sender_id)
    buf = generate_pdf(pdf_data)
    send_pdf(sender_id, buf)

    # После отчёта
    if count >= 3:
        # Лимит достигнут — усиленный оффер
        set_state(sender_id, step="limit_offer")
        send_limit_buttons(sender_id, lang)
    else:
        # Обычные кнопки после отчёта
        set_state(sender_id, step="after_report")
        send_after_report_buttons(sender_id, lang)

def handle_msg(sender_id, name, text):
    state = get_state(sender_id)
    lang  = state.get("lang")
    step  = state.get("step","")

    # Если агент ведёт диалог — молчим
    if step == "talking_to_agent":
        return

    # Сброс
    reset_words = ["start","reset","привет","hello","hola","hi","begin","menu","меню"]
    if text.lower().strip() in reset_words or not lang:
        user_states[sender_id] = {}
        send_lang_buttons(sender_id)
        return

    # Сбор имени (последний шаг воронки)
    if step == "collect_name":
        criteria = state.get("criteria", {})
        criteria["name"] = text.strip()
        set_state(sender_id, criteria=criteria, step="talking_to_agent")
        send_msg(sender_id, T["thanks"][lang])
        notify_hot_lead(sender_id, name, lang, criteria)
        return

    # Ожидаем ссылку
    if step == "await_link":
        if len(text.strip()) > 8:
            set_state(sender_id, step="processing")
            process_report(sender_id, name, text.strip(), lang)
        else:
            send_msg(sender_id, T["invalid"][lang])
        return

    # После отчёта — любое сообщение = спрашиваем снова
    if step in ("after_report", "done"):
        set_state(sender_id, step="await_link")
        send_msg(sender_id, T["ask"][lang])
        return

    # Дефолт
    send_lang_buttons(sender_id)
    user_states[sender_id] = {}

def handle_postback(sender_id, name, payload):
    state = get_state(sender_id)
    lang  = state.get("lang","ru")
    step  = state.get("step","")

    # Если агент ведёт — молчим
    if step == "talking_to_agent":
        return

    # Выбор языка
    if payload in ("LANG_EN","LANG_RU","LANG_ES"):
        lang = {"LANG_EN":"en","LANG_RU":"ru","LANG_ES":"es"}[payload]
        set_state(sender_id, lang=lang, step="await_link")
        send_msg(sender_id, T["ask"][lang])
        return

    # Ещё объект
    if payload == "ANOTHER_REPORT":
        reports = get_reports_today(sender_id)
        if reports >= 3:
            set_state(sender_id, step="limit_offer")
            send_limit_buttons(sender_id, lang)
        else:
            set_state(sender_id, step="await_link")
            send_msg(sender_id, T["ask"][lang])
        return

    # Хочу консультацию (с любого этапа)
    if payload == "WANT_AGENT":
        set_state(sender_id, step="collect_type", criteria={})
        send_msg(sender_id, T["yes_connect"][lang])
        send_quick_replies(sender_id, T["ask_type"][lang], TYPES[lang])
        return

    # Да, хочу горячие объекты
    if payload == "HOT_YES":
        set_state(sender_id, step="collect_type", criteria={})
        send_msg(sender_id, T["yes_connect"][lang])
        send_quick_replies(sender_id, T["ask_type"][lang], TYPES[lang])
        return

    # Нет, подожду 24 часа
    if payload == "HOT_NO":
        set_state(sender_id, step="done")
        send_msg(sender_id, T["wait_24h"][lang])
        return

def handle_quick_reply(sender_id, name, text):
    state = get_state(sender_id)
    lang  = state.get("lang","ru")
    step  = state.get("step","")
    criteria = state.get("criteria", {})

    if step == "collect_type":
        criteria["type"] = text
        set_state(sender_id, criteria=criteria, step="collect_budget")
        send_quick_replies(sender_id, T["ask_budget"][lang], BUDGETS[lang])
        return

    if step == "collect_budget":
        criteria["budget"] = text
        set_state(sender_id, criteria=criteria, step="collect_area")
        send_quick_replies(sender_id, T["ask_area"][lang], AREAS[lang])
        return

    if step == "collect_area":
        criteria["area"] = text
        set_state(sender_id, criteria=criteria, step="collect_name")
        send_msg(sender_id, T["ask_name"][lang])
        return

    # Если не распознали quick reply — обрабатываем как обычное сообщение
    handle_msg(sender_id, name, text)

# ─── FLASK ────────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.mode") == "subscribe" and \
       request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data.get("object") == "page":
        for entry in data.get("entry",[]):
            for ev in entry.get("messaging",[]):
                sid  = ev["sender"]["id"]
                name = "User"
                try:
                    p = requests.get(
                        f"https://graph.facebook.com/{sid}?fields=first_name&access_token={PAGE_ACCESS_TOKEN}",
                        timeout=5).json()
                    name = p.get("first_name","User")
                except: pass

                if "message" in ev:
                    msg = ev["message"]
                    # Quick reply
                    if "quick_reply" in msg:
                        handle_quick_reply(sid, name, msg["quick_reply"]["payload"])
                    elif "text" in msg:
                        handle_msg(sid, name, msg["text"])
                elif "postback" in ev:
                    handle_postback(sid, name, ev["postback"]["payload"])

    return jsonify({"status":"ok"}), 200

# Telegram callback для агента (подтверждение рассылки)
@app.route("/telegram_callback", methods=["POST"])
def telegram_callback():
    data = request.get_json()
    if "callback_query" in data:
        cb = data["callback_query"]
        cb_data = cb.get("data","")
        msg_id = cb["message"]["message_id"]

        if cb_data.startswith("confirm_"):
            user_fb_id = cb_data.replace("confirm_","")
            set_state(user_fb_id, subscribed=True)
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                json={"callback_query_id":cb["id"],"text":"Рассылка подтверждена!"})
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageReplyMarkup",
                json={"chat_id":cb["message"]["chat"]["id"],"message_id":msg_id,"reply_markup":{}})

        elif cb_data.startswith("reject_"):
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                json={"callback_query_id":cb["id"],"text":"Лид отклонён"})

    return jsonify({"ok":True})

@app.route("/")
def index():
    return "InmoSpain Bot is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
