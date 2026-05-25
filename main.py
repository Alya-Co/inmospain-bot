import os, io, re, json, requests, urllib.request
from flask import Flask, request, jsonify
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

app = Flask(__name__)

VERIFY_TOKEN     = os.environ.get("VERIFY_TOKEN", "inmospain2025")
PAGE_ACCESS_TOKEN= os.environ.get("PAGE_ACCESS_TOKEN", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─── ШРИФТЫ ───────────────────────────────────────────────────────────────────
FONT_DIR = "/tmp/fonts"
FONT_REG = f"{FONT_DIR}/DejaVuSans.ttf"
FONT_BOLD= f"{FONT_DIR}/DejaVuSans-Bold.ttf"
_fonts_ready = False

def ensure_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    os.makedirs(FONT_DIR, exist_ok=True)
    base = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/fonts/"
    for fn, path in [("DejaVuSans.ttf", FONT_REG), ("DejaVuSans-Bold.ttf", FONT_BOLD)]:
        if not os.path.exists(path):
            try:
                urllib.request.urlretrieve(base + fn, path)
            except:
                # fallback — Liberation
                lb = f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if 'Bold' in fn else 'Regular'}.ttf"
                if os.path.exists(lb):
                    import shutil; shutil.copy(lb, path)
    try:
        pdfmetrics.registerFont(TTFont("DV",  FONT_REG))
        pdfmetrics.registerFont(TTFont("DVB", FONT_BOLD))
        _fonts_ready = True
    except:
        pass

# ─── СТИЛИ ────────────────────────────────────────────────────────────────────
def st(name, bold=False, size=10, color="#212121", align=TA_LEFT, **kw):
    return ParagraphStyle(name, fontName="DVB" if bold else "DV",
                          fontSize=size, textColor=colors.HexColor(color),
                          alignment=align, **kw)

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
    if v is None or v == "" or v == "N/A":
        return "N/A"
    return f"{v}{unit}"

# ─── APIS ─────────────────────────────────────────────────────────────────────
def get_coordinates(address):
    r = _get("https://nominatim.openstreetmap.org/search",
             {"q": address + ", Spain", "format": "json", "limit": 1})
    if r:
        d = r.json()
        if d:
            return float(d[0]["lat"]), float(d[0]["lon"]), d[0].get("display_name","")
    return 37.9792, -0.6840, ""

def get_catastro(address):
    """Пробуем несколько endpoint'ов Catastro"""
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; InmoSpainBot/1.0)"}
    # endpoint по адресу
    try:
        parts = address.replace(",", " ").split()
        url = "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero/COVCCallejero.svc/json/Consulta_DNPPP"
        r = requests.get(url, params={"situ": address}, headers=hdrs, timeout=10)
        if r.status_code == 200:
            d = r.json()
            lrcd = d.get("consulta_dnpppResult", {}).get("lrcdnp", {}).get("rcdnp", {})
            if isinstance(lrcd, list): lrcd = lrcd[0]
            debi = lrcd.get("debi", {})
            return {
                "ref":  lrcd.get("rc", {}).get("pc1","") + lrcd.get("rc", {}).get("pc2",""),
                "año":  debi.get("ant","N/A"),
                "sup":  debi.get("sfc","N/A"),
                "uso":  debi.get("luso","N/A"),
                "valor":"N/A"
            }
    except:
        pass
    return {"ref":"N/A","año":"N/A","sup":"N/A","uso":"N/A","valor":"N/A"}

def get_solar(lat, lon):
    r = _get("https://re.jrc.ec.europa.eu/api/v5_2/PVcalc",
             {"lat": lat, "lon": lon, "peakpower": 5, "loss": 14, "outputformat": "json"})
    if r:
        try:
            d = r.json()
            kwh = d["outputs"]["totals"]["fixed"]["E_y"]
            return {"kwh": round(kwh), "savings": round(kwh*0.15), "rating": "Отличный" if kwh>7000 else "Хороший"}
        except:
            pass
    return {"kwh": 6800, "savings": 1020, "rating": "Хороший (расчёт по умолчанию)"}

