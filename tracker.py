#!/usr/bin/env python3
"""
Tracker macro automatisé.

Récupère les dernières actualités financières via des flux RSS (Google News,
ForexFactory, Yahoo Finance), les fait interpréter par l'API Anthropic (Claude)
selon un jeu de règles macroéconomiques, récupère les prix en direct et le
calendrier économique, puis génère un `index.html` autonome (thème sombre,
responsive) affichant le biais courant de 4 actifs : XAUUSD, NAS100, SP500,
BTCUSD.

Si aucune clé API Anthropic n'est disponible, ou si l'appel échoue, le script
retombe sur une analyse par mots-clés locale afin que le tableau de bord soit
toujours généré (aucune interruption du pipeline d'automatisation).

Limite assumée : ce script tourne toutes les 5 minutes au mieux et se base sur
de l'actualité écrite, pas sur le carnet d'ordres ou des données tick-by-tick.
Il ne fournit donc pas — et ne prétend pas fournir — de signal d'entrée fiable
à la minute pour du scalping. Il donne un biais directionnel de fond et un
calendrier des échéances à risque, ce qui reste utile pour éviter de trader
à contre-tendance ou pile pendant une annonce à fort impact.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import feedparser
import requests

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_HEADLINES = 40
REQUEST_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (compatible; MacroTrackerBot/1.0; "
    "+https://github.com/) tracker.py"
)

ASSETS = ["XAUUSD", "NAS100", "SP500", "BTCUSD"]

ASSET_LABELS = {
    "XAUUSD": "Or spot (XAU/USD)",
    "NAS100": "Nasdaq 100",
    "SP500": "S&P 500",
    "BTCUSD": "Bitcoin (BTC/USD)",
}

FEEDS = [
    ("Google News - Fed & Inflation",
     "https://news.google.com/rss/search?q=federal+reserve+OR+inflation+OR+interest+rates+OR+CPI&hl=en-US&gl=US&ceid=US:en"),
    ("Google News - Marchés & Actions",
     "https://news.google.com/rss/search?q=stock+market+OR+nasdaq+OR+s%26p+500+OR+earnings&hl=en-US&gl=US&ceid=US:en"),
    ("Google News - Crypto & Bitcoin",
     "https://news.google.com/rss/search?q=bitcoin+OR+crypto+ETF+OR+crypto+regulation&hl=en-US&gl=US&ceid=US:en"),
    ("Google News - Géopolitique",
     "https://news.google.com/rss/search?q=geopolitical+tensions+OR+war+OR+conflict+economy+oil&hl=en-US&gl=US&ceid=US:en"),
    ("Yahoo Finance - Top Stories",
     "https://finance.yahoo.com/news/rssindex"),
]

FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

# Symboles Yahoo Finance (gratuit, sans clé API). Cotations différées, à but indicatif.
PRICE_SYMBOLS = {
    "XAUUSD": "XAUUSD=X",
    "NAS100": "^NDX",
    "SP500": "^GSPC",
    "BTCUSD": "BTC-USD",
}

# Repli spécifique Bitcoin (source très fiable, gratuite, sans clé).
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
)

BIAS_STYLE = {
    "HAUSSE": {"emoji": "🟢", "label": "HAUSSE", "css": "up"},
    "BAISSE": {"emoji": "🔴", "label": "BAISSE", "css": "down"},
    "NEUTRE": {"emoji": "⚪", "label": "NEUTRE", "css": "flat"},
}

IMPACT_STYLE = {
    "high": {"label": "Fort impact", "css": "impact-high"},
    "medium": {"label": "Impact moyen", "css": "impact-medium"},
}

DEFAULT_REASON = "Pas assez de signal clair dans les actualités récentes pour trancher."
DEFAULT_SUMMARY = "Pas assez d'actualités récentes pour dégager une synthèse claire du climat de marché."

# Classification des événements du calendrier par mots-clés : actifs concernés
# + exemple concret d'impact selon que le chiffre publié soit au-dessus ou en
# dessous des attentes. Appliqué localement (pas d'appel API) pour rester
# rapide et gratuit à chaque exécution toutes les 5 minutes.
CALENDAR_IMPACT_RULES = [
    (r"cpi|inflation rate|pce price index|core pce|ppi\b", ASSETS,
     "Chiffre au-dessus des attentes → craintes d'inflation ravivées, Fed perçue plus restrictive → pression baissière probable sur les 4 actifs.",
     "Chiffre en dessous des attentes → anticipations de baisse de taux renforcées → pression haussière probable sur les 4 actifs."),
    (r"fomc|interest rate decision|fed funds rate|federal funds rate|rate statement|fed chair|powell|monetary policy statement",
     ASSETS,
     "Ton plus restrictif (\"hawkish\") que prévu → pression baissière probable sur les 4 actifs.",
     "Ton plus accommodant (\"dovish\") que prévu → pression haussière probable sur les 4 actifs."),
    (r"non-?farm|nonfarm payrolls|employment change|unemployment rate|jobless claims|average hourly earnings",
     ASSETS,
     "Emploi plus solide que prévu → peut raviver les craintes que la Fed reste restrictive plus longtemps (logique \"bonne nouvelle économique = mauvaise nouvelle pour les taux\") → pression baissière possible sur les 4 actifs.",
     "Emploi plus faible que prévu → paris renforcés sur une Fed plus accommodante → pression haussière possible sur les 4 actifs."),
    (r"\bgdp\b", ["NAS100", "SP500", "BTCUSD"],
     "Croissance plus forte que prévu → généralement favorable aux actions et au risque (mais peut aussi raviver les craintes d'inflation).",
     "Croissance plus faible que prévu → augmente le risque de ralentissement, pèse généralement sur les actions et les actifs risqués."),
    (r"pmi|ism manufacturing|ism services|retail sales|consumer confidence|durable goods",
     ["NAS100", "SP500", "BTCUSD"],
     "Chiffre plus fort que prévu → plutôt favorable à l'appétit pour le risque (actions, crypto).",
     "Chiffre plus faible que prévu → pèse généralement sur l'appétit pour le risque (actions, crypto)."),
]


def classify_calendar_event(title: str) -> dict | None:
    lowered = title.lower()
    for pattern, assets, higher, lower in CALENDAR_IMPACT_RULES:
        if re.search(pattern, lowered):
            return {"assets": assets, "higher": higher, "lower": lower}
    return None


def fetch_headlines() -> list[dict]:
    """Récupère et fusionne les titres des flux RSS configurés."""
    headlines: list[dict] = []
    seen_titles: set[str] = set()

    for source_name, url in FEEDS:
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:  # noqa: BLE001 - un flux qui échoue ne doit jamais bloquer le pipeline
            print(f"[warn] flux ignoré ({source_name}): {exc}", file=sys.stderr)
            continue

        for entry in parsed.entries[:15]:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            headlines.append({
                "title": title,
                "source": source_name,
                "link": getattr(entry, "link", ""),
                "published": getattr(entry, "published", ""),
            })

    return headlines[:MAX_HEADLINES]


def fetch_prices() -> dict:
    """Récupère un prix indicatif (différé) pour chaque actif, sans clé API."""
    prices: dict[str, dict | None] = {}

    for asset, symbol in PRICE_SYMBOLS.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            meta = resp.json()["chart"]["result"][0]["meta"]

            price_raw = meta.get("regularMarketPrice")
            if price_raw is None:
                raise ValueError("pas de cotation disponible")
            price = float(price_raw)

            prev_close_raw = meta.get("previousClose") or meta.get("chartPreviousClose")
            change_pct = None
            if prev_close_raw:
                prev_close = float(prev_close_raw)
                if prev_close:
                    change_pct = (price - prev_close) / prev_close * 100

            prices[asset] = {"price": price, "change_pct": change_pct, "date": "", "time": ""}
        except Exception as exc:  # noqa: BLE001 - une source de prix en panne ne doit jamais bloquer le pipeline
            print(f"[warn] prix indisponible pour {asset} via Yahoo Finance: {exc}", file=sys.stderr)
            prices[asset] = None

    if prices.get("BTCUSD") is None:
        try:
            resp = requests.get(COINGECKO_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()["bitcoin"]
            prices["BTCUSD"] = {
                "price": float(data["usd"]),
                "change_pct": float(data.get("usd_24h_change")) if data.get("usd_24h_change") is not None else None,
                "date": "",
                "time": "",
            }
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] prix BTCUSD indisponible via CoinGecko (repli): {exc}", file=sys.stderr)

    return prices


def fetch_calendar() -> list[dict]:
    """Récupère les prochaines échéances macro à fort impact (ForexFactory)."""
    events: list[dict] = []
    try:
        resp = requests.get(FF_CALENDAR_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for ev in root.findall(".//event"):
            impact = (ev.findtext("impact") or "").strip().lower()
            if impact not in ("high", "medium"):
                continue
            events.append({
                "title": (ev.findtext("title") or "").strip(),
                "country": (ev.findtext("country") or "").strip(),
                "date": (ev.findtext("date") or "").strip(),
                "time": (ev.findtext("time") or "").strip(),
                "impact": impact,
                "forecast": (ev.findtext("forecast") or "").strip(),
                "previous": (ev.findtext("previous") or "").strip(),
            })
    except Exception as exc:  # noqa: BLE001 - le calendrier ne doit jamais bloquer le pipeline
        print(f"[warn] calendrier économique indisponible: {exc}", file=sys.stderr)

    return events[:25]


def build_prompt(headlines: list[dict]) -> str:
    headlines_block = "\n".join(f"- {h['title']} (source: {h['source']})" for h in headlines) or "(aucune actualité récupérée)"

    return f"""Tu es un analyste macroéconomique. Voici les dernières actualités financières collectées automatiquement :

