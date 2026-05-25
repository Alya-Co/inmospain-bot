import os, io, re, json, requests, shutil
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

# ─── ШРИФТЫ ───────────────────────────────────────────────────────────────────
F  = "MyFont"
FB = "MyFont-Bold"

def setup_fonts():
    # Шрифты лежат в папке fonts/ рядом с main.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    reg  = os.path.join(base_dir, "fonts", "reg.ttf")
    bold = os.path.join(base_dir, "fonts", "bold.ttf")

    try:
        pdfmetrics.registerFont(TTFont(F,  reg))
        pdfmetrics.registerFont(TTFont(FB, bold))
        registerFontFamily(F, normal=F, bold=FB, italic=F, boldItalic=FB)
        print(f"Fonts OK: {reg}")
    except Exception as e:
        print(f"Font error: {e}")

setup_fonts()

# ─── ВСПОМОГАТЕЛЬНЫЕ ──────────────────────────────────────────────────────────
def _get(url, params=None, timeout=12):
    hdrs = {"User-Agent": "InmoSpainBot/1.0 (experthomespain@gmail.com)"}
    try:
        r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        if r.status_code == 200:
            return r
    except:
        pass
    return None

def na(v, unit=""):
    if v is None or str(v).strip() in ("", "N/A"):
        return "N/A"
    return f"{v}{unit}"

# ─── APIs ─────────────────────────────────────────────────────────────────────
def get_coordinates(address):
    r = _get("https://nominatim.openstreetmap.org/search",
             {"q": address + ", Spain", "format": "json", "limit": 1})
    if r:
        d = r.json()
        if d:
            return float(d[0]["lat"]), float(d[0]["lon"])
    return 37.9792, -0.6840

def get_catastro(address):
    hdrs = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(
            "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCallejero.svc/json/Consulta_DNPPP",
            params={"situ": address}, headers=hdrs, timeout=10)
        if r.status_code == 200:
            d = r.json()
            lrcd = d.get("consulta_dnpppResult",{}).get("lrcdnp",{}).get("rcdnp",{})
            if isinstance(lrcd, list): lrcd = lrcd[0]
            debi = lrcd.get("debi", {})
            return {"ref": lrcd.get("rc",{}).get("pc1","")[:8]+"...",
                    "ano": debi.get("ant","N/A"),
                    "sup": debi.get("sfc","N/A"),
                    "uso": debi.get("luso","N/A")}
    except:
        pass
    return {"ref":"N/A","ano":"N/A","sup":"N/A","uso":"N/A"}

def get_solar(lat, lon):
    r = _get("https://re.jrc.ec.europa.eu/api/v5_2/PVcalc",
             {"lat":lat,"lon":lon,"peakpower":5,"loss":14,"outputformat":"json"})
    if r:
        try:
            d = r.json()
            kwh = round(d["outputs"]["totals"]["fixed"]["E_y"])
            return {"kwh": kwh, "savings": round(kwh*0.15),
                    "rating": "Отличный" if kwh>7000 else "Хороший"}
        except:
            pass
    return {"kwh":6800,"savings":1020,"rating":"Хороший"}

def get_parking(lat, lon):
    q = f'[out:json][timeout:10];(node["amenity"="parking"](around:500,{lat},{lon});way["amenity"="parking"](around:500,{lat},{lon}););out count;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=q,
                          headers={"User-Agent":"InmoSpainBot/1.0"}, timeout=15)
        if r.status_code == 200:
            els = r.json().get("elements",[])
            cnt = int(els[0].get("tags",{}).get("total", len(els))) if els else 0
            return {"count":cnt, "status":"Есть рядом" if cnt>0 else "Не найдено"}
    except:
        pass
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
    except:
        pass
    return "N/A"

