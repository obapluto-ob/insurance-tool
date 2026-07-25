from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
import os

trader = Blueprint("trader", __name__, url_prefix="/tradingbot",
                   template_folder="trader_templates")

BOT_SECRET = os.getenv("BOT_SECRET_KEY", "changeme_trader")

events = []


def _log(event_type, data):
    events.insert(0, {
        "type": event_type,
        "data": data,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    })
    if len(events) > 200:
        events.pop()


@trader.route("/")
def index():
    return render_template("trader_index.html")


@trader.route("/api/event", methods=["POST"])
def receive_event():
    body = request.json or {}
    if body.get("secret") != BOT_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    _log(body.get("event", "unknown"), body.get("data", {}))
    return jsonify({"ok": True})


@trader.route("/api/feed")
def feed():
    return jsonify(events[:50])