{headlines_block}

Applique STRICTEMENT ces règles macro pour déterminer le biais de chaque actif :
- Inflation en hausse / Fed "hawkish" => BAISSE pour XAUUSD, NAS100, SP500, BTCUSD.
- Inflation en baisse / Fed "dovish" => HAUSSE pour XAUUSD, NAS100, SP500, BTCUSD.
- Tensions géopolitiques => HAUSSE pour XAUUSD, BAISSE pour NAS100, SP500, BTCUSD.
- Résultats Tech positifs => HAUSSE pour NAS100 et SP500.
- Flux ETF positifs / régulation crypto favorable => HAUSSE pour BTCUSD.
- Si les signaux sont contradictoires ou insuffisants pour un actif, réponds NEUTRE pour cet actif.

Réponds UNIQUEMENT avec un objet JSON strict (pas de texte autour, pas de balises markdown), de la forme exacte :
{{
  "summary": "3 à 5 phrases en français résumant le climat macro actuel à partir des actualités ci-dessus (ce qui domine le narratif en ce moment, le sentiment général des marchés tel qu'il ressort de la presse)",
  "XAUUSD": {{"bias": "HAUSSE|BAISSE|NEUTRE", "reason": "une phrase courte en français expliquant pourquoi"}},
  "NAS100": {{"bias": "HAUSSE|BAISSE|NEUTRE", "reason": "..."}},
  "SP500": {{"bias": "HAUSSE|BAISSE|NEUTRE", "reason": "..."}},
  "BTCUSD": {{"bias": "HAUSSE|BAISSE|NEUTRE", "reason": "..."}}
}}"""


def call_claude(prompt: str) -> dict | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[info] ANTHROPIC_API_KEY absente, bascule sur l'analyse par mots-clés.", file=sys.stderr)
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1536,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return parse_json_response(text)
    except Exception as exc:  # noqa: BLE001 - toute erreur API retombe sur le fallback local
        print(f"[warn] appel Anthropic échoué, bascule sur le fallback: {exc}", file=sys.stderr)
        return None


def parse_json_response(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or not all(asset in data for asset in ASSETS):
        return None

    return data


KEYWORD_RULES = [
    # (regex, {asset: delta})
    (r"\b(hawkish|rate hike|raises rates|higher for longer|inflation (rises|jumps|accelerates)|hot cpi)\b",
     {"XAUUSD": -1, "NAS100": -1, "SP500": -1, "BTCUSD": -1}),
    (r"\b(dovish|rate cut|cuts rates|inflation (falls|cools|eases)|cooling cpi|disinflation)\b",
     {"XAUUSD": 1, "NAS100": 1, "SP500": 1, "BTCUSD": 1}),
    (r"\b(war|conflict|geopolitical|tensions|missile|strike|invasion|sanctions)\b",
     {"XAUUSD": 1, "NAS100": -1, "SP500": -1, "BTCUSD": -1}),
    (r"\b(earnings beat|strong earnings|record profit|tech rally|beats estimates)\b",
     {"NAS100": 1, "SP500": 1}),
    (r"\b(earnings miss|weak earnings|profit warning|guidance cut)\b",
     {"NAS100": -1, "SP500": -1}),
    (r"\b(etf inflow|spot etf approv|bitcoin etf|crypto regulation clarity|favorable regulation)\b",
     {"BTCUSD": 1}),
    (r"\b(crypto ban|regulatory crackdown|etf outflow|sec sues|exchange collapse)\b",
     {"BTCUSD": -1}),
]


def fallback_rule_based(headlines: list[dict]) -> dict:
    scores = {asset: 0 for asset in ASSETS}
    matched_terms: dict[str, list[str]] = {asset: [] for asset in ASSETS}

    corpus = [(h["title"], h["title"].lower()) for h in headlines]

    for pattern, deltas in KEYWORD_RULES:
        regex = re.compile(pattern, re.IGNORECASE)
        for title, lowered in corpus:
            if regex.search(lowered):
                for asset, delta in deltas.items():
                    scores[asset] += delta
                    matched_terms[asset].append(title)

    result: dict = {}
    for asset in ASSETS:
        score = scores[asset]
        if score > 0:
            bias = "HAUSSE"
            reason = "Les actualités récentes penchent vers des catalyseurs favorables (Fed accommodante, flux positifs ou tensions géopolitiques selon l'actif)."
        elif score < 0:
            bias = "BAISSE"
            reason = "Les actualités récentes penchent vers des catalyseurs défavorables (Fed restrictive, résultats décevants ou aversion au risque)."
        else:
            bias = "NEUTRE"
            reason = DEFAULT_REASON
        result[asset] = {"bias": bias, "reason": reason}

    if headlines:
        result["summary"] = (
            f"Analyse par mots-clés sur {len(headlines)} titres récents (l'API Claude n'a pas pu être utilisée pour "
            "cette exécution). Cette synthèse est plus grossière qu'une analyse Claude : elle compte les occurrences "
            "de termes macro connus sans en comprendre le contexte complet."
        )
    else:
        result["summary"] = DEFAULT_SUMMARY

    return result


def normalize_analysis(data: dict) -> tuple[dict, str]:
    normalized = {}
    for asset in ASSETS:
        entry = data.get(asset, {}) if isinstance(data, dict) else {}
        bias = str(entry.get("bias", "NEUTRE")).strip().upper()
        if bias not in BIAS_STYLE:
            bias = "NEUTRE"
        reason = str(entry.get("reason", "")).strip() or DEFAULT_REASON
        normalized[asset] = {"bias": bias, "reason": reason}

    summary = str(data.get("summary", "")).strip() if isinstance(data, dict) else ""
    summary = summary or DEFAULT_SUMMARY

    return normalized, summary


def format_price(entry: dict | None) -> str:
    if not entry or entry.get("price") is None:
        return "indisponible"
    price = entry["price"]
    if price >= 1000:
        return f"${price:,.2f}".replace(",", " ")
    return f"${price:,.4f}"


def format_change(entry: dict | None) -> tuple[str, str]:
    if not entry or entry.get("change_pct") is None:
        return "", "flat"
    pct = entry["change_pct"]
    css = "up" if pct > 0 else "down" if pct < 0 else "flat"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.2f}%", css


def generate_html(
    analysis: dict,
    summary: str,
    headlines: list[dict],
    prices: dict,
    calendar: list[dict],
    generated_at: datetime,
) -> str:
    timestamp_str = generated_at.strftime("%d/%m/%Y à %H:%M:%S UTC")

    price_cards = []
    for asset in ASSETS:
        entry = prices.get(asset)
        change_str, change_css = format_change(entry)
        price_cards.append(f"""
        <div class="price-card">
          <span class="ticker">{asset}</span>
          <span class="price-value">{format_price(entry)}</span>
          {f'<span class="price-change {change_css}">{change_str}</span>' if change_str else '<span class="price-change flat">—</span>'}
        </div>""")

    cards_html = []
    for asset in ASSETS:
        entry = analysis[asset]
        style = BIAS_STYLE[entry["bias"]]
        cards_html.append(f"""
        <article class="card {style['css']}">
          <div class="card-header">
            <span class="ticker">{asset}</span>
            <span class="bias-badge {style['css']}">{style['emoji']} {style['label']}</span>
          </div>
          <h2>{html.escape(ASSET_LABELS[asset])}</h2>
          <p class="reason">{html.escape(entry['reason'])}</p>
        </article>""")

    def render_event(ev: dict) -> str:
        impact_info = classify_calendar_event(ev["title"])
        detail_html = ""
        if impact_info:
            assets_str = ", ".join(impact_info["assets"])
            detail_html = f'''
          <div class="event-detail">
            <p class="event-assets">Actifs concernés : <strong>{html.escape(assets_str)}</strong></p>
            <p class="event-scenario"><span class="scenario-up">Si au-dessus des attentes :</span> {html.escape(impact_info["higher"])}</p>
            <p class="event-scenario"><span class="scenario-down">Si en dessous des attentes :</span> {html.escape(impact_info["lower"])}</p>
          </div>'''
        return f'''<li class="event">
          <div class="event-row">
            <span class="event-when">{html.escape(ev["date"])} {html.escape(ev["time"])}</span>
            <span class="event-badge {IMPACT_STYLE[ev["impact"]]["css"]}">{IMPACT_STYLE[ev["impact"]]["label"]}</span>
            <span class="event-country">{html.escape(ev["country"])}</span>
            <span class="event-title">{html.escape(ev["title"])}</span>
          </div>{detail_html}
        </li>'''

    calendar_html = "\n".join(render_event(ev) for ev in calendar) or (
        '<li class="event-empty">Calendrier économique indisponible pour cette exécution.</li>'
    )

    sources_html = "\n".join(
        f'<li><a href="{html.escape(h["link"])}" target="_blank" rel="noopener">{html.escape(h["title"])}</a> '
        f'<span class="src">— {html.escape(h["source"])}</span></li>'
        for h in headlines[:15]
    ) or "<li>Aucune actualité disponible pour cette exécution.</li>"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Tracker Macro — XAUUSD · NAS100 · SP500 · BTCUSD</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0b0e14;
    --panel: #131826;
    --panel-border: #232a3d;
    --text: #e6e9f2;
    --text-dim: #9aa3b8;
    --up: #22c55e;
    --down: #ef4444;
    --flat: #94a3b8;
    --high: #ef4444;
    --medium: #f59e0b;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 24px 16px 48px;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 24px; }}
  header h1 {{ font-size: 1.6rem; margin: 0 0 8px; }}
  header p {{ color: var(--text-dim); margin: 4px 0; font-size: 0.9rem; }}
  h3 {{ margin: 0 0 12px; font-size: 1.05rem; }}
  .disclaimer {{
    padding: 12px 16px; margin-bottom: 20px;
    background: #1a1400; border: 1px solid #4a3b00; border-radius: 10px;
    color: #f5d76e; font-size: 0.82rem; line-height: 1.4;
  }}
  .prices-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .price-card {{
    background: var(--panel); border: 1px solid var(--panel-border); border-radius: 12px;
    padding: 14px 16px; display: flex; flex-direction: column; gap: 4px;
  }}
  .price-value {{ font-size: 1.3rem; font-weight: 700; }}
  .price-change {{ font-size: 0.85rem; font-weight: 600; }}
  .price-change.up {{ color: var(--up); }}
  .price-change.down {{ color: var(--down); }}
  .price-change.flat {{ color: var(--text-dim); }}
  section.panel {{
    background: var(--panel); border: 1px solid var(--panel-border); border-radius: 14px;
    padding: 18px 20px; margin-bottom: 20px;
  }}
  .summary-text {{ color: var(--text-dim); font-size: 0.92rem; line-height: 1.55; margin: 0; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
  }}
  .card {{
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 14px;
    padding: 18px;
    border-top: 3px solid var(--flat);
  }}
  .card.up {{ border-top-color: var(--up); }}
  .card.down {{ border-top-color: var(--down); }}
  .card.flat {{ border-top-color: var(--flat); }}
  .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .ticker {{ font-weight: 700; letter-spacing: 0.5px; color: var(--text-dim); font-size: 0.85rem; }}
  .bias-badge {{ font-weight: 700; font-size: 0.85rem; padding: 4px 10px; border-radius: 999px; background: rgba(255,255,255,0.06); }}
  .bias-badge.up {{ color: var(--up); }}
  .bias-badge.down {{ color: var(--down); }}
  .bias-badge.flat {{ color: var(--flat); }}
  .card h2 {{ margin: 0 0 8px; font-size: 1.25rem; }}
  .reason {{ color: var(--text-dim); font-size: 0.92rem; line-height: 1.4; margin: 0; }}
  ul.events {{ list-style: none; margin: 0; padding: 0; }}
  li.event {{
    padding: 10px 0; border-bottom: 1px solid var(--panel-border); font-size: 0.88rem;
  }}
  li.event:last-child {{ border-bottom: none; }}
  .event-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
  .event-when {{ color: var(--text-dim); min-width: 130px; font-variant-numeric: tabular-nums; }}
  .event-badge {{ font-size: 0.72rem; font-weight: 700; padding: 2px 8px; border-radius: 999px; }}
  .event-badge.impact-high {{ background: rgba(239,68,68,0.15); color: var(--high); }}
  .event-badge.impact-medium {{ background: rgba(245,158,11,0.15); color: var(--medium); }}
  .event-country {{ color: var(--text-dim); font-weight: 600; min-width: 32px; }}
  .event-title {{ flex: 1; min-width: 140px; }}
  .event-empty {{ color: var(--text-dim); font-size: 0.88rem; }}
  .event-detail {{
    margin: 8px 0 0; padding: 10px 12px; background: rgba(255,255,255,0.03);
    border-radius: 8px; border: 1px solid var(--panel-border);
  }}
  .event-detail p {{ margin: 0 0 6px; font-size: 0.84rem; line-height: 1.4; color: var(--text-dim); }}
  .event-detail p:last-child {{ margin-bottom: 0; }}
  .event-assets strong {{ color: var(--text); }}
  .scenario-up {{ color: var(--up); font-weight: 600; }}
  .scenario-down {{ color: var(--down); font-weight: 600; }}
  section.panel ul {{ margin: 0; padding-left: 18px; }}
  section.panel li {{ margin-bottom: 6px; font-size: 0.88rem; }}
  section.panel a {{ color: var(--text); text-decoration: none; }}
  section.panel a:hover {{ text-decoration: underline; }}
  .src {{ color: var(--text-dim); }}
  .note {{ color: var(--text-dim); font-size: 0.8rem; line-height: 1.4; margin-top: 10px; }}
  footer {{ text-align: center; color: var(--text-dim); font-size: 0.8rem; margin-top: 8px; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>📊 Tracker Macro Automatisé</h1>
      <p>Biais généré automatiquement à partir de l'actualité économique — mis à jour toutes les 5 minutes.</p>
      <p>Dernière mise à jour : <strong>{timestamp_str}</strong> · {len(headlines)} actualités analysées</p>
    </header>

    <div class="disclaimer">
      ⚠️ Ceci n'est pas un conseil en investissement. Les prix sont différés (source gratuite, pas de flux temps réel),
      et le biais affiché résulte d'une analyse automatisée d'actualités publiques — il peut être incomplet, en retard
      ou erroné. <strong>Ce site ne fournit pas de signal d'entrée à la minute</strong> : il donne un biais directionnel
      de fond et signale les échéances macro à risque, pas des points d'entrée de scalping. Faites toujours vos propres
      recherches et vérifiez le prix en temps réel chez votre broker avant toute décision.
    </div>

    <div class="prices-row">
      {''.join(price_cards)}
    </div>

    <section class="panel">
      <h3>🧭 Résumé du climat de marché</h3>
      <p class="summary-text">{html.escape(summary)}</p>
    </section>

    <div class="grid">
      {''.join(cards_html)}
    </div>

    <section class="panel">
      <h3>🗓️ Calendrier économique — prochaines échéances à risque</h3>
      <ul class="events">
        {calendar_html}
      </ul>
      <p class="note">Heures au format ForexFactory (heure de New York, ET). Seuls les événements à impact moyen/fort sont listés — ce sont ceux qui peuvent provoquer des mèches violentes à la minute où ils tombent.</p>
    </section>

    <section class="panel">
      <h3>🗞️ Actualités prises en compte</h3>
      <ul>
        {sources_html}
      </ul>
    </section>

    <footer>
      Sources : Google News, Yahoo Finance, ForexFactory, CoinGecko · Analyse : Claude (Anthropic) avec repli automatique par mots-clés.
    </footer>
  </div>
</body>
</html>
"""


def main() -> None:
    headlines = fetch_headlines()
    print(f"[info] {len(headlines)} actualités récupérées.", file=sys.stderr)

    prices = fetch_prices()
    calendar = fetch_calendar()

    analysis_raw = None
    if headlines:
        prompt = build_prompt(headlines)
        analysis_raw = call_claude(prompt)

    if analysis_raw is None:
        analysis_raw = fallback_rule_based(headlines)
        print("[info] Analyse générée via le moteur de repli par mots-clés.", file=sys.stderr)
    else:
        print("[info] Analyse générée via l'API Anthropic (Claude).", file=sys.stderr)

    analysis, summary = normalize_analysis(analysis_raw)

    generated_at = datetime.now(timezone.utc)
    output = generate_html(analysis, summary, headlines, prices, calendar, generated_at)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"[ok] index.html généré ({out_path}).", file=sys.stderr)


if __name__ == "__main__":
    main()
