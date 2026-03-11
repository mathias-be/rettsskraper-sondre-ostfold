import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

API_URL = "https://www.domstol.no/api/episerver/v3/beramming"
CACHE_FILE = Path("cache.json")

DOMSTOL_NAVN = "Søndre Østfold tingrett"
DOMSTOL_ID = os.environ.get("DOMSTOL_ID")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

SAKSTYPER = ("TVI", "TOV", "MED", "SKJ")

HIGH_PRIORITY_WORDS = [
    "barn",
    "unge",
    "narkotika", 
    "ungdom",
    "mindreår",
    "mindreårig",
    "grov",
    "førstegangsfengsling", 
    "ran",
    "drap",
]

MEDIUM_PRIORITY_WORDS = [
    "arbeidsforhold",
    "arbeidsmiljø",
    "arbeidsrett",
    "oppsigelse",
    "avskjed",
    "ansettelse",
    "trakassering",
    "varsling",
]

LOW_PRIORITY_WORDS = [
    "foreldretvist",
    "foreldreansvar",
    "samvær",
    "fast bosted",
    "barnefordeling",
]

INTERESTING_PARTIES = [
    "kommune",
    "politi",
    "sykehus",
    "statsforvalter",
    "skatteetaten",
    "nav",
    "skole",
    "universitet",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.domstol.no/no/nar-gar-rettssaken/",
}


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


def bygg_params(page_number):
    if not DOMSTOL_ID:
        raise RuntimeError("DOMSTOL_ID mangler i GitHub Secrets")

    today = datetime.now()

    return {
        "fraDato": (today - timedelta(days=14)).strftime("%Y-%m-%d"),
        "tilDato": (today + timedelta(days=365)).strftime("%Y-%m-%d"),
        "domstolid": DOMSTOL_ID,
        "sortTerm": "rettsmoete",
        "sortAscending": "true",
        "pageSize": "1000",
        "pageNumber": str(page_number),
    }


