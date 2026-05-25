import os
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Переменные окружения (настроим на Railway)
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "inmospain2025")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Определяем язык по тексту сообщения
def detect_language(text):
    text = text.lower()
    ru_words = ["привет", "отчет", "отчёт", "дом", "квартира", "купить", "цена", "район"]
    es_words = ["hola", "informe", "casa", "piso", "comprar", "precio", "barrio", "buenas"]
    
    for word in ru_words:
        if word in text:
            return "ru"
    for word in es_words:
        if word in text:
            return "es"
    return "en"

# Отправляем сообщение в Messenger
def send_message(recipient_id, message_text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    requests.post(url, json=payload)

# Отправляем кнопки выбора языка
def send_language_buttons(recipient_id):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": "👋 Hi! / Привет! / ¡Hola!\n\nPlease choose your language:",
                    "buttons": [
                        {"type": "postback", "title": "🇬🇧 English", "payload": "LANG_EN"},
                        {"type": "postback", "title": "🇷🇺 Русский", "payload": "LANG_RU"},
                        {"type": "postback", "title": "🇪🇸 Español", "payload": "LANG_ES"}
                    ]
                }
            }
        }
    }
    requests.post(url, json=payload)

# Уведомление тебе в Telegram
def notify_telegram(sender_id, sender_name, message, lang):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    lang_emoji = {"en": "🇬🇧", "ru": "🇷🇺", "es": "🇪🇸"}.get(lang, "🌍")
    text = (
        f"🏠 *Новый запрос InmoSpain Bot*\n\n"
        f"👤 {sender_name} (ID: {sender_id})\n"
        f"{lang_emoji} Язык: {lang}\n"
        f"💬 Сообщение: {message}\n\n"
        f"👉 Ответить: https://www.facebook.com/messages/t/{sender_id}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })

# Хранилище состояний пользователей (в памяти)
user_states = {}

# Обработка сообщений
def handle_message(sender_id, sender_name, message_text):
    text = message_text.strip()
    state = user_states.get(sender_id, {})
    lang = state.get("lang", None)

    # Если язык не выбран — показываем кнопки
    if not lang:
        send_language_buttons(sender_id)
        user_states[sender_id] = {"lang": None, "step": "awaiting_lang"}
        return

    # Если ждём ссылку/адрес
    if state.get("step") == "awaiting_link":
        # Проверяем что это похоже на ссылку или адрес
        is_link = "idealista" in text.lower() or "fotocasa" in text.lower() or "http" in text.lower()
        is_address = len(text) > 10

        if is_link or is_address:
            # Отправляем подтверждение
            messages = {
                "en": f"⏳ Got it! Analyzing the property...\n\nI'll send your full AI report in 2-3 minutes. Please wait! 🔍",
                "ru": f"⏳ Получила! Анализирую объект...\n\nПришлю полный AI-отчёт через 2-3 минуты. Пожалуйста, подождите! 🔍",
                "es": f"⏳ ¡Recibido! Analizando el inmueble...\n\n¡Te enviaré el informe completo de IA en 2-3 minutos. Por favor espera! 🔍"
            }
            send_message(sender_id, messages.get(lang, messages["en"]))
            
            # Уведомляем тебя в Telegram
            notify_telegram(sender_id, sender_name, text, lang)
            
            # Сбрасываем состояние
            user_states[sender_id] = {"lang": lang, "step": "waiting_report"}
        else:
            messages = {
                "en": "Please send me an Idealista link or a property address 🏠",
                "ru": "Пожалуйста, пришлите ссылку с Idealista или адрес объекта 🏠",
                "es": "Por favor envíame un enlace de Idealista o la dirección del inmueble 🏠"
            }
            send_message(sender_id, messages.get(lang, messages["en"]))
        return

    # Если уже ждём следующего запроса
    if state.get("step") == "waiting_report":
        messages = {
            "en": "Your report is being prepared! Meanwhile, do you have another property to check?",
            "ru": "Ваш отчёт готовится! Есть ещё объект для проверки?",
            "es": "¡Tu informe está en preparación! ¿Tienes otro inmueble para consultar?"
        }
        send_message(sender_id, messages.get(lang, messages["en"]))

# Обработка postback (нажатие кнопок)
def handle_postback(sender_id, sender_name, payload):
    lang_map = {
        "LANG_EN": "en",
        "LANG_RU": "ru", 
        "LANG_ES": "es"
    }
    
    if payload in lang_map:
        lang = lang_map[payload]
        user_states[sender_id] = {"lang": lang, "step": "awaiting_link"}
        
        messages = {
            "en": "🏠 Great choice! Send me an Idealista link or property address and I'll generate a full AI report:\n\n• Roof condition 🏗️\n• Solar potential ☀️\n• Price comparison 💰\n• Days on market 📅\n• Similar properties nearby 🗺️\n• Unauthorized extensions ⚠️\n\nExample:\nhttps://www.idealista.com/inmueble/...\n\nOr just the address: Calle Mayor 5, Murcia",
            "ru": "🏠 Отлично! Пришлите ссылку с Idealista или адрес объекта, и я подготовлю полный AI-отчёт:\n\n• Состояние крыши 🏗️\n• Солнечный потенциал ☀️\n• Сравнение цен 💰\n• Дней на рынке 📅\n• Похожие объекты рядом 🗺️\n• Незарегистрированные пристройки ⚠️\n\nПример:\nhttps://www.idealista.com/inmueble/...\n\nИли просто адрес: Calle Mayor 5, Murcia",
            "es": "🏠 ¡Perfecto! Envíame un enlace de Idealista o la dirección del inmueble y prepararé un informe completo de IA:\n\n• Estado del tejado 🏗️\n• Potencial solar ☀️\n• Comparación de precios 💰\n• Días en el mercado 📅\n• Inmuebles similares cercanos 🗺️\n• Construcciones no registradas ⚠️\n\nEjemplo:\nhttps://www.idealista.com/inmueble/...\n\nO simplemente la dirección: Calle Mayor 5, Murcia"
        }
        send_message(sender_id, messages[lang])
        notify_telegram(sender_id, sender_name, f"Выбрал язык: {lang}", lang)

# Webhook верификация (для Meta)
@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

# Приём сообщений от Meta
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                sender_id = event["sender"]["id"]
                sender_name = "User"
                
                # Получаем имя пользователя
                try:
                    profile_url = f"https://graph.facebook.com/{sender_id}?fields=first_name&access_token={PAGE_ACCESS_TOKEN}"
                    profile = requests.get(profile_url).json()
                    sender_name = profile.get("first_name", "User")
                except:
                    pass
                
                # Обработка текстового сообщения
                if "message" in event and "text" in event["message"]:
                    handle_message(sender_id, sender_name, event["message"]["text"])
                
                # Обработка нажатия кнопок
                elif "postback" in event:
                    handle_postback(sender_id, sender_name, event["postback"]["payload"])
    
    return jsonify({"status": "ok"}), 200

@app.route("/")
def index():
    return "InmoSpain Bot is running! 🏠", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
