
import random
from html import escape

from ui.texts import private_cards_text


CARD_LABELS = {
    "kasb": "💼 Kasb",
    "sogliq": "❤️ Sog‘liq",
    "yosh": "🎂 Yosh",
    "hobbi": "🎯 Hobbi",
    "xarakter": "🧠 Xarakter",
    "bagaj": "🎒 Bagaj",
    "sir": "🤫 Sir",
}

CARDS = {
    "kasb": [
        "Shifokor",
        "Muhandis",
        "Dasturchi",
        "Agronom",
        "Elektrik",
        "Oshpaz",
        "Harbiy",
        "Biolog",
        "Quruvchi",
        "Psixolog",
        "Haydovchi",
        "Kimyogar",
    ],
    "sogliq": [
        "Mutlaqo sog‘lom",
        "Yengil allergiya",
        "Ko‘rish qobiliyati zaif",
        "Surunkali bel og‘rig‘i",
        "Immuniteti zaif",
        "Yaxshi jismoniy holat",
        "Yuqori qon bosimi",
        "Tez charchaydi",
    ],
    "yosh": [
        "18 yosh",
        "23 yosh",
        "29 yosh",
        "34 yosh",
        "41 yosh",
        "48 yosh",
        "55 yosh",
        "62 yosh",
    ],
    "hobbi": [
        "Baliq ovlash",
        "Kimyo tajribalari",
        "Bog‘dorchilik",
        "Mexanika",
        "Tibbiy kitoblar",
        "Ovchilik",
        "Elektronika",
        "Ovqat tayyorlash",
    ],
    "xarakter": [
        "Sovuqqon",
        "Jasur",
        "Shubhali",
        "Jamoaviy",
        "Qaysar",
        "Ziyrak",
        "Vahimachi",
        "Ishonuvchan",
    ],
    "bagaj": [
        "Dori qutisi",
        "Radio",
        "Asboblar to‘plami",
        "Konserva zaxirasi",
        "Fonarik",
        "Arqon",
        "Filtrli niqob",
        "Bo‘sh ryukzak",
    ],
    "sir": [
        "Bir necha kunlik oziq-ovqat zaxirangiz bor.",
        "Sizda muhim ma’lumot bor, lekin uni yashiryapsiz.",
        "Balandlikdan qo‘rqasiz.",
        "Boshqalarga aytmagan foydali ko‘nikmangiz bor.",
        "Sizda noma’lum kasallik alomatlari paydo bo‘lgan.",
        "Bunkerga kirishingizga yordam beradigan tanishingiz bor.",
        "Bir buyumingizning asl vazifasini yashiryapsiz.",
        "Favqulodda vaziyatda ilgari xato qaror qilgansiz.",
    ],
}


def deal_cards() -> dict[str, str]:
    """Har bir toifadan bittadan karta tanlaydi."""
    return {
        category: random.choice(cards)
        for category, cards in CARDS.items()
    }


def format_private_cards(cards: dict[str, str]) -> str:
    return private_cards_text(cards)


def format_card_detail(category: str, value: str) -> str:
    label = CARD_LABELS.get(category)
    if label is None:
        label = f"🎴 {escape(category.title())}"
    else:
        label = escape(label)
    return f"{label}: <b>{escape(value)}</b>"