def hent_en_side(page_number):
    response = requests.get(
        API_URL,
        params=bygg_params(page_number),
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def hent_alle_saker():
    alle_saker = []
    page_number = 1
    rapportert_total = None

    while True:
        data = hent_en_side(page_number)
        hits = data.get("hits", [])
        count = data.get("count")

        if rapportert_total is None:
            rapportert_total = count
            print(f"API-et rapporterer totalt {rapportert_total} saker for {DOMSTOL_NAVN}")

        print(f"Hentet side {page_number}: {len(hits)} saker")

        if not hits:
            break

        alle_saker.extend(hits)

        if len(hits) < 1000:
            break

        page_number += 1

    print(f"Hentet totalt {len(alle_saker)} saker fra API-et for {DOMSTOL_NAVN}")
    return alle_saker


def bygg_sakslenke(sak_id):
    return f"https://www.domstol.no/no/nar-gar-rettssaken/?saksid={sak_id}"


def finn_sakstype(saksnr):
    for sakstype in SAKSTYPER:
        if sakstype in saksnr:
            return sakstype
    return "UKJENT"


def unike_verdier(verdier):
    sett = set()
    resultat = []
    for verdi in verdier:
        if verdi not in sett:
            resultat.append(verdi)
            sett.add(verdi)
    return resultat


def parse_sak_dato(sak):
    startdato = sak.get("startdato")
    if not startdato:
        return None
    try:
        return datetime.strptime(startdato[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def formater_rettsmoete(sak):
    startdato = sak.get("startdato", "")
    rettsmoete = startdato[:10] if startdato else "Ukjent"

    intervaller = sak.get("rettsmoeteIntervaller") or []
    if intervaller:
        start = intervaller[0].get("start", "")
        end = intervaller[0].get("end", "")
        if start and end:
            rettsmoete = f"{start} – {end}"

    return rettsmoete


def vurder_sak(sak):
    saken_gjelder = (sak.get("sakenGjelder") or "").lower()
    parter = (sak.get("parter") or "").lower()
    samlet_text = f"{saken_gjelder} {parter}"
    saksnr = sak.get("saksnummer", "")
    sakstype = finn_sakstype(saksnr)

    score = 0
    reasons = []

    # Alle straffesaker er interessante som basis
    if sakstype == "TOV":
        score += 3
        reasons.append("straffesak (TOV)")
    elif sakstype == "MED":
        score += 2
        reasons.append("meddomssak (MED)")
    elif sakstype == "SKJ":
        score += 1
        reasons.append("kjennelse/skjønn (SKJ)")
    elif sakstype == "TVI":
        reasons.append("tvistesak (TVI)")

    for word in HIGH_PRIORITY_WORDS:
        if word in samlet_text:
            score += 5
            reasons.append(f'treff på "{word}"')

    for word in MEDIUM_PRIORITY_WORDS:
        if word in samlet_text:
            score += 2
            reasons.append(f'treff på "{word}"')

    for word in INTERESTING_PARTIES:
        if word in parter:
            score += 1
            reasons.append(f'part inneholder "{word}"')

    low_hits = []
    for word in LOW_PRIORITY_WORDS:
        if word in samlet_text:
            low_hits.append(word)

    if low_hits:
        score -= 2
        for word in low_hits:
            reasons.append(f'treff på "{word}" (lavere prioritet)')

    reasons = unike_verdier(reasons)

    if score >= 5:
        nivå = "high"
        label = "🔥 Høy interesse"
    elif score >= 2:
        nivå = "medium"
        label = "👀 Interessant"
    else:
        nivå = "low"
        label = "ℹ️ Lav prioritet"

    return {
        "score": score,
        "nivå": nivå,
        "label": label,
        "sakstype": sakstype,
        "reasons": reasons[:6],
    }


def send_slack_varsel(sakinfo, vurdering):
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL mangler")

    begrunnelse = "\n".join([f"• {grunn}" for grunn in vurdering["reasons"]]) or "• ny sak i domstolen"

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{vurdering['label']} – {sakinfo['domstol']}",
                },
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
                        f"*Prioritet:* {vurdering['label']} (score {vurdering['score']})"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Hvorfor flagget:*\n{begrunnelse}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Åpne saken",
                        },
                        "url": sakinfo["sakslenke"],
                        "style": "primary",
                    }
                ],
            },
        ]
    }

    response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()
    print(f"Sendte Slack-varsel for {sakinfo['saksnr']} ({vurdering['nivå']})")


def main():
    cache = les_cache()
    saker = hent_alle_saker()

    idag = datetime.now().date()

    antall_riktig_sakstype = 0
    antall_fremtidige = 0
    antall_nye_saker = 0
    antall_sendt = 0

    for sak in saker:
        saksnr = sak.get("saksnummer", "")
        if not any(sakstype in saksnr for sakstype in SAKSTYPER):
            continue
        antall_riktig_sakstype += 1

        sak_dato = parse_sak_dato(sak)
        if sak_dato is None:
            print(f"Skipper uten gyldig dato: {saksnr}")
            continue

        if sak_dato < idag:
            print(f"Skipper gammel sak: {saksnr} ({sak_dato})")
            continue
        antall_fremtidige += 1

        sak_id = sak.get("sakId", "")
        cache_key = f"{sak_id}:{saksnr}"
        if cache_key in cache:
            continue
        antall_nye_saker += 1

        sakinfo = {
            "domstol": sak.get("domstol") or DOMSTOL_NAVN,
            "saksnr": saksnr,
            "rettsmoete": formater_rettsmoete(sak),
            "saken_gjelder": sak.get("sakenGjelder") or "–",
            "parter": sak.get("parter") or "–",
            "sakslenke": bygg_sakslenke(sak_id),
        }

        vurdering = vurder_sak(sak)

        # SEND ALLTID, også low og MED
        send_slack_varsel(sakinfo, vurdering)
        antall_sendt += 1

        cache[cache_key] = datetime.now().isoformat()

    skriv_cache(cache)

    print(f"Saker med riktig sakstype: {antall_riktig_sakstype}")
    print(f"Fremtidige saker: {antall_fremtidige}")
    print(f"Nye saker: {antall_nye_saker}")
    print(f"Slack-varsler sendt: {antall_sendt}")


if __name__ == "__main__":
    main()