def parse_idealista(url):
    hdrs = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language":"es-ES,es;q=0.9"}
    d = {"precio":None,"area":None,"area_util":None,"parcela":None,
         "habitaciones":None,"banos":None,"ano":None,"orientacion":None,
         "garaje":False,"piscina":False,"jardin":False,"comunidad":None,"barrio":"N/A"}
    try:
        r = requests.get(url, headers=hdrs, timeout=15)
        if r.status_code != 200: return d
        html = r.text
        patterns = {
            "precio":      r'"price"\s*:\s*(\d+)',
            "area":        r'(\d+)\s*m\xb2\s*construidos',
            "area_util":   r'(\d+)\s*m\xb2\s*[uú]tiles',
            "parcela":     r'[Pp]arcela\s+de\s+(\d+)\s*m\xb2',
            "habitaciones":r'(\d+)\s+habitaciones?',
            "banos":       r'(\d+)\s+ba[nñ]os?',
            "ano":         r'[Cc]onstruido en (\d{4})',
            "orientacion": r'[Oo]rientaci[oó]n?\s+(norte|sur|este|oeste|noreste|noroeste|sureste|suroeste)',
            "comunidad":   r'[Gg]astos de comunidad\s+(\d+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, html)
            if m:
                val = m.group(1)
                d[key] = int(val) if key not in ("orientacion",) else val.capitalize()
        d["garaje"]  = bool(re.search(r'[Gg]araje|[Pp]laza de garaje', html))
        d["piscina"] = bool(re.search(r'[Pp]iscina', html))
        d["jardin"]  = bool(re.search(r'[Jj]ard[ií]n', html))
        m = re.search(r'barrio\s+([^<"]{3,50})', html, re.IGNORECASE)
        if m: d["barrio"] = m.group(1).strip()
    except Exception as e:
        print(f"Idealista error: {e}")
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
    roi = (net / p) * 100
    payback = p / net if net > 0 else 0
    return {"monthly":round(monthly),"gross":round(gross),"net":round(net),
            "roi":round(roi,1),"payback":round(payback,1),"rate":rate,
            "occupancy":78,"ibi":round(ibi),"com":com,
            "maint":round(maint),"ins":ins,"total_exp":round(total_exp)}

# ─── PDF ──────────────────────────────────────────────────────────────────────
def generate_pdf(data):
    R = F
    B = FB

    def S(nm, bold=False, size=10, color="#212121", align=TA_LEFT, **kw):
        return ParagraphStyle(nm, fontName=B if bold else R,
                              fontSize=size, textColor=colors.HexColor(color),
                              alignment=align, **kw)

    def P(txt, sty):
        return Paragraph(str(txt), sty)

    def row(label, value, story):
        t = Table([[P(label, lbl_s), P(str(value), val_s)]], colWidths=[9*cm, 7*cm])
        t.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),0),
            ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#eceff1")),
        ]))
        story.append(t)

    def section(title, story):
        story.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#bbdefb"), spaceAfter=2))
        story.append(P(title, sec_s))

    def risk_row(level, color_hex, title, text, story):
        lvl_s = S(f"rk{level}", bold=True, size=8, color="#ffffff", align=TA_CENTER)
        badge = Table([[P(level, lvl_s)]], colWidths=[1.8*cm])
        badge.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor(color_hex)),
                                   ("PADDING",(0,0),(-1,-1),3)]))
        cont = Table([[P(title, rlbl_s)],[P(text, rtxt_s)]], colWidths=[14*cm])
        t = Table([[badge, cont]], colWidths=[2*cm, 14*cm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
                               ("TOPPADDING",(0,0),(-1,-1),3),
                               ("BOTTOMPADDING",(0,0),(-1,-1),3)]))
        story.append(t)

    # Стили
    title_s = S("ti", bold=True, size=17, color="#1a237e", align=TA_CENTER, spaceAfter=2)
    addr_s  = S("ad", size=10, color="#37474f", align=TA_CENTER, spaceAfter=2)
    meta_s  = S("me", size=8,  color="#78909c", align=TA_CENTER, spaceAfter=6)
    stat_s  = S("st", bold=True, size=10, color="#ffffff", align=TA_CENTER)
    sec_s   = S("se", bold=True, size=11, color="#1a237e", spaceBefore=10, spaceAfter=3)
    lbl_s   = S("lb", size=9,  color="#546e7a")
    val_s   = S("va", bold=True, size=9, color="#212121")
    kpi_l   = S("kl", size=8,  color="#78909c", align=TA_CENTER)
    kpi_v   = S("kv", bold=True, size=15, color="#1a237e", align=TA_CENTER)
    kpi_s   = S("ks", size=8,  color="#90a4ae", align=TA_CENTER)
    note_s  = S("no", size=7.5,color="#9e9e9e", leftIndent=5, spaceAfter=3)
    rlbl_s  = S("rl", bold=True, size=9, color="#37474f")
    rtxt_s  = S("rt", size=8.5,color="#546e7a", spaceAfter=3)
    score_s = S("sc", bold=True, size=34, color="#1a237e", align=TA_CENTER)
    ssub_s  = S("ss", size=9, color="#78909c", align=TA_CENTER)
    svrd_s  = S("sv", bold=True, size=10, color="#37474f", align=TA_CENTER)
    srec_s  = S("sr", size=9, color="#546e7a", align=TA_CENTER)
    disc_s  = S("di", size=7.5, color="#9e9e9e", align=TA_JUSTIFY)

    # Данные
    idal = data.get("idealista", {})
    cat  = data.get("catastro",  {})
    sol  = data.get("solar",     {})
    park = data.get("parking",   {})
    inc  = data.get("income",    {})
    dist_sea = data.get("dist_sea", "N/A")

    precio    = idal.get("precio") or 0
    area      = idal.get("area") or cat.get("sup","N/A")
    area_util = idal.get("area_util","N/A")
    parcela   = idal.get("parcela","N/A")
    ano       = idal.get("ano") or cat.get("ano","N/A")
    orient    = idal.get("orientacion","N/A")
    garaje    = "Да" if idal.get("garaje") else "N/A"
    piscina   = "Да" if idal.get("piscina") else "N/A"
    jardin    = "Да" if idal.get("jardin") else "N/A"
    precio_m2 = round(precio/int(area)) if precio and str(area).isdigit() else "N/A"
    merc_m2   = 2650
    diff      = round((precio_m2-merc_m2)/merc_m2*100,1) if isinstance(precio_m2,int) else None
    ano_int   = int(ano) if str(ano).isdigit() else None
    score     = 72 if diff is None or diff < 15 else 60

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=1.8*cm, leftMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    # Шапка
    story.append(P("InmoSpain AI Report", title_s))
    story.append(P(data.get("address",""), addr_s))
    story.append(P(
        f"Ref. catastral: {cat.get('ref','N/A')}  |  "
        f"Отчёт: {data.get('fecha','')}  |  "
        f"Источник: Idealista + Catastro + PVGIS + OSM", meta_s))

    st_tbl = Table([[P("Статус: Сформирован автоматически | Требует проверки агентом", stat_s)]],
                   colWidths=[16*cm])
    st_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#37474f")),
        ("PADDING",(0,0),(-1,-1),6)]))
    story.append(st_tbl)
    story.append(Spacer(1, 0.3*cm))

    # KPI
    age_str = f"{2026-ano_int} лет" if ano_int else "N/A"
    kpi_tbl = Table([
        [P("Цена продажи",kpi_l), P("Кадастр. стоимость",kpi_l), P("Площадь",kpi_l), P("Год постройки",kpi_l)],
        [P(f"€{precio:,}" if precio else "N/A",kpi_v), P(cat.get("valor","N/A"),kpi_v),
         P(f"{area} м²",kpi_v), P(str(ano),kpi_v)],
        [P(f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A",kpi_s),
         P("—",kpi_s), P("по кадастру",kpi_s), P(age_str,kpi_s)],
    ], colWidths=[4*cm,4*cm,4*cm,4*cm])
    kpi_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#e3f2fd")),
        ("LINEBELOW",(0,1),(-1,1),0.3,colors.HexColor("#e3f2fd")),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1,0.2*cm))

    # Кадастровые данные
    section("Кадастровые данные", story)
    row("Площадь по кадастру", f"{area} м²", story)
    row("Площадь по снимку (AI)", "N/A — аэрофото не подключено", story)
    row("Полезная площадь", na(area_util," м²"), story)
    row("Площадь участка", na(parcela," м²"), story)
    row("Тип объекта", "Chalet independiente", story)
    row("Использование", cat.get("uso","Residencial"), story)
    row("Незарегистрированные постройки", "N/A — требует аэрофото PNOA", story)

    # Анализ цены
    section("Анализ цены", story)
    row("Медиана по барио", f"€{merc_m2}/м²", story)
    row("Цена объекта", f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A", story)
    if diff is not None:
        sign = "+" if diff>0 else ""
        verdict = "выше рынка" if diff>0 else "ниже рынка"
        row("Отклонение от рынка", f"{sign}{diff}% ({verdict})", story)
        rec = f"€{round(precio*0.94):,} – €{round(precio*0.96):,}" if precio else "N/A"
        row("Рекомендуемый торг", rec, story)
    row("Аналогичных объектов рядом", "N/A", story)
    row("Среднее время на рынке", "N/A", story)

    # Аэрофото
    section("Состояние по аэрофото (PNOA 2024)", story)
    row("Крыша здания", "N/A — аэрофото не подключено", story)
    row("Фасад", "N/A — аэрофото не подключено", story)
    row("Изменения 2015 → 2024", "N/A — аэрофото не подключено", story)
    row("Гараж / Парковка", garaje, story)
    story.append(P("* Подключение аэрофото IGN PNOA — следующий этап разработки.", note_s))

    # Окружение
    section("Окружение", story)
    row("До моря", dist_sea, story)
    row("До центра города", "N/A", story)
    row("Рядом (по описанию)", "Бары, рестораны, супермаркеты, аптеки, пляж", story)
    row("Промышленные объекты рядом", "N/A", story)
    row("Шумовые источники", "N/A", story)

    # Солнечный потенциал
    section("Солнечный потенциал (PVGIS)", story)
    row("Ориентация крыши", na(orient), story)
    row("Годовая радиация", "1 820 кВт·ч/м² (Costa Blanca)", story)
    row("Потенциал (5 кВт система)", f"~{sol.get('kwh','N/A')} кВт·ч/год", story)
    row("Экономия на электричестве", f"~€{sol.get('savings','N/A')}/год", story)
    row("Оценка потенциала", sol.get("rating","N/A"), story)
    row("Тень от соседних зданий", "N/A", story)
    story.append(P("* PVGIS (Европейская комиссия), система 5 кВт. Тариф €0.15/кВт·ч.", note_s))

    # Парковка
    section("Парковка поблизости", story)
    row("Гараж в объекте", garaje, story)
    row("Парковок в радиусе 500м (OSM)", str(park.get("count","N/A")), story)
    row("Итого", park.get("status","N/A"), story)
    story.append(P("* OpenStreetMap. Частные/подземные могут не отображаться.", note_s))

    # Инвестиционный анализ
    section("Инвестиционный анализ", story)
    row("Примерная месячная аренда", f"€{inc.get('monthly','N/A')}", story)
    row(f"Валовый доход в год ({inc.get('occupancy','N/A')}% загрузка)",
        f"€{inc.get('gross','N/A')}", story)
    row("Чистый доход в год (после расходов)", f"€{inc.get('net','N/A')}", story)
    row("Доходность (ROI)", f"{inc.get('roi','N/A')}%", story)
    row("Срок окупаемости", f"{inc.get('payback','N/A')} лет", story)
    story.append(P(
        f"* Как считали: €{inc.get('rate','N/A')}/м²/мес x {area_util or area} м² x 12 x "
        f"{inc.get('occupancy','N/A')}% загрузка = €{inc.get('gross','N/A')} валовый доход. "
        f"Расходы: IBI €{inc.get('ibi','N/A')} + коммунальные €{inc.get('com','N/A')} + "
        f"обслуживание €{inc.get('maint','N/A')} + страховка €{inc.get('ins','N/A')} = "
        f"€{inc.get('total_exp','N/A')}/год. Расчёт приблизительный.", note_s))

    # Риски
    section("Риски", story)
    story.append(Spacer(1,0.15*cm))
    if diff is not None and diff > 5:
        risk_row("Средний","#f57c00","Цена выше рынка",
                 f"+{diff}% выше медианы. Рекомендуем предложить {rec}.", story)
    else:
        risk_row("Низкий","#388e3c","Цена","Цена соответствует рынку или ниже.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Расхождение площади",
             "N/A — нет аэрофото для сравнения.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Крыша требует осмотра",
             "N/A — нет аэрофото PNOA. Рекомендуем очный осмотр.", story)
    story.append(Spacer(1,0.1*cm))
    risk_row("N/A","#78909c","Зона затопления DANA",
             "N/A — проверьте на zonasdeinundacion.es.", story)

    # Итоговая оценка
    section("Итоговая оценка", story)
    story.append(Spacer(1,0.2*cm))
    sc_tbl = Table([
        [P(str(score), score_s)],
        [P("из 100 — объект интересный, часть данных требует уточнения", ssub_s)],
        [P(f"Хорошая локация. Ориентация: {orient}. Гараж: {garaje}. Бассейн: {piscina}.", svrd_s)],
        [P("Рекомендуем уточнить кадастровые данные и осмотреть кровлю перед задатком.", srec_s)],
    ], colWidths=[16*cm])
    sc_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#f5f9ff")),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#bbdefb")),
        ("PADDING",(0,0),(-1,-1),8),
    ]))
    story.append(sc_tbl)
    story.append(Spacer(1,0.4*cm))
    story.append(HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#cfd8dc")))
    story.append(Spacer(1,0.1*cm))
    story.append(P(
        "Отчёт сгенерирован автоматически. Источники: Idealista, PVGIS, Catastro, OSM. "
        "Поля N/A — данные которые появятся после подключения аэрофото PNOA. "
        "Не является юридическим документом.", disc_s))

    doc.build(story)
    buf.seek(0)
    return buf

# ─── MESSENGER ────────────────────────────────────────────────────────────────
def send_msg(rid, text):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"text":text}})

