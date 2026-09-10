#!/usr/bin/env python3
"""
Tracker macro automatisé.

Récupère les dernières actualités financières via des flux RSS (Google News,
ForexFactory, Yahoo Finance), les fait interpréter par l'API Anthropic (Claude)
selon un jeu de règles macroéconomiques, puis génère un `index.html` autonome
(thème sombre, responsive) affichant le biais courant de 4 actifs :
XAUUSD, NAS100, SP500, BTCUSD.

Si aucune clé API Anthropic n'est disponible, ou si l'appel échoue, le script
retombe sur une analyse par mots-clés locale afin que le tableau de bord soit
toujours généré (aucune interruption du pipeline d'automatisation).
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
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
    ("ForexFactory - Calendrier économique",
     "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"),
]

BIAS_STYLE = {
    "HAUSSE": {"emoji": "🟢", "label": "HAUSSE", "css": "up"},
    "BAISSE": {"emoji": "🔴", "label": "BAISSE", "css": "down"},
    "NEUTRE": {"emoji": "⚪", "label": "NEUTRE", "css": "flat"},
}

DEFAULT_REASON = "Pas assez de signal clair dans les actualités récentes pour trancher."


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
            max_tokens=1024,
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

    result = {}
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

    return result


def normalize_analysis(data: dict) -> dict:
    normalized = {}
    for asset in ASSETS:
        entry = data.get(asset, {}) if isinstance(data, dict) else {}
        bias = str(entry.get("bias", "NEUTRE")).strip().upper()
        if bias not in BIAS_STYLE:
            bias = "NEUTRE"
        reason = str(entry.get("reason", "")).strip() or DEFAULT_REASON
        normalized[asset] = {"bias": bias, "reason": reason}
    return normalized


def generate_html(analysis: dict, headlines: list[dict], generated_at: datetime) -> str:
    timestamp_str = generated_at.strftime("%d/%m/%Y à %H:%M UTC")

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
  header {{ text-align: center; margin-bottom: 28px; }}
  header h1 {{ font-size: 1.6rem; margin: 0 0 8px; }}
  header p {{ color: var(--text-dim); margin: 4px 0; font-size: 0.9rem; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
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
  section.sources {{
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 14px;
    padding: 18px 20px;
  }}
  section.sources h3 {{ margin-top: 0; font-size: 1.05rem; }}
  section.sources ul {{ margin: 0; padding-left: 18px; }}
  section.sources li {{ margin-bottom: 6px; font-size: 0.88rem; }}
  section.sources a {{ color: var(--text); text-decoration: none; }}
  section.sources a:hover {{ text-decoration: underline; }}
  .src {{ color: var(--text-dim); }}
  footer {{ text-align: center; color: var(--text-dim); font-size: 0.8rem; margin-top: 28px; }}
  .disclaimer {{
    max-width: 1000px; margin: 0 auto 24px; padding: 12px 16px;
    background: #1a1400; border: 1px solid #4a3b00; border-radius: 10px;
    color: #f5d76e; font-size: 0.82rem; line-height: 1.4;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>📊 Tracker Macro Automatisé</h1>
      <p>Biais généré automatiquement à partir de l'actualité économique — mis à jour toutes les heures.</p>
      <p>Dernière mise à jour : <strong>{timestamp_str}</strong> · {len(headlines)} actualités analysées</p>
    </header>

    <div class="disclaimer">
      ⚠️ Ceci n'est pas un conseil en investissement. Le biais affiché résulte d'une analyse automatisée
      d'actualités publiques et peut être incomplet, en retard ou erroné. Faites toujours vos propres recherches.
    </div>

    <div class="grid">
      {''.join(cards_html)}
    </div>

    <section class="sources">
      <h3>🗞️ Actualités prises en compte</h3>
      <ul>
        {sources_html}
      </ul>
    </section>

    <footer>
      Sources : Google News, Yahoo Finance, ForexFactory · Analyse : Claude (Anthropic) avec repli automatique par mots-clés.
    </footer>
  </div>
</body>
</html>
"""


def main() -> None:
    headlines = fetch_headlines()
    print(f"[info] {len(headlines)} actualités récupérées.", file=sys.stderr)

    analysis = None
    if headlines:
        prompt = build_prompt(headlines)
        analysis = call_claude(prompt)

    if analysis is None:
        analysis = fallback_rule_based(headlines)
        print("[info] Analyse générée via le moteur de repli par mots-clés.", file=sys.stderr)
    else:
        print("[info] Analyse générée via l'API Anthropic (Claude).", file=sys.stderr)

    analysis = normalize_analysis(analysis)

    generated_at = datetime.now(timezone.utc)
    output = generate_html(analysis, headlines, generated_at)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"[ok] index.html généré ({out_path}).", file=sys.stderr)


if __name__ == "__main__":
    main()
