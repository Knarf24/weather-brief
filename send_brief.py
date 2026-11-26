"""Send the morning brief as a phone notification (ntfy), to Telegram and/or WhatsApp. Runs every morning on GitHub Actions,
or on any computer with Python 3.10+ (no packages to install).

    python send_brief.py Durban            # just print the brief
    python send_brief.py "Cape Town" --send

Where it goes is set with environment variables (on GitHub: Settings > Secrets and variables > Actions):

    NTFY_TOPIC                              free phone notification: install the ntfy app, subscribe to this topic
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   free: make a bot with @BotFather, see README
    CALLMEBOT_PHONE, CALLMEBOT_APIKEY      free WhatsApp: text @CallMeBot once to get a key, see README
    CITY                                    used when no city is given on the command line
    BRIEF_STYLE=full                        send the whole brief instead of the short 3-line message
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request

import brief

UA = {"User-Agent": "weather-brief/1.0 (+https://github.com/Knarf24/weather-brief)"}


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


async def get_json(url: str) -> dict:
    return json.loads(await asyncio.to_thread(_get, url))


def send_telegram(token: str, chat_id: str, text: str) -> None:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        if not json.loads(r.read()).get("ok"):
            raise RuntimeError("Telegram refused the message")


def send_whatsapp_callmebot(phone: str, apikey: str, text: str) -> None:
    q = urllib.parse.urlencode({"phone": phone, "text": text, "apikey": apikey})
    body = _get("https://api.callmebot.com/whatsapp.php?" + q, timeout=60).decode("utf-8", "replace")
    low = body.lower()
    if "queued" not in low and "sent" not in low:  # e.g. "APIKey is invalid"
        raise RuntimeError("CallMeBot didn't accept it: " + " ".join(body.split())[:200])


def send_ntfy(topic: str, text: str, click: str | None = None, server: str | None = None) -> None:
    """Phone notification through ntfy (free app, no account): anyone subscribed to the topic gets it."""
    server = (server or "https://ntfy.sh").rstrip("/")
    headers = {**UA, "Title": "Morning brief", "Tags": "partly_sunny", "Content-Type": "text/plain; charset=utf-8"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"{server}/{urllib.parse.quote(topic.strip())}", data=text.encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status >= 300:
            raise RuntimeError(f"ntfy answered {r.status}")


PAGE = "https://knarf24.github.io/weather-brief/"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily Weather & Air Brief")
    ap.add_argument("city", nargs="?", default=os.environ.get("CITY") or "Durban")
    ap.add_argument("--send", action="store_true", help="send to Telegram / WhatsApp if their keys are set")
    ap.add_argument("--at-hour", default=None,
                    help="only send if it is one of these hours (0-23, e.g. 6,12,17) in the city right now; "
                         "lets an hourly job send at local time")
    args = ap.parse_args(argv)

    result = asyncio.run(brief.brief_for_city(args.city, get_json))
    # The phone message is the short 3-line version unless BRIEF_STYLE=full.
    full = (os.environ.get("BRIEF_STYLE") or "").lower() == "full"
    text = result["text"] if full else result["short_text"]
    print(f"[{result['place']}, local time {result['now'][11:16]} ({result.get('timezone')})]\n")
    print(text)
    if not args.send:
        return 0
    if args.at_hour is not None:
        local_hour = int(result["now"][11:13])
        hours = {int(x) for x in str(args.at_hour).replace(" ", "").split(",") if x}
        if local_hour not in hours:
            when = ", ".join(f"{h:02d}:xx" for h in sorted(hours))
            print(f"\nNot sending: it's {result['now'][11:16]} in {args.city}; the brief goes out at {when}.")
            return 0

    env = os.environ
    sent, failed = [], []
    targets = []
    if env.get("NTFY_TOPIC"):
        click = PAGE + "?" + urllib.parse.urlencode({"city": args.city})
        targets.append(("phone notification (ntfy)", lambda: send_ntfy(env["NTFY_TOPIC"], text, click, env.get("NTFY_SERVER"))))
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        targets.append(("Telegram", lambda: send_telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], text)))
    if env.get("CALLMEBOT_PHONE") and env.get("CALLMEBOT_APIKEY"):
        targets.append(("WhatsApp", lambda: send_whatsapp_callmebot(env["CALLMEBOT_PHONE"], env["CALLMEBOT_APIKEY"], text)))
    if not targets:
        print("\nNothing sent: no ntfy topic, Telegram or WhatsApp keys are set (see README).")
        return 0
    for name, send in targets:
        try:
            send()
            sent.append(name)
        except Exception as e:  # keep going so one broken channel doesn't block the other
            failed.append(name)
            print(f"\n{name} failed: {e}", file=sys.stderr)
    if sent:
        print("\nSent to " + " and ".join(sent) + ".")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