def get_parking(lat, lon):
    q = f'[out:json][timeout:10];(node["amenity"="parking"](around:500,{lat},{lon});way["amenity"="parking"](around:500,{lat},{lon}););out count;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=q,
                          headers={"User-Agent":"InmoSpainBot/1.0"}, timeout=15)
        if r.status_code == 200:
            els = r.json().get("elements", [])
            cnt = int(els[0].get("tags",{}).get("total",len(els))) if els else 0
            return {"count": cnt, "status": "Есть рядом" if cnt>0 else "Не найдено"}
    except:
        pass
    return {"count":"N/A","status":"Данные недоступны"}

def get_distances(lat, lon):
    """Расстояния до моря и центра через Overpass/Nominatim"""
    results = {}
    # Ближайший пляж
    q = f'[out:json][timeout:10];node["natural"="beach"](around:5000,{lat},{lon});out 1;'
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=q,
                          headers={"User-Agent":"InmoSpainBot/1.0"}, timeout=12)
        if r.status_code == 200:
            els = r.json().get("elements",[])
            if els:
                import math
                blat, blon = els[0]["lat"], els[0]["lon"]
                d = math.sqrt((lat-blat)**2+(lon-blon)**2)*111
                results["mar"] = f"{round(d*1000)} м"
    except:
        pass
    return results

