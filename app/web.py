"""Простая веб-форма и JSON API.

    uvicorn app.web:app --reload        → http://127.0.0.1:8000
    POST /api/card   тело — JSON товара, ответ — карточка (JSON + Markdown)
"""

import html
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .generator import GenerationError
from .input_check import InputError
from .llm import LLMError
from .render import to_markdown
from .service import card_from_json, make_client

app = FastAPI(title="AI-помощник для карточек КИП", version="1.0.0")
app.state.client_factory = make_client

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "examples" / "input" / "tm-510r-m2.json"

STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--muted:#5b6475;--line:#d9dde5;--accent:#1f5fbf;--warn:#9a5b00;--warnbg:#fff6e5;--err:#a8232a;--errbg:#fdecec}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1d2128;--ink:#e6e9ef;--muted:#9aa3b2;--line:#323844;--accent:#6ea2ff;--warn:#f0b35a;--warnbg:#2b2416;--err:#ff8a8a;--errbg:#2d1a1b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:24px 0 8px}
p.lead{color:var(--muted);margin:0 0 20px}.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
label{display:block;font-weight:600;margin:12px 0 4px}input,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:110px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}.hint{color:var(--muted);font-size:13px;margin-top:4px}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer;margin-top:14px}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13px;max-height:520px;overflow:auto}
.warn{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:10px 12px;margin:8px 0}.err{background:var(--errbg);color:var(--err);border-radius:8px;padding:10px 12px;margin:8px 0}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.or{color:var(--muted);text-align:center;margin:8px 0}
.head{justify-content:space-between}.head h2{margin:0}button.small{margin-top:0;padding:6px 12px;font-size:13px}
table{border-collapse:collapse;width:100%}td{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top}td:first-child{color:var(--muted);width:45%}
.box p{margin:8px 0}.box ul{margin:8px 0;padding-left:20px}summary{cursor:pointer;font-weight:600}details pre{margin-top:8px}
@media (max-width:600px){td:first-child{width:50%}}
"""

COPY_JS = """
function copyText(id, btn){navigator.clipboard.writeText(document.getElementById(id).innerText).then(()=>{const t=btn.innerText;btn.innerText='Скопировано';setTimeout(()=>btn.innerText=t,1500)})}
function fillExample(){document.getElementById('raw_json').value=EXAMPLE}
"""


def _page(body: str) -> HTMLResponse:
    example = EXAMPLE_PATH.read_text(encoding="utf-8") if EXAMPLE_PATH.exists() else "{}"
    return HTMLResponse(
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Карточки КИП</title><style>{STYLE}</style></head><body><main>{body}</main>"
        f"<script>const EXAMPLE={json.dumps(example)};{COPY_JS}</script></body></html>"
    )


FORM = """
<h1>AI-помощник для карточек КИП</h1>
<p class="lead">Вставьте JSON товара или заполните поля. Все технические значения в карточке берутся только из этих данных.</p>
<form method="post" action="/card" class="box">
  <label for="raw_json">JSON товара</label>
  <textarea id="raw_json" name="raw_json" placeholder='{{"name": "...", "manufacturer": "...", "specs": {{...}}, "description": "..."}}'>{raw_json}</textarea>
  <div class="row"><button type="button" class="secondary" onclick="fillExample()">Подставить пример</button></div>
  <p class="or">— или поля формы (используются, если JSON пустой) —</p>
  <label for="name">Название товара</label><input id="name" name="name" value="{name}">
  <label for="manufacturer">Производитель</label><input id="manufacturer" name="manufacturer" value="{manufacturer}">
  <label for="specs">Характеристики</label>
  <textarea id="specs" name="specs" placeholder="диапазон измерений: 0–1,6 МПа&#10;класс точности: 1,5">{specs}</textarea>
  <div class="hint">По одной на строку: «параметр: значение».</div>
  <label for="description">Описание или фрагмент паспорта</label>
  <textarea id="description" name="description">{description}</textarea>
  <button type="submit">Сформировать карточку</button>
