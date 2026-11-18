import os, re, json, time, sqlite3, urllib.parse, urllib.robotparser
from datetime import datetime, timezone
from typing import Optional
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = os.path.dirname(__file__)
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/knowledge.db")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
SITES = {
    "DarGlobal": {
        "base": "https://darglobal.co.uk",
        "seeds": ["https://darglobal.co.uk/", "https://darglobal.co.uk/press"],
    },
    "Wasalt": {
        "base": "https://wasalt.sa",
        "seeds": ["https://wasalt.sa/en/", "https://wasalt.sa/en/corporate",
                  "https://wasalt.sa/en/international"],
    },
}
HEADERS = {"User-Agent": "RealEstateResearchAssistant/1.0 (public website content; contact: admin@example.com)"}
app = FastAPI(title="Real Estate AI Assistant", version="1.0.0")
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY, company TEXT, url TEXT UNIQUE, title TEXT,
        content TEXT, fetched_at TEXT)""")
    con.commit()
    return con

def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        el.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return title, text[:30000]

def allowed_by_robots(url):
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    try:
        response = requests.get(
            robots_url,
            headers=HEADERS,
            timeout=15
        )

        if not response.ok:
            return False

        from protego import Protego
        parser = Protego.parse(response.text)

        user_agent = HEADERS["User-Agent"].split("/")[0]

        return parser.can_fetch(url, user_agent)

    except Exception as e:
        print(f"Robots check error: {str(e)[:150]}")
        return False

def get_sitemap_urls(base, limit=25):
    import gzip
    import xml.etree.ElementTree as ET

    urls = []

    if "wasalt.sa" in base:
        sitemaps = [
            "https://cdn.wasalt.sa/sitemap/product_sitemap_en_sa.xml.gz",
            "https://cdn.wasalt.sa/sitemap/static_sitemap_en_sa.xml.gz",
        ]
    else:
        sitemaps = [
            base.rstrip("/") + "/sitemap.xml",
            base.rstrip("/") + "/sitemap_index.xml",
        ]

    def extract_locations(content):
        # Handle genuinely gzip-compressed responses.
        if content.startswith(b"\x1f\x8b"):
            content = gzip.decompress(content)

        root = ET.fromstring(content)
        return [
            element.text.strip()
            for element in root.iter()
            if element.tag.split("}")[-1] == "loc" and element.text
        ]

    for sitemap_url in sitemaps:
        if len(urls) >= limit:
            break

        try:
            response = requests.get(
                sitemap_url,
                headers=HEADERS,
                timeout=20
            )

            if not response.ok:
                continue

            locations = extract_locations(response.content)

            for loc in locations:
                parsed = urllib.parse.urlparse(loc)

                if "wasalt.sa" in base:
                    if parsed.netloc not in ("wasalt.sa", "www.wasalt.sa"):
                        continue

                    if not (
                        parsed.path.startswith("/en/property/sale/")
                        or parsed.path.startswith("/en/property/rent/")
                        or parsed.path == "/en/"
                        or parsed.path.startswith("/en/s/")
                    ):
                        continue

                if loc not in urls:
                    urls.append(loc)

                if len(urls) >= limit:
                    break

        except Exception as e:
            print(f"Sitemap error ({sitemap_url}): {str(e)[:150]}")

    return urls[:limit]


def refresh_site(company, limit=25):
    cfg = SITES[company]
    base = cfg["base"]
    candidates = list(dict.fromkeys(cfg["seeds"] + get_sitemap_urls(base, limit)))
    saved, skipped, errors = 0, 0, []
    con = db()
    for url in candidates[:limit]:
        p = urllib.parse.urlparse(url)
        if p.netloc not in urllib.parse.urlparse(base).netloc: continue
        if not allowed_by_robots(url):
            skipped += 1
            continue
        try:
            r = requests.get(url, headers=HEADERS, timeout=18)
            if not r.ok or "text/html" not in r.headers.get("content-type", ""): continue
            title, content = clean_text(r.text)
            if len(content) < 180: continue
            con.execute("""INSERT INTO pages(company,url,title,content,fetched_at)
                VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                company=excluded.company,title=excluded.title,content=excluded.content,
                fetched_at=excluded.fetched_at""",
                (company,url,title,content,datetime.now(timezone.utc).isoformat()))
            saved += 1
            time.sleep(0.35)
        except Exception as e:
            errors.append(f"{url}: {str(e)[:100]}")
    con.commit()
    con.close()
    return {"company": company, "saved_or_updated": saved, "robots_skipped": skipped, "errors": errors[:5]}

def tokenize(s):
    return set(re.findall(r"[a-z0-9]{2,}", (s or "").lower()))

def retrieve(question, limit=6):
    q = tokenize(question)
    con = db()
    rows = con.execute("SELECT company,url,title,content,fetched_at FROM pages").fetchall()
    con.close()
    ranked = []
    for company,url,title,content,fetched_at in rows:
        words = tokenize((title or "") + " " + content)
        overlap = len(q & words)
        if overlap:
            # Simple transparent lexical ranking; no external embedding service required.
            score = overlap / max(1, len(q)) + min(overlap, 20) / 100
            ranked.append((score, company,url,title,content,fetched_at))
    ranked.sort(reverse=True, key=lambda x:x[0])
    return ranked[:limit]



def call_model(question, contexts, history):
    if not OPENROUTER_API_KEY:
        raise HTTPException(
            503,
            "Add OPENROUTER_API_KEY in your environment to enable AI responses."
        )

    context_text = "\n\n".join(
        f"[{c['company']} | {c['title']} | {c['url']}]\n{c['content'][:1800]}"
        for c in contexts[:6]
    )

    messages = [{
        "role": "system",
        "content": (
            "You are EstateIQ, a real-estate information assistant for DarGlobal and Wasalt. "
            "Answer only from the supplied source excerpts. "
            "Never invent prices, property details, availability or project information. "
            "Clearly distinguish the two companies. "
            "Give short, direct answers in readable Markdown. "
            "Include relevant source URLs. "
            "If information is missing, say so. "
            "Warn users to verify time-sensitive listing details.\n\n"
            "SOURCE EXCERPTS:\n" + context_text
        )
    }]

    for item in (history or [])[-6:]:
        if item.get("role") in ("user", "assistant"):
            messages.append({
                "role": item["role"],
                "content": str(item.get("content", ""))[:2000]
            })

    messages.append({"role": "user", "content": question})

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
        "X-Title": "EstateIQ Real Estate Assistant"
    }

    answer = None

    try:
        for attempt in range(2):
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 3000,
                    "reasoning": {"effort": "minimal"}
                },
                timeout=90
            )

            data = response.json()

            if not response.ok:
                if attempt == 0:
                    continue

                raise HTTPException(
                    response.status_code,
                    data.get("error", {}).get(
                        "message", "OpenRouter request failed"
                    )
                )

            choices = data.get("choices", [])
            answer = (
                choices[0].get("message", {}).get("content")
                if choices else None
            )

            if isinstance(answer, str) and answer.strip():
                return answer.strip()

            print(f"OpenRouter returned empty answer. Attempt: {attempt + 1}")

        fallback = (
            "I could not generate an AI response right now. "
            "Here are the collected source pages for your query:\n"
        )

        for c in contexts:
            fallback += f"\n- {c['title']}: {c['url']}"

        return fallback

    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach OpenRouter: {e}")


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    history: list[dict] = []

@app.on_event("startup")
def startup():
    db()

@app.get("/", response_class=HTMLResponse)
def home():
    with open(os.path.join(APP_DIR,"static","index.html"),encoding="utf-8") as f:
        return f.read()

@app.get("/api/health")
def health():
    con=db()
    counts={name:con.execute("SELECT COUNT(*) FROM pages WHERE company=?",(name,)).fetchone()[0] for name in SITES}
    con.close()
    return {"status":"ok","model":OPENROUTER_MODEL,"openrouter_configured":bool(OPENROUTER_API_KEY),"pages":counts}

@app.get("/api/sources")
def sources():
    con=db()
    rows=con.execute("SELECT company,url,title,fetched_at FROM pages ORDER BY company,title").fetchall()
    con.close()
    return [{"company":r[0],"url":r[1],"title":r[2],"fetched_at":r[3]} for r in rows]

@app.post("/api/refresh")
def refresh(company: Optional[str]=None):
    targets=[company] if company else list(SITES)
    if any(x not in SITES for x in targets): raise HTTPException(400,"Unknown company")
    return {"results":[refresh_site(x) for x in targets]}

@app.post("/api/chat")
def chat(req: ChatRequest):
    matches=retrieve(req.message)
    if not matches:
        return {"answer":"I don't have relevant collected source material for that question yet. Try refreshing the sources, or ask about DarGlobal's developments or Wasalt's services.","sources":[]}
    contexts=[]
    for score,company,url,title,content,fetched_at in matches:
        contexts.append({"company":company,"url":url,"title":title,"content":content})
    answer=call_model(req.message,contexts,req.history)
    return {"answer":answer,"sources":[{"company":c["company"],"title":c["title"],"url":c["url"]} for c in contexts]}
