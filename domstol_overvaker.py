import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

API_URL = "https://www.domstol.no/api/episerver/v3/beramming"
CACHE_FILE = Path("cache.json")

DOMSTOL_NAVN = "Søndre Østfold tingrett"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

SAKSTYPER = ("TVI", "TOV", "MED", "SKJ")

HIGH_PRIORITY_WORDS = [
    "drap", "voldtekt", "seksu", "overgrep", "mishandling", "vold",
    "ran", "underslag", "bedrageri", "svindel", "korrupsjon",
    "oppsigelse", "avskjed", "arbeidsmiljø", "barnevern",
    "kommune", "politiet", "sykehus", "skole", "offentlig",
]

MEDIUM_PRIORITY_WORDS = [
    "erstatning", "kontrakt", "tvist", "arbeidsrett",
    "nabotvist", "eiendom", "byggetvist", "entreprise",
]

ALWAYS_INTERESTING_PARTIES = [
    "kommune", "as", "politi", "sykehus", "statsforvalter",
    "skatteetaten", "nav", "skole", "universitet",
]


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


def finn_sakstype(saksnr):
    for stype in SAKSTYPER:
        if stype in saksnr:
            return stype
    return "UKJENT"


def vurder_sak(sak):
    saken_gjelder = (sak.get("sakenGjelder") or "").lower()
    parter = (sak.get("parter") or "").lower()
    saksnr = sak.get("saksnummer", "")
    sakstype = finn_sakstype(saksnr)

    score = 0
    reasons = []

    if sakstype == "TOV":
        score += 3
        reasons.append("sakstype TOV")
    elif sakstype in ("MED", "SKJ"):
        score += 2
        reasons.append(f"sakstype {sakstype}")
    elif sakstype == "TVI":
        score += 1
        reasons.append("sakstype TVI")

    for word in HIGH_PRIORITY_WORDS:
        if word in saken_gjelder or word in parter:
            score += 3
            reasons.append(f'treff på "{word}"')

    for word in MEDIUM_PRIORITY_WORDS:
        if word in saken_gjelder or word in parter:
            score += 1
            reasons.append(f'treff på "{word}"')

    for word in ALWAYS_INTERESTING_PARTIES:
        if word in parter:
            score += 2
            reasons.append(f'part inneholder "{word}"')

    # Fjern duplikater men behold rekkefølge
    unique_reasons = []
    for reason in reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)

    if score >= 6:
        nivå = "high"
        label = "🔥 Høy interesse"
    elif score >= 3:
        nivå = "medium"
        label = "👀 Mulig interessant"
    else:
        nivå = "low"
        label = "ℹ️ Ny sak"

    return {
        "score": score,
        "nivå": nivå,
        "label": label,
        "sakstype": sakstype,
        "reasons": unique_reasons[:5],
    }


def send_slack_varsel(sakinfo, vurdering):
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL mangler")

    begrunnelse = "\n".join([f"• {r}" for r in vurdering["reasons"]]) or "• ny sak i domstolen"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{vurdering['label']} – {sakinfo['domstol']}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Saksnummer:* {sakinfo['saksnr']}\n"
                        f"*Sakstype:* {vurdering['sakstype']}\n"
                        f"*Rettsmøte:* {sakinfo['rettsmoete']}\n"
                        f"*Saken gjelder:* {sakinfo['saken_gjelder']}\n"
                        f"*Parter:* {sakinfo['parter']}\n"
                        f"*Vurdering:* score {vurdering['score']}"
                    )
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Hvorfor flagget:*\n{begrunnelse}"
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
    print(f"Sendte Slack-varsel for {sakinfo['saksnr']} ({vurdering['nivå']})")


def main():
    cache = les_cache()
    saker = hent_saker()

    antall_riktig_domstol = 0
    antall_riktig_sakstype = 0
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

        sak_id = sak.get("sakId", "")
        cache_key = f"{sak_id}:{saksnr}"
        if cache_key in cache:
            continue

        startdato = sak.get("startdato", "")
        rettsmoete = startdato[:10] if startdato else "Ukjent"

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

        vurdering = vurder_sak(sak)

        # send bare medium/høy som standard
        if vurdering["nivå"] in ("medium", "high"):
            send_slack_varsel(sakinfo, vurdering)
            antall_sendt += 1
        else:
            print(f"Skipper lav interesse: {saksnr}")

        cache[cache_key] = datetime.now().isoformat()

    skriv_cache(cache)

    print(f"Saker i riktig domstol: {antall_riktig_domstol}")
    print(f"Saker med riktig sakstype: {antall_riktig_sakstype}")
    print(f"Slack-varsler sendt: {antall_sendt}")


if __name__ == "__main__":
    main()