</form>
"""


def _form(values: dict | None = None) -> str:
    values = values or {}
    return FORM.format(**{k: html.escape(values.get(k, "")) for k in ("raw_json", "name", "manufacturer", "specs", "description")})


def _form_to_product(values: dict) -> str | dict:
    if values.get("raw_json", "").strip():
        return values["raw_json"]
    specs = {}
    for line in values.get("specs", "").splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        specs[key.strip()] = value.strip()
    return {
        "name": values.get("name", ""),
        "manufacturer": values.get("manufacturer", ""),
        "specs": specs,
        "description": values.get("description", ""),
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return _page(_form())


@app.post("/card", response_class=HTMLResponse)
async def card_page(request: Request):
    form = await request.form()
    values = {k: str(form.get(k, "")) for k in ("raw_json", "name", "manufacturer", "specs", "description")}
    try:
        card = card_from_json(_form_to_product(values), app.state.client_factory())
    except InputError as exc:
        return _page(f"<div class='err'>Ошибка входных данных: {html.escape(str(exc))}</div>" + _form(values))
    except (LLMError, GenerationError) as exc:
        return _page(f"<div class='err'>Ошибка модели: {html.escape(str(exc))}</div>" + _form(values))

    return _page(_card_html(card))


def _section(title: str, block_id: str, inner: str) -> str:
    return (
        "<div class='box'><div class='row head'>"
        f"<h2>{title}</h2>"
        f"<button type='button' class='secondary small' onclick=\"copyText('{block_id}',this)\">Копировать</button></div>"
        f"<div id='{block_id}'>{inner}</div></div>"
    )


def _card_html(card) -> str:
    esc = html.escape
    warnings = "".join(f"<div class='warn'>{esc(w)}</div>" for w in card.warnings)
    paragraphs = "".join(f"<p>{esc(p)}</p>" for p in card.seo_description.split("\n\n"))
    rows = "".join(f"<tr><td>{esc(r['name'])}</td><td>{esc(r['value'])}</td></tr>" for r in card.characteristics)
    queries = "".join(f"<li>{esc(q)}</li>" for q in card.search_queries)
    commercial = card.commercial
    manager = f"<p><b>Кому подходит:</b> {esc(commercial.get('for_whom', ''))}</p>"
    if commercial.get("tasks"):
        manager += "<p><b>Какие задачи решает:</b></p><ul>" + "".join(f"<li>{esc(t)}</li>" for t in commercial["tasks"]) + "</ul>"
    if commercial.get("selling_points"):
        manager += "<p><b>Аргументы для клиента:</b></p><ul>" + "".join(f"<li>{esc(t)}</li>" for t in commercial["selling_points"]) + "</ul>"
    clarify = ""
    if card.needs_clarification:
        clarify = "<div class='box'><h2>Требует уточнения</h2><ul>" + "".join(
            f"<li>{esc(t)}</li>" for t in card.needs_clarification) + "</ul></div>"

    markdown = to_markdown(card)
    data = json.dumps(card.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"<h1>{esc(card.name)}</h1>"
        f"<p class='lead'>Проверка фактов: {esc(card.meta.get('fact_check', ''))} · "
        f"модель: {esc(str(card.meta.get('model', '')))} · попыток: {card.meta.get('attempts', 1)}</p>{warnings}"
        + _section(f"SEO-заголовок · {len(card.seo_title)}/70", "title", f"<p>{esc(card.seo_title)}</p>")
        + _section(f"SEO-описание · {len(card.seo_description)} символов", "descr", paragraphs)
        + _section("Ключевые характеристики", "specs", f"<table>{rows}</table>")
        + _section("Поисковые запросы", "queries", f"<ul>{queries}</ul>")
        + _section("Для менеджера", "manager", manager)
        + clarify
        + "<details class='box'><summary>Вся карточка в Markdown</summary>"
        "<button type='button' class='secondary small' onclick=\"copyText('md',this)\">Копировать</button>"
        f"<pre id='md'>{esc(markdown)}</pre></details>"
        "<details class='box'><summary>JSON</summary>"
        "<button type='button' class='secondary small' onclick=\"copyText('json',this)\">Копировать</button>"
        f"<pre id='json'>{esc(data)}</pre></details>"
        "<p><a href='/'>← Новый товар</a></p>"
    )


@app.post("/api/card")
async def api_card(request: Request):
    raw = await request.body()
    try:
        card = card_from_json(raw, app.state.client_factory())
    except InputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except (LLMError, GenerationError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return {"card": card.to_dict(), "markdown": to_markdown(card)}


@app.get("/health")
def health():
    return {"status": "ok"}
