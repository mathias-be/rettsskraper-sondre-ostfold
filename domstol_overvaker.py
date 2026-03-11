import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

API_URL = "https://www.domstol.no/api/episerver/v3/beramming"
CACHE_FILE = Path("cache.json")

DOMSTOL_NAVN = "Søndre Østfold tingrett"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

VARSEL_DAGER = 14
SAKSTYPER = ("TVI", "MED", "SKJ")


def les_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def skriv_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def hent_saker():
    today = datetime.now()

    params = {
        "fraDato": (today - timedelta(days=14)).strftime("%Y-%m-%d"),
        "tilDato": (today + timedelta(days=365)).strftime("%Y-%m-%d"),
        "sortTerm": "rettsmoete",
        "sortAscending": "true",
        "pageSize": "1000",
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.domstol.no/no/nar-gar-rettssaken/",
    }

    r = requests.get(API_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()

    data = r.json()
    return data.get("hits", [])


def bygg_sakslenke(sak_id):
    return f"https://www.domstol.no/no/nar-gar-rettssaken/?saksid={sak_id}"


def skal_varsles(sak, idag, grense):
    domstol = sak.get("domstol")
    if domstol != DOMSTOL_NAVN:
        return False

    saksnr = sak.get("saksnummer", "")
    if not any(stype in saksnr for stype in SAKSTYPER):
        return False

    startdato = sak.get("startdato")
    if not startdato:
        return False

    sak_dato = datetime.strptime(startdato[:10], "%Y-%m-%d")
    return idag <= sak_dato <= grense


def formater_rettsmoete(sak):
    intervaller = sak.get("rettsmoeteIntervaller") or []
    if intervaller:
        start = intervaller[0].get("start", "")
        end = intervaller[0].get("end", "")
        if start and end:
            return f"{start} – {end}"
    return sak.get("startdato", "")[:10]


def send_slack_varsel(sakinfo):
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL mangler")

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "⚖️ Ny rettssak funnet"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Domstol:* {sakinfo['domstol']}\n"
                        f"*Saksnummer:* {sakinfo['saksnr']}\n"
                        f"*Rettsmøte:* {sakinfo['rettsmoete']}\n"
                        f"*Saken gjelder:* {sakinfo['saken_gjelder']}\n"
                        f"*Parter:* {sakinfo['parter']}"
                    )
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Åpne saken"
                        },
                        "url": sakinfo["sakslenke"],
                        "style": "primary"
                    }
                ]
            }
        ]
    }

    response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()


def main():
    cache = les_cache()
    saker = hent_saker()

    idag = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    grense = idag + timedelta(days=VARSEL_DAGER)

    for sak in saker:
        saksnr = sak.get("saksnummer", "")
        sak_id = sak.get("sakId", "")
        cache_key = f"{sak_id}:{saksnr}"

        if cache_key in cache:
            continue

        if not skal_varsles(sak, idag, grense):
            continue

        sakinfo = {
            "domstol": sak.get("domstol", "Ukjent domstol"),
            "saksnr": saksnr,
            "rettsmoete": formater_rettsmoete(sak),
            "saken_gjelder": sak.get("sakenGjelder") or "–",
            "parter": sak.get("parter") or "–",
            "sakslenke": bygg_sakslenke(sak_id),
        }

        send_slack_varsel(sakinfo)
        cache[cache_key] = datetime.now().isoformat()

    skriv_cache(cache)


if __name__ == "__main__":
    main()
