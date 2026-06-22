"""AI-powered keyword suggestions for tender search."""
import json
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# ── Router ────────────────────────────────────────────────────────

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class SuggestionResponse(BaseModel):
    keywords: list[str]
    source: str  # "ai" or "fallback"


# Fallback suggestions by category
FALLBACK_SUGGESTIONS: dict[str, list[str]] = {
    "строитель": [
        "строительные работы", "капитальный ремонт", "реконструкция зданий",
        "благоустройство территории", "дорожное строительство", "проектные работы",
        "строительный контроль", "отделочные работы",
    ],
    "ремонт": [
        "капитальный ремонт", "текущий ремонт", "ремонт дорог",
        "ремонт оборудования", "ремонт фасадов", "ремонт кровли",
    ],
    "ит": [
        "разработка ПО", "системное администрирование", "техническая поддержка",
        "информационная безопасность", "базы данных", "облачные сервисы",
        "1С сопровождение", "сайты и порталы",
    ],
    "медицин": [
        "медицинское оборудование", "лекарственные препараты", "расходные материалы",
        "лабораторные исследования", "медицинские изделия", "диагностика",
    ],
    "питание": [
        "организация питания", "продукты питания", "школьное питание",
        "кейтеринг", "поставка продуктов", "столовые услуги",
    ],
    "охрана": [
        "охрана объектов", "пожарная сигнализация", "видеонаблюдение",
        "пультовая охрана", "системы безопасности", "контроль доступа",
    ],
    "транспорт": [
        "транспортные услуги", "перевозка грузов", "пассажирские перевозки",
        "аренда транспорта", "техническое обслуживание автомобилей", "транспортная логистика",
    ],
    "уборка": [
        "клининговые услуги", "уборка помещений", "вывоз мусора",
        "уборка территории", "дезинфекция", "химчистка",
    ],
    "канцелярия": [
        "канцелярские товары", "офисная бумага", "хозяйственные товары",
        "офисная мебель", "оргтехника", "расходные материалы для принтеров",
    ],
    "обучение": [
        "повышение квалификации", "образовательные услуги", "профессиональная переподготовка",
        "тренинги", "семинары", "дистанционное обучение",
    ],
}


@router.get("/api/suggestions", response_model=SuggestionResponse)
async def get_suggestions(keyword: str = Query(..., min_length=2, description="Keyword to get suggestions for")):
    """
    Get AI-powered keyword suggestions for tender search.
    When the user enters a keyword, this endpoint returns similar/related keywords.
    Uses DeepSeek AI for intelligent suggestions, falls back to category-based suggestions.
    """
    # Try AI first
    ai_suggestions = await _ai_suggestions(keyword)
    if ai_suggestions:
        return SuggestionResponse(keywords=ai_suggestions, source="ai")

    # Fallback: category-based suggestions
    fallback = _fallback_suggestions(keyword)
    return SuggestionResponse(keywords=fallback, source="fallback")


async def _ai_suggestions(keyword: str) -> list[str] | None:
    """Get suggestions from DeepSeek AI."""
    if not settings.DEEPSEEK_API_KEY:
        return None

    prompt = (
        f"Пользователь ищет тендеры по ключевому слову: «{keyword}».\n"
        f"Предложи 5-8 похожих или связанных ключевых слов/фраз для поиска тендеров "
        f"на русском языке. Это должны быть формулировки, которые реально используются "
        f"в названиях государственных закупок на сайте zakupki.gov.ru.\n"
        f"Ответь строго в формате JSON: {{'keywords': ['слово1', 'слово2', ...]}}.\n"
        f"Только ключевые слова, без пояснений."
    )

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 200,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                result = _extract_keywords(content)
                if result:
                    return result
    except Exception as e:
        logger.error(f"AI suggestions error: {e}")

    return None


def _fallback_suggestions(keyword: str) -> list[str]:
    """Generate fallback suggestions based on keyword categories."""
    keyword_lower = keyword.lower().strip()
    suggestions = []

    # Exact match in categories
    for category, keywords in FALLBACK_SUGGESTIONS.items():
        if category in keyword_lower:
            suggestions.extend(keywords[:5])

    # Fuzzy: partial matches
    if not suggestions:
        for category, keywords in FALLBACK_SUGGESTIONS.items():
            # Check if any word from keyword matches category
            kw_words = set(keyword_lower.split())
            cat_words = set(category.split())
            if kw_words & cat_words:
                suggestions.extend(keywords[:4])

    # Deduplicate and filter out exact keyword match
    suggestions = list(dict.fromkeys(
        s for s in suggestions
        if s.lower() != keyword_lower and keyword_lower not in s.lower()[:len(keyword_lower)]
    ))

    return suggestions[:6] if suggestions else [
        f"{keyword} тендер",
        f"закупка {keyword}",
        f"поставка {keyword}",
        f"оказание услуг {keyword}",
        f"выполнение работ {keyword}",
    ]


def _extract_keywords(text: str) -> list[str] | None:
    """Extract keywords list from AI response."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "keywords" in data:
            return data["keywords"][:8]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array
    import re
    match = re.search(r'\[(.*?)\]', text, re.DOTALL)
    if match:
        try:
            arr = json.loads(f"[{match.group(1)}]")
            if isinstance(arr, list):
                return arr[:8]
        except json.JSONDecodeError:
            pass

    return None
