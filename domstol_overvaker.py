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
    hits = data.get("hits", [])
    print(f"Hentet {len(hits)} saker fra API-et")
    return hits


def bygg_sakslenke(sak_id):
    return f"https://www.domstol.no/no/nar-gar-rettssaken/?saksid={sak_id}"


def send_slack_varsel(sakinfo):
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL mangler")

    payload = {
        "text": (
            "⚖️ Ny rettssak funnet\n"
            f"Domstol: {sakinfo['domstol']}\n"
            f"Saksnummer: {sakinfo['saksnr']}\n"
            f"Rettsmøte: {sakinfo['rettsmoete']}\n"
            f"Saken gjelder: {sakinfo['saken_gjelder']}\n"
            f"Parter: {sakinfo['parter']}\n"
            f"Sak: {sakinfo['sakslenke']}"
        )
    }

    response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()
    print(f"Sendte Slack-varsel for {sakinfo['saksnr']}")


def main():
    cache = les_cache()
    saker = hent_saker()

    idag = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    grense = idag + timedelta(days=VARSEL_DAGER)

    antall_riktig_domstol = 0
    antall_riktig_sakstype = 0
    antall_innenfor_dato = 0
    antall_sendt = 0

    for sak in saker:
        domstol = sak.get("domstol", "")
        if domstol != DOMSTOL_NAVN:
            continue
        antall_riktig_domstol += 1

        saksnr = sak.get("saksnummer", "")
        if not any(stype in saksnr for stype in SAKSTYPER):
            continue
        antall_riktig_sakstype += 1

        startdato = sak.get("startdato")
        if not startdato:
            continue

        sak_id = sak.get("sakId", "")
        cache_key = f"{sak_id}:{saksnr}"
        if cache_key in cache:
            print(f"Allerede varslet: {saksnr}")
            continue

        rettsmoete = startdato[:10]
        intervaller = sak.get("rettsmoeteIntervaller") or []
        if intervaller:
            start = intervaller[0].get("start", "")
            end = intervaller[0].get("end", "")
            if start and end:
                rettsmoete = f"{start} – {end}"

        sakinfo = {
            "domstol": domstol,
            "saksnr": saksnr,
            "rettsmoete": rettsmoete,
            "saken_gjelder": sak.get("sakenGjelder") or "–",
            "parter": sak.get("parter") or "–",
            "sakslenke": bygg_sakslenke(sak_id),
        }

        send_slack_varsel(sakinfo)
        cache[cache_key] = datetime.now().isoformat()
        antall_sendt += 1

    skriv_cache(cache)

    print(f"Saker i riktig domstol: {antall_riktig_domstol}")
    print(f"Saker med riktig sakstype: {antall_riktig_sakstype}")
    print(f"Saker innenfor dato: {antall_innenfor_dato}")
    print(f"Slack-varsler sendt: {antall_sendt}")


if __name__ == "__main__":
    main()
