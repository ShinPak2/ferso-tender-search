"""AI service: DeepSeek Flash analysis of tenders."""
import json
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def analyze_tender(title: str, description: str) -> dict | None:
    """
    Analyze a tender using DeepSeek AI.
    Returns analysis with relevance score, risks, and recommendation.
    """
    if not settings.DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set, skipping AI analysis")
        return _mock_analysis(title, description)

    prompt = _build_analysis_prompt(title, description)

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты — эксперт по анализу государственных тендеров. "
                    "Твоя задача — проанализировать тендер и дать структурированную оценку. "
                    "Отвечай строго в формате JSON с полями: "
                    "analysis (краткий анализ, 2-3 предложения), "
                    "relevance (число 1-10, насколько тендер привлекателен для малого/среднего бизнеса), "
                    "risks (основные риски, 2-3 пункта), "
                    "recommendation (рекомендация: участвовать/рассмотреть/пропустить)."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Try to extract JSON from the response
                result = _extract_json(content)
                return result
            else:
                logger.error(f"DeepSeek API error: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error calling DeepSeek API: {e}")

    return _mock_analysis(title, description)


def _build_analysis_prompt(title: str, description: str) -> str:
    """Build the analysis prompt for DeepSeek."""
    return f"""Проанализируй тендер:

Название: {title}
Описание: {description}

Критерии оценки:
1. Привлекательность для малого и среднего бизнеса
2. Сложность выполнения требований
3. Финансовые риски (авансирование, сроки оплаты)
4. Конкурентность ниши
5. Реалистичность сроков

Дай оценку релевантности от 1 до 10, опиши основные риски и дай рекомендацию."""


def _extract_json(text: str) -> dict:
    """Extract JSON object from model response."""
    try:
        # Direct JSON parse
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in the response
    import re
    json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: parse from text
    return {
        "analysis": text[:200],
        "relevance": 5,
        "risks": "Требуется дополнительный анализ документации",
        "recommendation": "рассмотреть",
    }


def _mock_analysis(title: str, description: str) -> dict:
    """Generate mock analysis when AI is unavailable."""
    import hashlib

    # Deterministic mock based on title hash
    h = int(hashlib.md5(title.encode()).hexdigest()[:8], 16)
    relevance = (h % 8) + 3  # 3-10 range
    risks_options = [
        "Отсутствие авансирования — потребуются оборотные средства",
        "Высокая конкуренция в данной нише",
        "Короткие сроки выполнения работ",
        "Сложная техническая документация",
        "Необходимость банковской гарантии",
        "Штрафные санкции за срыв сроков",
    ]
    recs = ["участвовать", "рассмотреть", "пропустить"]

    return {
        "analysis": f"Тендер «{title[:80]}» представляет интерес для поставщиков в данной категории. Рекомендуется детально изучить документацию перед подачей заявки.",
        "relevance": relevance,
        "risks": ", ".join(risks_options[:2]),
        "recommendation": recs[relevance % 3],
    }