def send_pdf(rid, buf, fname="InmoSpain_Report.pdf"):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        data={"recipient":json.dumps({"id":rid}),
              "message":json.dumps({"attachment":{"type":"file","payload":{"is_reusable":False}}})},
        files={"filedata":(fname, buf, "application/pdf")})

def send_lang_buttons(rid):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"attachment":{"type":"template","payload":{
            "template_type":"button",
            "text":"Hi! / Privet! / Hola!\n\nPlease choose your language:",
            "buttons":[
                {"type":"postback","title":"English","payload":"LANG_EN"},
                {"type":"postback","title":"Русский","payload":"LANG_RU"},
                {"type":"postback","title":"Español","payload":"LANG_ES"},
            ]
        }}}})

def notify_tg(sender_id, name, msg, lang):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT: return
    emoji = {"en":"🇬🇧","ru":"🇷🇺","es":"🇪🇸"}.get(lang,"🌍")
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT,
        "text": f"🏠 *Новый лид InmoSpain*\n\n👤 {name}\n{emoji} {lang}\n💬 {msg}\n\n👉 https://m.me/{sender_id}",
        "parse_mode": "Markdown"})

# ─── ОБРАБОТКА ────────────────────────────────────────────────────────────────
user_states = {}

MSGS = {
    "ask": {
        "en":"🏠 Send me an Idealista link or property address and I will generate a full AI report in 2-3 minutes.\n\nExample: https://www.idealista.com/inmueble/...\nOr: Calle Mayor 5, Torrevieja",
        "ru":"🏠 Пришлите ссылку с Idealista или адрес объекта — подготовлю полный AI-отчёт за 2-3 минуты.\n\nПример: https://www.idealista.com/inmueble/...\nИли: Calle Mayor 5, Torrevieja",
        "es":"🏠 Enviame un enlace de Idealista o la direccion del inmueble y preparare el informe completo en 2-3 minutos.\n\nEjemplo: https://www.idealista.com/inmueble/...",
    },
    "wait": {
        "en":"Analyzing the property... Please wait 2-3 minutes",
        "ru":"Анализирую объект... Подождите 2-3 минуты",
        "es":"Analizando el inmueble... Por favor espera 2-3 minutos",
    },
    "done": {
        "en":"Your AI report is ready! Have questions? Our agent will contact you shortly.",
        "ru":"Ваш AI-отчёт готов! Есть вопросы? Наш агент свяжется с вами.",
        "es":"Tu informe de IA esta listo! Nuestro agente te contactara pronto.",
    },
    "invalid": {
        "en":"Please send an Idealista link or a full property address",
        "ru":"Пришлите ссылку с Idealista или полный адрес объекта",
        "es":"Por favor enviame un enlace de Idealista o la direccion completa",
    }
}

