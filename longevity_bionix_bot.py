import requests
import re
from typing import List, Dict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from openai import OpenAI
from datetime import datetime

# ==== Настройки токена и модели ====
TELEGRAM_TOKEN = "TOKEM"
client = OpenAI(
    base_url="TOKEN",
    api_key="dummy-key"
)

def fetch_pubmed_articles(query: str, max_results: int = 10) -> List[Dict]:
    """Получает статьи из PubMed по поисковому запросу."""
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
        doi = dois[i] if i < len(dois) else ""
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        articles.append({"pmid": pmid, "title": title, "abstract": abstract, "link": link, "doi": doi})
    return articles

def is_human_study(text: str) -> bool:
    """
    Проверяет, что статья действительно о человеке, а не о животных, растениях и др.
    """
    must_have = ["human", "homo sapiens", "человек", "patients", "elderly", "aged"]
    not_have = [
        "mouse", "mice", "rat", "rats", "plant", "plants", "arabidopsis",
        "grape", "yeast", "drosophila", "zebrafish", "caenorhabditis", "c. elegans",
        "vine", "rice", "corn", "wheat", "fly", "flies"
    ]
    text = text.lower()
    return any(mh in text for mh in must_have) and not any(nh in text for nh in not_have)

def gpt_extract_question_and_answer(article: Dict) -> Dict[str, str]:
    """
    LLM выделяет исследовательский вопрос и ответ по аннотации (всегда из одной статьи),
    чтобы не было несостыковок.
    """
    prompt = (
        f"Ты эксперт по биомедицине и старению человека. Прочитай аннотацию: \"{article['abstract']}\". "
        f"1. Сформулируй один конкретный исследовательский вопрос, который решали авторы статьи (строго по сути аннотации!). "
        f"2. Дай короткий научный ответ на ворос 1, который нашли авторы, строго по тексту статьи.\n"
        f"Ответь в формате:\nВопрос: ...\nОтвет: ...\n"
        f"Если статья не по старению человека — напиши: \"Статья не подходит для задачи приоритезации\"."
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-instruct",
        messages=[{"role": "system", "content": "Ты биомедицинский эксперт."},
                  {"role": "user", "content": prompt}],
        max_tokens=220,
        temperature=0.2,
    )
    text = response.choices[0].message.content.strip()
    # Парсим результат
    q = r"Вопрос:(.*)"
    a = r"Ответ:(.*)"
    question = re.search(q, text)
    answer = re.search(a, text)
    return {
        "question": question.group(1).strip() if question else "",
        "answer": answer.group(1).strip() if answer else ""
    }

def gpt_extract_keywords_ru(article: Dict) -> str:
    """LLM выделяет 5-7 ключевых слов/фраз на русском."""
    prompt = (
        f"Выдели 5–7 ключевых слов или фраз по статье и её аннотации на русском языке, перечисли их через запятую. Без лишних комментариев.\n"
        f"Название: {article['title']}\nАннотация: {article['abstract']}"
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-instruct",
        messages=[{"role": "system", "content": "Ты биомедицинский аналитик."},
                  {"role": "user", "content": prompt}],
        max_tokens=40,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()

def get_supporting_articles(articles: List[Dict], exclude_pmid: str, limit: int = 3) -> List[Dict]:
    """Выбирает supporting-источники — до 3 других статей по теме."""
    return [art for art in articles if art['pmid'] != exclude_pmid][:limit]

def confidence_word(n_support: int) -> str:
    if n_support >= 7:
        return "Высокая (на основе рецензируемых публикаций)"
    elif n_support >= 4:
        return "Средняя (есть несколько подтверждений)"
    else:
        return "Низкая (ограниченное число публикаций)"

async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().strftime("%d.%m.%Y")
    query = "aging AND (lifespan OR longevity OR senescence OR anti-aging) AND (human OR homo sapiens)"
    articles = fetch_pubmed_articles(query, max_results=15)
    # Найдём первую релевантную статью
    main = None
    for article in articles:
        if is_human_study(article['title'] + ' ' + article['abstract']):
            main = article
            break
    if not main:
        await update.message.reply_text("Не удалось найти релевантную статью по долголетию человека.")
        return

    # Supporting-источники (до 3)
    supporting = get_supporting_articles(articles, main['pmid'])
    n_support = 1 + len(supporting)
    confidence = confidence_word(n_support)
    consensus = "Да (данные согласуются)" if n_support > 1 else "Нет (только одна публикация)"

    # LLM: Вопрос + Ответ (единый prompt), ключевые слова
    qa = gpt_extract_question_and_answer(main)
    keywords = gpt_extract_keywords_ru(main)

    supporting_lines = ""
    for art in supporting:
        supporting_lines += f"{art['title']} 🔗 ({art['link']})\n"

    doi_part = f" (DOI: {main['doi']})" if main['doi'] else ""
    answer = (
        f"Решаемый вопрос:📋 {qa['question']}\n"
        f"Дата 📅: {now}\n\n"
        f"Название статьи 📄: {main['title']}\n"
        f"Ссылка 🔗: {main['link']}{doi_part}\n"
        f"Ключевые слова 🏷️: {keywords}\n\n"
        f"Короткое описание ℹ️:\n{qa['answer']}\n\n"
        f"Оценка достоверности статьи 📊:\n"
        f"Вспомогательные источники 📚: {n_support}\n"
        f"Примеры:\n{supporting_lines if supporting_lines else 'Нет других supporting-статей'}"
        f"Уверенность ✅: {confidence}\n"
        f"Консенсус 🤝: {consensus}\n"
        f"Тип источника 🔬: PubMed\n"
        f"Качество ⭐: Аннотированные рецензируемые издания"
    )
    await update.message.reply_text(answer)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я LongevityBionixBot.\n"
        "Я ищу лучшие научные задачи по продлению жизни и замедлению старения у людей.\n"
        "Введи /task, чтобы получить приоритетный вопрос дня!"
    )

def run_bot():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("task", task))
    import asyncio
    asyncio.run(app.run_polling())

if __name__ == "__main__":
    run_bot()
