import sqlite3
import ollama
import pandas as pd
from datetime import datetime
import streamlit as st
import requests
import time
import html

DB_NAME = "brand_monitor.db"
DEFAULT_OLLAMA_MODEL = "gpt-oss:120b-cloud"

# creating db
def init_db():
    """Initializes the SQLite database"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS brand_mentions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand TEXT NOT NULL,  
                source TEXT NOT NULL,
                text TEXT NOT NULL,
                url TEXT,
                timestamp DATETIME,
                sentiment TEXT,
                topic TEXT,
                urgency TEXT
            )
        """)

# inserting into the db
def add_mention(brand_name, source, text, url, timestamp):
    """Adds a new mention. Returns True if added, False if duplicate."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM brand_mentions WHERE url=?", (url,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO brand_mentions (brand, source, text, url, timestamp) VALUES (?, ?, ?, ?, ?)",
                (brand_name, source, text, url, timestamp)
            )
        conn.commit()

# getting information for any brand mention from the db
def get_all_mentions_as_df(brand_name):
    """Fetches all mentions for a SPECIFIC brand."""
    with sqlite3.connect(DB_NAME) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM brand_mentions WHERE brand = ? ORDER BY timestamp DESC",
            conn,
            params=(brand_name,)
        )
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df

# fetch hacker news for any brand mention
def fetch_hn_mentions(brand_name, max_pages=3) -> int:
    """
    Fetches Hacker News comments mentioning the brand using the Algolia API.
    Returns the number of new comments added.
    """

    url = "https://hn.algolia.com/api/v1/search_by_date"
    added_count = 0

    # Get existing URLs from DB to prevent duplicates
    existing_df = get_all_mentions_as_df(brand_name)

    if not existing_df.empty and "url" in existing_df.columns:
        existing_urls = set(existing_df["url"].dropna().tolist())
    else:
        existing_urls = set()

    processed_urls = set()

    try:
        # Algolia allows up to 20 pages (1000 results max)
        for page in range(min(max_pages, 20)):

            params = {
                "query": brand_name,
                "tags": "comment",
                "page": page,
                "hitsPerPage": 50
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code != 200:
                st.warning(
                    f"Algolia API returned HTTP {response.status_code} on page {page}"
                )
                break

            data = response.json()
            hits = data.get("hits", [])

            if not hits:
                break

            for hit in hits:

                comment_id = hit.get("objectID")

                if not comment_id:
                    continue

                post_url = f"https://news.ycombinator.com/item?id={comment_id}"

                # Skip duplicates
                if (
                    post_url in processed_urls
                    or post_url in existing_urls
                ):
                    continue

                raw_text = hit.get("comment_text", "")

                created_utc = hit.get("created_at_i")

                try:
                    timestamp = datetime.fromtimestamp(
                        int(created_utc)
                    )
                except (TypeError, ValueError):
                    timestamp = datetime.now()

                add_mention(
                    brand_name=brand_name,
                    source="Hacker News",
                    text=raw_text,
                    url=post_url,
                    timestamp=timestamp
                )

                processed_urls.add(post_url)
                added_count += 1

            # Avoid hammering the API
            time.sleep(1)

        return added_count

    except Exception as e:
        st.error(f"Hacker News API Error: {e}")
        return 0

def clean_html(df):
    """Utility to strip HTML tags from the entire text column."""
    df["text"] = df["text"].str.replace(r'<[^<>]*>', ' ', regex=True)
    df["text"] = df["text"].apply(html.unescape)
    df["text"] = df["text"].str.replace(r'\s+', ' ', regex=True).str.strip()
    return df

## ANALYSIS
# get sentiment
def get_sentiment(text):
    prompt_template = "Analyze the sentiment of the following text. Is it Positive, Negative or Neutral? Answer with only one word."

    full_prompt = f"{prompt_template}\n\nText to analyze:\n{text}"

    try:
        response = ollama.generate(
            model=DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        # the response key is 'response' for ollama.generate
        return response['response'].strip()
    except Exception as e:
        st.error(f"Ollama error (Topic): {e}")
        return None
    
# get topic
def get_topic(text):
    """Classifies the text into topics using ollama.generate."""
    prompt_template = """
        You are a text analysis engine. Your task is to read the following text and assign the **single best-fitting** 
        category from the list below.
        **Categories & Definitions:**

        * **Customer Service Issue**: Problems with support, billing, shipping, or account interaction.
        * **Product Defect/Bug**: The product is broken, crashing, or not working as intended.
        * **High Price Complaint**: Feedback that the product or service is too expensive.
        * **Positive Review**: General praise, compliments, or success stories.
        * **Competitor Comparison**: The text explicitly mentions a competitor.
        * **Feature Request**: A suggestion for a new feature or an improvement to an existing one.
        * **PR/News**: Text that appears to be a press release, news article, or public announcement.
        * **Other**: Any other topic that does not clearly fit one of the categories above (e.g., general inquiry, spam, wrong email).

        **Rules:**
        1.  Choose only **one** category.
        2.  If none of the specific categories are a good match, you must use **'Other'**.
        3.  Output only the category name.
    """

    # Combine the instruction and the text into a single prompt
    full_prompt = f"{prompt_template}\n\nText to analyze:\n{text}"

    try:
        response = ollama.generate(
            model=DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        return response["response"].strip()
    except Exception as e:
        st.error(f"Ollama error (Topic): {e}")
        return None

# get urgency
def get_urgency(text):
    """Determines the urgency of a text using ollama.generate."""
    prompt_template = """
    You are a PR crisis manager. Read this text. Is this a 'High Urgency' issue
    (e.g., safety risk, potential PR crisis, going viral) or a 'Low Urgency'
    issue (e.g., single user complaint, question)? Answer with 'High Urgency' or 'Low Urgency'.
    """

    # Combine the instruction and the text into a single prompt
    full_prompt = f"{prompt_template}\n\nText to analyze:\n{text}"

    try:
        response = ollama.generate(
            model=DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        return response["response"].strip()
    except Exception as e:
        st.error(f"Ollama error (Urgency): {e}")
        return None
    

def update_mention_analysis(mention_id, sentiment, topic, urgency):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
                UPDATE brand_mentions
                SET sentiment = ?, topic = ?, urgency = ? WHERE id = ?
            """, 
            (sentiment, topic, urgency, mention_id)
        )
        conn.commit()
        