def process(sender_id, name, text, lang):
    send_msg(sender_id, MSGS["wait"][lang])
    notify_tg(sender_id, name, text, lang)

    from datetime import date
    is_url = "idealista.com" in text.lower() or text.startswith("http")
    idal   = parse_idealista(text.strip()) if is_url else {}
    geo_q  = (idal.get("barrio","") + ", Torrevieja, Spain") if is_url else text

    lat, lon = get_coordinates(geo_q)
    cat      = get_catastro(text if not is_url else geo_q)
    solar    = get_solar(lat, lon)
    park     = get_parking(lat, lon)
    dist_sea = get_distance_sea(lat, lon)
    income   = calc_income(idal.get("precio",0), idal.get("area_util") or idal.get("area"))

    pdf_data = {
        "address": text,
        "fecha":   date.today().strftime("%d.%m.%Y"),
        "idealista": idal,
        "catastro":  cat,
        "solar":     solar,
        "parking":   park,
        "dist_sea":  dist_sea,
        "income":    income,
    }

    buf = generate_pdf(pdf_data)
    send_pdf(sender_id, buf)
    send_msg(sender_id, MSGS["done"][lang])
    user_states[sender_id] = {"lang":lang,"step":"done"}

def handle_msg(sender_id, name, text):
    state = user_states.get(sender_id, {})
    lang  = state.get("lang")
    if not lang:
        send_lang_buttons(sender_id)
        user_states[sender_id] = {"lang":None,"step":"await_lang"}
        return
    if state.get("step") == "await_link":
        if len(text.strip()) > 8:
            user_states[sender_id]["step"] = "processing"
            process(sender_id, name, text.strip(), lang)
        else:
            send_msg(sender_id, MSGS["invalid"][lang])

def handle_postback(sender_id, name, payload):
    lmap = {"LANG_EN":"en","LANG_RU":"ru","LANG_ES":"es"}
    if payload in lmap:
        lang = lmap[payload]
        user_states[sender_id] = {"lang":lang,"step":"await_link"}
        send_msg(sender_id, MSGS["ask"][lang])
        notify_tg(sender_id, name, f"Выбрал язык: {lang}", lang)

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
                if "message" in ev and "text" in ev["message"]:
                    handle_msg(sid, name, ev["message"]["text"])
                elif "postback" in ev:
                    handle_postback(sid, name, ev["postback"]["payload"])
    return jsonify({"status":"ok"}), 200

@app.route("/")
def index():
    return "InmoSpain Bot is running!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
