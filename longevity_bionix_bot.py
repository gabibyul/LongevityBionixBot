import requests
import re
from typing import List, Dict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from openai import OpenAI
from datetime import datetime

TELEGRAM_TOKEN = "7918970741:AAEk6UNuzfRk0zU4TLv_dn6Xh0I_F9lbLVg"
client = OpenAI(
    base_url="http://80.209.242.40:8000/v1",
    api_key="dummy-key"
)

DIRECTIONS = {
    "Эпигенетика": ["epigenetic", "methylation", "histone"],
    "Метаболизм": ["metabolism", "mitochondria", "NAD", "AMPK"],
    "Иммунология": ["immunology", "immune", "inflammation", "cytokine"],
    "Сенесценция": ["senescence", "senescent", "p16", "SASP"],
    "Теломеры": ["telomere", "telomerase"],
    "Aging": ["aging", "ageing", "longevity", "lifespan"]
}

def fetch_pubmed_articles(query: str, max_results: int = 10) -> List[Dict]:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"}
    resp = requests.get(url, params=params).json()
    ids = resp.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    url2 = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
    xml = requests.get(url2, params=params2).text
    titles = re.findall(r"<ArticleTitle>(.*?)</ArticleTitle>", xml, re.DOTALL)
    abstracts = re.findall(r"<AbstractText.*?>(.*?)</AbstractText>", xml, re.DOTALL)
    dois = re.findall(r'<ELocationID EIdType="doi" ValidYN="Y">(.*?)</ELocationID>', xml)
    articles = []
    for i, pmid in enumerate(ids):
        title = titles[i] if i < len(titles) else ""
        abstract = abstracts[i] if i < len(abstracts) else ""
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        doi = dois[i] if i < len(dois) else ""
        articles.append({"pmid": pmid, "title": title, "abstract": abstract, "link": link, "doi": doi})
    return articles

def classify_directions(text: str) -> List[str]:
    found = []
    text = text.lower()
    for dir_name, keywords in DIRECTIONS.items():
        for kw in keywords:
            if kw in text:
                found.append(dir_name)
                break
    return found or ["Aging"]

def aggregate_by_direction(articles: List[Dict]) -> Dict[str, Dict]:
    result = {}
    for art in articles:
        dirs = classify_directions(art['title'] + ' ' + art['abstract'])
        for d in dirs:
            if d not in result:
                result[d] = {"support": 0, "articles": []}
            result[d]["support"] += 1
            result[d]["articles"].append(art)
    return result

def confidence_word(n_support: int) -> str:
    if n_support >= 7:
        return "Высокая (на основе рецензируемых публикаций)"
    elif n_support >= 4:
        return "Средняя (есть несколько подтверждений в литературе)"
    else:
        return "Низкая (ограниченное число публикаций)"

def gpt_generate_summary_ru(direction: str, article: Dict, n_support: int) -> str:
    prompt = (
        f"Напиши на русском языке короткое (до 150 слов) описание сути приоритетной задачи '{direction}' на базе этой статьи ('{article['title']}') и её аннотации."
        f"Используй научно-популярный стиль, не копируй аннотацию, а обобщи главное."
        f"\nВ статье: {article['abstract']}\n"
        f"Найдено supporting-источников: {n_support}."
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-instruct",
        messages=[
            {"role": "system", "content": "Ты эксперт по биомедицине, всегда отвечаешь по-русски."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=220,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()

def gpt_extract_keywords_ru(title: str, abstract: str, n: int = 7) -> str:
    prompt = (
        f"Выдели 5–7 ключевых слов или фраз по этой научной статье и её аннотации, перечисли их через запятую. Не добавляй лишний текст.\n"
        f"Название: {title}\nАннотация: {abstract}"
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-instruct",
        messages=[
            {"role": "system", "content": "Ты биомедицинский аналитик, всегда отвечаешь на русском."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=60,
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()

def get_supporting_articles(articles: List[Dict], exclude_pmid: str, limit: int = 3) -> List[Dict]:
    result = []
    for art in articles:
        if art['pmid'] == exclude_pmid:
            continuegir
        result.append({"title": art['title'], "link": art['link']})
        if len(result) >= limit:
            break
    return result

def format_answer(
    top_dir: str, date_str: str, article: Dict, doi: str,
    keywords: str, summary: str, n_support: int, supporting_list: List[Dict],
    confidence: str, consensus: str
) -> str:
    supporting_examples = ""
    for art in supporting_list:
        supporting_examples += f"{art['title']} 🔗 ({art['link']})\n"
    if not supporting_examples:
        supporting_examples = "Нет других supporting-статей\n"

    doi_part = f" (DOI: {doi})" if doi else ""
    return (
        f"Приоритетная задача 📋: {top_dir}\n"
        f"Дата 📅: {date_str}\n\n"
        f"Название статьи 📄: {article['title']}\n"
        f"Ссылка 🔗: {article['link']}{doi_part}\n"
        f"Ключевые слова 🏷️: {keywords}\n\n"
        f"Короткое описание ℹ️:\n{summary}\n\n"
        f"Оценка достоверности статьи 📊:\n"
        f"Вспомогательные источники 📚: {n_support}\n"
        f"Примеры:\n{supporting_examples}"
        f"Уверенность ✅: {confidence}\n"
        f"Консенсус 🤝: {consensus}\n"
        f"Тип источника 🗄️: PubMed\n"
        f"Качество ⭐: Аннотированные рецензируемые издания"
    )

async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = fetch_pubmed_articles("aging OR senescence OR epigenetic OR metabolism", max_results=15)
    if not articles:
        await update.message.reply_text("Сегодня не найдено новых релевантных статей.")
        return
    agg = aggregate_by_git initdirection(articles)
    if not agg:
        await update.message.reply_text("Нет задач по ключевым направлениям сегодня.")
        return
    top_dir = max(agg, key=lambda d: agg[d]['support'])
    entry = agg[top_dir]
    best_article = entry["articles"][0]
    keywords = gpt_extract_keywords_ru(best_article['title'], best_article['abstract'])
    summary = gpt_generate_summary_ru(top_dir, best_article, entry["support"])
    confidence = confidence_word(entry["support"])
    consensus = "Да (данные исследований согласуются)" if entry["support"] > 1 else "Нет (ограниченное число подтверждений)"
    now = datetime.now().strftime("%d.%m.%Y")
    supporting_articles = get_supporting_articles(entry["articles"], best_article['pmid'], limit=3)
    answer = format_answer(
        top_dir, now, best_article, best_article.get('doi', ''),
        keywords, summary, entry['support'], supporting_articles,
        confidence, consensus
    )
    await update.message.reply_text(answer)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я LongevityBionixBot.\n"
        "Я ежедневно нахожу для тебя ключевую задачу в науке о долголетии: статья, ссылка, краткий обзор и оценка достоверности.\n"
        "Введи /task, чтобы узнать задачу дня."
    )

def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("task", task))
    import asyncio
    asyncio.run(app.run_polling())

if __name__ == "__main__":
    run_bot()