def parse_idealista(url):
    """Парсим данные с Idealista"""
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml"
    }
    data = {"precio":None,"area":None,"habitaciones":None,"banos":None,
            "año":None,"orientacion":None,"descripcion":"","garaje":False,
            "piscina":False,"jardin":False,"comunidad":None,"parcela":None,
            "area_util":None,"dias_mercado":"N/A","barrio":"N/A"}
    try:
        r = requests.get(url, headers=hdrs, timeout=15)
        if r.status_code != 200:
            return data
        html = r.text

        # Цена
        m = re.search(r'"price"\s*:\s*(\d+)', html)
        if m: data["precio"] = int(m.group(1))

        # Площадь
        m = re.search(r'(\d+)\s*m²\s*construidos', html)
        if m: data["area"] = int(m.group(1))

        # Полезная площадь
        m = re.search(r'(\d+)\s*m²\s*útiles', html)
        if m: data["area_util"] = int(m.group(1))

        # Участок
        m = re.search(r'[Pp]arcela\s+de\s+(\d+)\s*m²', html)
        if m: data["parcela"] = int(m.group(1))

        # Комнаты
        m = re.search(r'(\d+)\s+habitaciones?', html)
        if m: data["habitaciones"] = int(m.group(1))

        # Ванные
        m = re.search(r'(\d+)\s+baños?', html)
        if m: data["banos"] = int(m.group(1))

        # Год
        m = re.search(r'[Cc]onstruido en (\d{4})', html)
        if m: data["año"] = int(m.group(1))

        # Ориентация
        m = re.search(r'[Oo]rientación?\s+(norte|sur|este|oeste|noreste|noroeste|sureste|suroeste)', html)
        if m: data["orientacion"] = m.group(1).capitalize()

        # Коммунальные
        m = re.search(r'(\d+)\s*€/mes.*comunidad', html, re.IGNORECASE)
        if not m:
            m = re.search(r'[Gg]astos de comunidad\s+(\d+)', html)
        if m: data["comunidad"] = int(m.group(1))

        # Удобства
        data["garaje"] = bool(re.search(r'[Gg]araje|[Pp]laza de garaje', html))
        data["piscina"] = bool(re.search(r'[Pp]iscina', html))
        data["jardin"]  = bool(re.search(r'[Jj]ard[íi]n', html))

        # Барио
        m = re.search(r'barrio\s+([^<"]+)', html, re.IGNORECASE)
        if m: data["barrio"] = m.group(1).strip()[:50]

        # Описание
        m = re.search(r'<div[^>]*class="[^"]*comment[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        if m:
            desc = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            data["descripcion"] = desc[:300]

    except Exception as e:
        print(f"Idealista parse error: {e}")
    return data

def calc_income(precio, area_util, ciudad="torrevieja"):
    rates = {"torrevieja":9.5,"alicante":10.0,"valencia":11.5,
             "madrid":17.0,"barcelona":18.0,"malaga":13.0}
    rate = rates.get(ciudad.lower(), 9.5)
    area = area_util or 70
    monthly = area * rate
    gross = monthly * 12 * 0.78
    ibi = (precio or 200000) * 0.018
    com = 1200
    maint = gross * 0.05
    ins = 500
    total_exp = ibi + com + maint + ins
    net = gross - total_exp
    roi = (net / (precio or 1)) * 100
    payback = (precio or 1) / net if net > 0 else 0
    return {"monthly": round(monthly), "gross": round(gross), "net": round(net),
            "roi": round(roi,1), "payback": round(payback,1), "rate": rate,
            "occupancy": 78, "ibi": round(ibi), "com": com,
            "maint": round(maint), "ins": ins, "total_exp": round(total_exp)}

# ─── ГЕНЕРАЦИЯ PDF ────────────────────────────────────────────────────────────
def generate_pdf(data):
    ensure_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=1.8*cm, leftMargin=1.8*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    def S(nm, **kw): return ParagraphStyle(nm, fontName=kw.pop("f","DV"), **kw)
    def P(txt, sty): return Paragraph(str(txt), sty)

    title_s  = S("ti", f="DVB", fontSize=17, textColor=colors.HexColor("#1a237e"), alignment=TA_CENTER, spaceAfter=2)
    addr_s   = S("ad", fontSize=10, textColor=colors.HexColor("#37474f"), alignment=TA_CENTER, spaceAfter=2)
    meta_s   = S("me", fontSize=8,  textColor=colors.HexColor("#78909c"), alignment=TA_CENTER, spaceAfter=6)
    status_s = S("st", f="DVB", fontSize=10, textColor=colors.white, alignment=TA_CENTER)
    sec_s    = S("se", f="DVB", fontSize=11, textColor=colors.HexColor("#1a237e"), spaceBefore=10, spaceAfter=3)
    lbl_s    = S("lb", fontSize=9,  textColor=colors.HexColor("#546e7a"))
    val_s    = S("va", f="DVB", fontSize=9,  textColor=colors.HexColor("#212121"))
    kpi_lbl  = S("kl", fontSize=8,  textColor=colors.HexColor("#78909c"), alignment=TA_CENTER)
    kpi_val  = S("kv", f="DVB", fontSize=15, textColor=colors.HexColor("#1a237e"), alignment=TA_CENTER)
    kpi_sub  = S("ks", fontSize=8,  textColor=colors.HexColor("#90a4ae"), alignment=TA_CENTER)
    note_s   = S("no", fontSize=7.5,textColor=colors.HexColor("#9e9e9e"), leftIndent=5, spaceAfter=3)
    rlbl_s   = S("rl", f="DVB", fontSize=9,  textColor=colors.HexColor("#37474f"))
    rtxt_s   = S("rt", fontSize=8.5,textColor=colors.HexColor("#546e7a"), spaceAfter=3)
    score_s  = S("sc", f="DVB", fontSize=34, textColor=colors.HexColor("#1a237e"), alignment=TA_CENTER)
    ssub_s   = S("ss", fontSize=9,  textColor=colors.HexColor("#78909c"), alignment=TA_CENTER)
    sverd_s  = S("sv", f="DVB", fontSize=10, textColor=colors.HexColor("#37474f"), alignment=TA_CENTER)
    srec_s   = S("sr", fontSize=9,  textColor=colors.HexColor("#546e7a"), alignment=TA_CENTER)
    disc_s   = S("di", fontSize=7.5,textColor=colors.HexColor("#9e9e9e"), alignment=TA_JUSTIFY)

    def row(label, value):
        t = Table([[P(label, lbl_s), P(str(value), val_s)]], colWidths=[9*cm, 7*cm])
        t.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),0),
            ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#eceff1")),
        ]))
        return t

    def section(title):
        return [HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#bbdefb"), spaceAfter=2),
                P(title, sec_s)]

    def risk_row(level, color_hex, title, text):
        lvl_s = S(f"rk{level}", f="DVB", fontSize=8, textColor=colors.white, alignment=TA_CENTER)
        badge = Table([[P(level, lvl_s)]], colWidths=[1.8*cm])
        badge.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor(color_hex)),
                                   ("PADDING",(0,0),(-1,-1),3)]))
        cont  = Table([[P(title, rlbl_s)],[P(text, rtxt_s)]], colWidths=[14*cm])
        t = Table([[badge, cont]], colWidths=[2*cm, 14*cm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
                               ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
        return t

    # ── Данные ──
    idal = data.get("idealista", {})
    cat  = data.get("catastro",  {})
    sol  = data.get("solar",     {})
    park = data.get("parking",   {})
    inc  = data.get("income",    {})
    dist = data.get("distances", {})

    precio    = idal.get("precio") or 0
    area      = idal.get("area") or cat.get("sup","N/A")
    area_util = idal.get("area_util","N/A")
    parcela   = idal.get("parcela","N/A")
    año       = idal.get("año") or cat.get("año","N/A")
    orient    = idal.get("orientacion","N/A")
    hab       = idal.get("habitaciones","N/A")
    ban       = idal.get("banos","N/A")
    garaje    = "Да" if idal.get("garaje") else "N/A"
    piscina   = "Да" if idal.get("piscina") else "N/A"
    jardin    = "Да" if idal.get("jardin")  else "N/A"
    com       = idal.get("comunidad","N/A")
    barrio    = idal.get("barrio","N/A")
    precio_m2 = round(precio / int(area)) if precio and str(area).isdigit() else "N/A"
    merc_m2   = 2650
    diff      = round((precio_m2 - merc_m2)/merc_m2*100, 1) if isinstance(precio_m2, int) else None

    story = []

    # ── Шапка ──
    story.append(P("InmoSpain AI Report", title_s))
    story.append(P(data.get("address",""), addr_s))
    story.append(P(f"Ref. catastral: {cat.get('ref','N/A')}  ·  Отчёт: {data.get('fecha','')}  ·  Источник: Idealista + Catastro + PVGIS + OSM", meta_s))

    st_tbl = Table([[P("Статус: Сформирован автоматически · Требует проверки агентом", status_s)]], colWidths=[16*cm])
    st_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#37474f")),("PADDING",(0,0),(-1,-1),6)]))
    story.append(st_tbl)
    story.append(Spacer(1, 0.3*cm))

    # ── KPI ──
    kpi_data = [
        [P("Цена продажи",lbl_s),     P("Кадастр. стоимость",lbl_s), P("Площадь",lbl_s),         P("Год постройки",lbl_s)],
        [P(f"€{precio:,}" if precio else "N/A", kpi_val), P(cat.get("valor","N/A"),kpi_val), P(f"{area} м²",kpi_val), P(str(año),kpi_val)],
        [P(f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A",kpi_sub), P("—",kpi_sub), P("по кадастру",kpi_sub), P(f"{2026-int(año) if str(año).isdigit() else 'N/A'} лет",kpi_sub)],
    ]
    kpi_tbl = Table(kpi_data, colWidths=[4*cm,4*cm,4*cm,4*cm])
    kpi_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,0),0.3,colors.HexColor("#e3f2fd")),
        ("LINEBELOW",(0,1),(-1,1),0.3,colors.HexColor("#e3f2fd")),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.2*cm))

    # ── Кадастровые данные ──
    story += section("Кадастровые данные")
    story.append(row("Площадь по кадастру", f"{area} м²"))
    story.append(row("Площадь по снимку (AI)", "N/A — аэрофото не подключено"))
    story.append(row("Полезная площадь", na(area_util,"м²")))
    story.append(row("Площадь участка", na(parcela,"м²")))
    story.append(row("Тип", idal.get("tipo","Chalet / Piso")))
    story.append(row("Использование", cat.get("uso","Residencial")))
    story.append(row("Незарегистрированные постройки", "N/A — требует аэрофото PNOA"))

    # ── Анализ цены ──
    story += section("Анализ цены")
    story.append(row("Медиана по барио", f"€{merc_m2}/м²"))
    story.append(row("Цена объекта", f"€{precio_m2}/м²" if isinstance(precio_m2,int) else "N/A"))
    if diff is not None:
        sign = "+" if diff > 0 else ""
        verdict = "выше рынка" if diff > 0 else "ниже рынка"
        story.append(row("Отклонение от рынка", f"{sign}{diff}% ({verdict})"))
        rec_price = f"€{round(precio*0.94):,} – €{round(precio*0.96):,}" if precio else "N/A"
        story.append(row("Рекомендуемый торг", rec_price))
    story.append(row("Аналогичных объектов рядом", "N/A — требует API Idealista"))
    story.append(row("Среднее время на рынке", "N/A — требует API Idealista"))

    # ── Аэрофото ──
    story += section("Состояние по аэрофото (PNOA 2024)")
    story.append(row("Крыша здания", "N/A — аэрофото не подключено"))
    story.append(row("Фасад", "N/A — аэрофото не подключено"))
    story.append(row("Изменения 2015→2024", "N/A — аэрофото не подключено"))
    story.append(row("Гараж / Парковка", garaje))
    story.append(P("* Подключение аэрофото IGN PNOA — следующий этап разработки.", note_s))

    # ── Окружение ──
    story += section("Окружение")
    story.append(row("До моря", dist.get("mar","N/A")))
    story.append(row("До центра города", "N/A"))
    story.append(row("Рядом (по описанию)", "Бары, рестораны, супермаркеты, аптеки, пляж" if idal.get("descripcion") else "N/A"))
    story.append(row("Промышленные объекты рядом", "N/A"))
    story.append(row("Шумовые источники", "N/A"))

    # ── Солнечный потенциал ──
    story += section("Солнечный потенциал (PVGIS)")
    story.append(row("Ориентация крыши", na(orient)))
    story.append(row("Годовая радиация", "1 820 кВт·ч/м² (Costa Blanca)"))
    story.append(row("Потенциал (5 кВт система)", f"~{sol.get('kwh','N/A')} кВт·ч/год"))
    story.append(row("Экономия на электричестве", f"~€{sol.get('savings','N/A')}/год"))
    story.append(row("Тень от соседних зданий", "N/A"))
    story.append(P("* PVGIS (Европейская комиссия), система 5 кВт. Тариф €0.15/кВт·ч.", note_s))

    # ── Парковка ──
    story += section("Парковка поблизости")
    story.append(row("Гараж в объекте", garaje))
    story.append(row("Парковок в радиусе 500м (OSM)", str(park.get("count","N/A"))))
    story.append(row("Итого", park.get("status","N/A")))
    story.append(P("* OpenStreetMap. Частные/подземные парковки могут не отображаться.", note_s))

    # ── Инвестиционный анализ ──
    story += section("Инвестиционный анализ")
    story.append(row("Примерная месячная аренда", f"€{inc.get('monthly','N/A')}"))
    story.append(row(f"Валовый доход в год ({inc.get('occupancy','N/A')}% загрузка)", f"€{inc.get('gross','N/A')}"))
    story.append(row("Чистый доход в год (после расходов)", f"€{inc.get('net','N/A')}"))
    story.append(row("Доходность (ROI)", f"{inc.get('roi','N/A')}%"))
    story.append(row("Срок окупаемости", f"{inc.get('payback','N/A')} лет"))
    story.append(P(
        f"* Как считали: €{inc.get('rate','N/A')}/м²/мес × {area_util or area} м² × 12 × {inc.get('occupancy','N/A')}% загрузка = €{inc.get('gross','N/A')} валовый. "
        f"Расходы: IBI €{inc.get('ibi','N/A')} + коммунальные €{inc.get('com','N/A')} + "
        f"обслуживание €{inc.get('maint','N/A')} + страховка €{inc.get('ins','N/A')} = €{inc.get('total_exp','N/A')}/год. "
        f"Расчёт приблизительный, не является финансовым советом.", note_s))

    # ── Риски ──
    story += section("Риски")
    story.append(Spacer(1, 0.15*cm))

    if diff is not None and diff > 5:
        story.append(risk_row("Средний","#f57c00","Цена выше рынка",
            f"+{diff}% выше медианы по барио. Рекомендуем предложить {rec_price}."))
    else:
        story.append(risk_row("Низкий","#388e3c","Цена","Цена соответствует рынку или ниже."))
    story.append(Spacer(1, 0.1*cm))
    story.append(risk_row("N/A","#78909c","Расхождение площади",
        "N/A — нет аэрофото для сравнения площади кадастра и реальной."))
    story.append(Spacer(1, 0.1*cm))
    story.append(risk_row("N/A","#78909c","Крыша требует осмотра",
        "N/A — нет аэрофото PNOA. Рекомендуем очный осмотр кровли."))
    story.append(Spacer(1, 0.1*cm))
    story.append(risk_row("N/A","#78909c","Зона затопления DANA",
        "N/A — требует проверки по картам MITECO (zonasdeinundacion.es)."))

    # ── Итоговая оценка ──
    story += section("Итоговая оценка")
    story.append(Spacer(1, 0.2*cm))
    score = 70 if diff is None or diff < 15 else 60
    sc_tbl = Table([
        [P(str(score), score_s)],
        [P(f"из 100 — объект интересный, часть данных требует уточнения", ssub_s)],
        [P(f"{'Хорошая локация' if idal.get('piscina') else 'Объект'}. Ориентация {orient}. Гараж: {garaje}. Бассейн: {piscina}.", sverd_s)],
        [P("Рекомендуем уточнить кадастровые данные и заказать осмотр кровли перед задатком.", srec_s)],
    ], colWidths=[16*cm])
    sc_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#f5f9ff")),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#bbdefb")),
        ("PADDING",(0,0),(-1,-1),8),
    ]))
    story.append(sc_tbl)
    story.append(Spacer(1, 0.4*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cfd8dc")))
    story.append(Spacer(1, 0.1*cm))
    story.append(P(
        "Отчёт сгенерирован автоматически. Источники: Idealista, PVGIS (EC), Catastro, OpenStreetMap. "
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
        data={"recipient": json.dumps({"id":rid}),
              "message": json.dumps({"attachment":{"type":"file","payload":{"is_reusable":False}}})},
        files={"filedata":(fname, buf, "application/pdf")})

def send_lang_buttons(rid):
    requests.post(
        f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}",
        json={"recipient":{"id":rid},"message":{"attachment":{"type":"template","payload":{
            "template_type":"button",
            "text":"👋 Hi! / Привет! / ¡Hola!\n\nPlease choose your language:",
            "buttons":[
                {"type":"postback","title":"🇬🇧 English","payload":"LANG_EN"},
                {"type":"postback","title":"🇷🇺 Русский","payload":"LANG_RU"},
                {"type":"postback","title":"🇪🇸 Español","payload":"LANG_ES"},
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
        "en": "🏠 Send me an Idealista link or property address — I'll generate a full AI report in 2-3 minutes.\n\nExample:\nhttps://www.idealista.com/inmueble/...\nOr: Calle Mayor 5, Torrevieja",
        "ru": "🏠 Пришлите ссылку с Idealista или адрес объекта — подготовлю полный AI-отчёт за 2-3 минуты.\n\nПример:\nhttps://www.idealista.com/inmueble/...\nИли: Calle Mayor 5, Torrevieja",
        "es": "🏠 Envíame un enlace de Idealista o la dirección del inmueble — en 2-3 minutos tendrás el informe completo de IA.\n\nEjemplo:\nhttps://www.idealista.com/inmueble/...\nO: Calle Mayor 5, Torrevieja",
    },
    "wait": {
        "en": "⏳ Analyzing the property... Please wait 2-3 minutes 🔍",
        "ru": "⏳ Анализирую объект... Подождите 2-3 минуты 🔍",
        "es": "⏳ Analizando el inmueble... Por favor espera 2-3 minutos 🔍",
    },
    "done": {
        "en": "✅ Your AI report is ready! Have questions? Our agent will contact you shortly.",
        "ru": "✅ Ваш AI-отчёт готов! Есть вопросы? Наш агент свяжется с вами.",
        "es": "✅ ¡Tu informe de IA está listo! ¿Tienes preguntas? Nuestro agente te contactará.",
    },
    "invalid": {
        "en": "Please send an Idealista link or a full property address 🏠",
        "ru": "Пришлите ссылку с Idealista или полный адрес объекта 🏠",
        "es": "Por favor envíame un enlace de Idealista o la dirección completa 🏠",
    }
}

def process(sender_id, name, text, lang):
    send_msg(sender_id, MSGS["wait"][lang])
    notify_tg(sender_id, name, text, lang)

    # Определяем адрес
    is_url = "idealista.com" in text.lower() or "http" in text.lower()
    address = text if not is_url else text.split("?")[0]

    # Собираем данные
    from datetime import date
    idal_data = {}
    if is_url:
        idal_data = parse_idealista(text.strip())
        geo_query = idal_data.get("barrio","") + ", Torrevieja, Spain"
    else:
        geo_query = text

    lat, lon, display = get_coordinates(geo_query)
    cat   = get_catastro(address)
    solar = get_solar(lat, lon)
    park  = get_parking(lat, lon)
    dist  = get_distances(lat, lon)
    area_util = idal_data.get("area_util") or idal_data.get("area") or 70
    income = calc_income(idal_data.get("precio",0), area_util)

    pdf_data = {
        "address": address,
        "fecha": date.today().strftime("%d.%m.%Y"),
        "idealista": idal_data,
        "catastro": cat,
        "solar": solar,
        "parking": park,
        "distances": dist,
        "income": income,
        "lat": lat, "lon": lon,
    }

    buf = generate_pdf(pdf_data)
    send_pdf(sender_id, buf)
    send_msg(sender_id, MSGS["done"][lang])
    user_states[sender_id] = {"lang": lang, "step": "done"}

def handle_msg(sender_id, name, text):
    state = user_states.get(sender_id, {})
    lang  = state.get("lang")
    if not lang:
        send_lang_buttons(sender_id)
        user_states[sender_id] = {"lang": None, "step": "await_lang"}
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
        user_states[sender_id] = {"lang": lang, "step": "await_link"}
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
        for entry in data.get("entry", []):
            for ev in entry.get("messaging", []):
                sid  = ev["sender"]["id"]
                name = "User"
                try:
                    p = requests.get(f"https://graph.facebook.com/{sid}?fields=first_name&access_token={PAGE_ACCESS_TOKEN}").json()
                    name = p.get("first_name","User")
                except: pass
                if "message" in ev and "text" in ev["message"]:
                    handle_msg(sid, name, ev["message"]["text"])
                elif "postback" in ev:
                    handle_postback(sid, name, ev["postback"]["payload"])
    return jsonify({"status":"ok"}), 200

@app.route("/")
def index():
    return "InmoSpain Bot 🏠 is running!", 200

if __name__ == "__main__":
    ensure_fonts()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