def generate_positive_report_summary(df):
    """Generates a detailed, high-level summary of POSITIVE feedback."""
    positive_texts = "\n----\n".join(df[df["sentiment"] == "Positive"]["text"].tolist())
    if not positive_texts:
        return "No positive feedback found to summarize."
    
    positive_texts_subset = positive_texts[0:4000]

    full_prompt = f"""
    You are an expert customer experience analyst.
    Your task is to analyze the following POSITIVE customer feedback and identify the main strengths appreciated by customers.

    Please provide a concise, business-oriented summary in exactly 3 bullet points covering:
    1. The top recurring points or aspects customers praised.
    2. The underlying strengths or reasons behind this positive sentiment (e.g., product quality, service experience, brand trust, etc.).
    3. The potential opportunities for the brand to further capitalize on these strengths.

    Be objective, avoid repetition, and use short, impactful sentences.

    POSITIVE CUSTOMER FEEDBACK:
    {positive_texts_subset}
    """

    try:
        response = ollama.generate(
            model = DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        return response['response'].strip()
    except Exception as e:
        st.error(f"Ollama error (Positive Summary): {e}")
        return "Error generating summary."
    
def generate_negative_report_summary(df):
    """Generates a detailed, high-level summary of NEGATIVE feedback."""
    negative_texts = "\n---\n".join(df[df['sentiment'] == 'Negative']['text'].tolist())
    if not negative_texts:
        return "No negative feedback found to summarize."

    negative_texts_subset = negative_texts[:4000]
    full_prompt = f"""
    You are an expert customer experience analyst.
    Your task is to analyze the following NEGATIVE customer feedback and identify the most common pain points.

    Please provide a concise, business-oriented summary in exactly 3 bullet points covering:
    1. The top recurring complaints or issues customers mentioned.
    2. The underlying cause or pattern behind these issues (if visible).
    3. The potential impact or area of improvement for the brand.

    Be objective, avoid repetition, and use short, impactful sentences.

    NEGATIVE CUSTOMER FEEDBACK:
    {negative_texts_subset}
    """

    try:
        response = ollama.generate(
            model=DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        return response["response"].strip()
    except Exception as e:
        st.error(f"Ollama error (Negative Summary): {e}")
        return "Error generating summary."

def generate_report_summary(df):
    """Generates a high-level summary of NEGATIVE feedback using ollama.generate."""
    negative_texts = "\n---\n".join(df[df['sentiment'] == 'Negative']['text'].tolist())
    if not negative_texts:
        return "No negative feedback found to summarize."

    negative_texts_subset = negative_texts[:4000]

    # The prompt already contains the text, so it's the 'full_prompt'
    full_prompt = f"""
        You are a product strategist. Read the following customer suggestions and feature requests.
        Analyze the underlying needs and ideas.

        Based *only* on these comments, provide a bullet-point summary of:
        1.  **Top Suggestions:** What are the most common or impactful ideas users are asking for?
        2.  **Future Opportunities:** What new features or future directions should the company consider working on based on these suggestions?

        Group similar ideas together.

        CUSTOMER SUGGESTIONS:
        {negative_texts_subset}
        """

    try:
        response = ollama.generate(
            model=DEFAULT_OLLAMA_MODEL,
            prompt=full_prompt
        )
        return response["response"].strip()
    except Exception as e:
        st.error(f"Ollama error (Negative Summary): {e}")
        return "Error generating summary."
