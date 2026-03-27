import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import requests
import streamlit as st

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def _parse_json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


def extract_slug(url_or_slug: str) -> str:
    text = (url_or_slug or "").strip()
    if not text:
        return ""
    if "polymarket.com" in text:
        parsed = urlparse(text)
        parts = [p for p in parsed.path.split("/") if p]
        # Expected: /event/<slug> or /market/<slug>
        if len(parts) >= 2 and parts[0] in {"event", "market"}:
            return parts[1]
        if parts:
            return parts[-1]
    return text


def fetch_event_by_slug(slug: str):
    resp = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        return data[0]
    return None


def build_markets(event):
    markets = event.get("markets") or []
    items = []
    for m in markets:
        outcomes = _parse_json_list(m.get("outcomes"))
        clob_ids = _parse_json_list(m.get("clobTokenIds") or m.get("clob_token_ids"))
        if not outcomes or not clob_ids:
            continue
        yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
        token_id = clob_ids[yes_idx] if yes_idx < len(clob_ids) else clob_ids[0]
        items.append(
            {
                "question": m.get("question") or m.get("title") or "Unknown",
                "market_id": m.get("id") or m.get("conditionId") or "",
                "token_id": token_id,
                "outcomes": outcomes,
                "clob_token_ids": clob_ids,
            }
        )
    return items


def fetch_orderbooks(token_ids):
    if not token_ids:
        return {}
    payload = [{"token_id": tid} for tid in token_ids]
    resp = requests.post(f"{CLOB_API}/books", json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    books = {}
    if isinstance(data, list):
        for book in data:
            books[book.get("asset_id")] = book
    return books


APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"


def image_link_html(path: Path, url: str, height_px: int = 48) -> str:
    if not path.exists():
        return f'<a href="{url}" target="_blank">{url}</a>'
    data = path.read_bytes()
    if path.name.lower().endswith(".svg"):
        mime = "image/svg+xml"
    elif path.name.lower().endswith(".png"):
        mime = "image/png"
    else:
        mime = "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return (
        f'<a href="{url}" target="_blank" style="text-decoration:none; display:block;">'
        f'<img src="data:{mime};base64,{b64}" '
        f'style="height:{height_px}px; width:auto; display:block; margin:0 auto;" />'
        f"</a>"
    )


LANGS = {
    "Русский": "ru",
    "English": "en",
}

I18N = {
    "ru": {
        "title": "Калькулятор ставок Polymarket",
        "caption": "Шаг 1: загрузить событие. Шаг 2: выбрать исходы и задать цены.",
        "inputs": "Параметры",
        "event_input": "Ссылка на событие или slug",
        "budget": "Сумма (USD)",
        "count_outcomes": "Количество исходов",
        "load": "Загрузить событие",
        "missing_slug": "Вставьте ссылку на событие Polymarket или slug.",
        "load_failed": "Не удалось загрузить событие:",
        "not_found": "Событие не найдено. Проверьте ссылку/slug.",
        "markets_found": "Найдено рынков:",
        "select_outcomes": "Выберите исходы (markets)",
        "select_warning": "Нужно выбрать хотя бы один исход.",
        "refresh": "Обновить ордербуки",
        "orderbooks_failed": "Не удалось получить ордербуки:",
        "orderbooks_title": "Ордербуки (5 уровней и спред)",
        "outcome": "Исход",
        "token_id": "Token ID",
        "best_bid": "Лучшая цена bid",
        "best_ask": "Лучшая цена ask",
        "set_prices": "Задайте лимитные цены",
        "price_label": "Цена",
        "allocation_title": "Распределение и прибыль",
        "total_price_err": "Сумма цен должна быть > 0.",
        "alloc_outcome": "Исход",
        "alloc_price": "Цена",
        "alloc_shares": "Shares",
        "alloc_cost": "Сумма ($)",
        "equal_payout": "Равная выплата при любом исходе:",
        "profit": "Прибыль на выигрышный исход (выплата - сумма):",
        "orders_title": "Лимитные ордера для выставления",
        "side": "Сторона",
        "price": "Цена",
        "size": "Размер (shares)",
        "info_start": "Вставьте ссылку/slug в сайдбаре и нажмите «Загрузить событие».",
        "language": "Язык",
        "spread": "Спред",
        "price_cents": "Цена (¢)",
        "size_label": "Размер",
        "token_expander": "Token ID",
    },
    "en": {
        "title": "Polymarket bet calculator",
        "caption": "Step 1: load an event. Step 2: pick outcomes and set limit prices.",
        "inputs": "Inputs",
        "event_input": "Event link or slug",
        "budget": "Budget (USD)",
        "count_outcomes": "Number of outcomes",
        "load": "Load event",
        "missing_slug": "Please paste a Polymarket event link or slug.",
        "load_failed": "Failed to fetch event:",
        "not_found": "Event not found. Check the link/slug.",
        "markets_found": "Markets found:",
        "select_outcomes": "Select outcomes (markets)",
        "select_warning": "Select at least one outcome.",
        "refresh": "Refresh orderbooks",
        "orderbooks_failed": "Failed to fetch orderbooks:",
        "orderbooks_title": "Orderbooks (5 levels and spread)",
        "outcome": "Outcome",
        "token_id": "Token ID",
        "best_bid": "Best bid",
        "best_ask": "Best ask",
        "set_prices": "Set your limit prices",
        "price_label": "Price",
        "allocation_title": "Allocation & profit",
        "total_price_err": "Total price must be > 0.",
        "alloc_outcome": "Outcome",
        "alloc_price": "Price",
        "alloc_shares": "Shares",
        "alloc_cost": "Cost ($)",
        "equal_payout": "Equal payout if any outcome wins:",
        "profit": "Profit per winning outcome (payout - budget):",
        "orders_title": "Limit orders to place",
        "side": "Side",
        "price": "Price",
        "size": "Size (shares)",
        "info_start": "Paste a Polymarket event link or slug in the sidebar and click 'Load event'.",
        "language": "Language",
        "spread": "Spread",
        "price_cents": "Price (¢)",
        "size_label": "Size",
        "token_expander": "Token ID",
    },
}

st.set_page_config(page_title="Polymarket Bet Calculator", layout="wide")

with st.sidebar:
    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown(
            image_link_html(
                ASSETS_DIR / "polymarket-white.svg",
                "https://polymarket.com/?r=ORACLEPM",
                height_px=48,
            ),
            unsafe_allow_html=True,
        )
    with col_right:
        st.markdown(
            image_link_html(
                ASSETS_DIR / "proxyline.jpg",
                "https://proxyline.net?line=208153",
                height_px=48,
            ),
            unsafe_allow_html=True,
        )

    lang_label = st.selectbox("Language / Язык", list(LANGS.keys()), index=0)
    lang = LANGS[lang_label]
    t = I18N[lang]

st.title(t["title"])
st.caption(t["caption"])

with st.sidebar:
    st.header(t["inputs"])
    event_input = st.text_input(t["event_input"], placeholder="https://polymarket.com/event/...")
    budget = st.number_input(t["budget"], min_value=0.01, value=15.0, step=1.0)
    count_outcomes = st.number_input(t["count_outcomes"], min_value=1, value=3, step=1)
    load_clicked = st.button(t["load"])

if load_clicked:
    slug = extract_slug(event_input)
    if not slug:
        st.error(t["missing_slug"])
    else:
        try:
            event = fetch_event_by_slug(slug)
        except Exception as exc:
            st.error(f"{t['load_failed']} {exc}")
            event = None
        if event:
            st.session_state["event"] = event
            st.session_state["markets"] = build_markets(event)
        else:
            st.error(t["not_found"])

event = st.session_state.get("event")
markets = st.session_state.get("markets", [])

if event and markets:
    st.subheader(event.get("title") or "Event")
    st.write(event.get("description") or "")
    st.caption(f"{t['markets_found']} {len(markets)}")

    market_labels = [m["question"] for m in markets]
    default_selected = market_labels[: int(count_outcomes)]
    selected_labels = st.multiselect(t["select_outcomes"], market_labels, default=default_selected)

    selected = [m for m in markets if m["question"] in selected_labels]
    if not selected:
        st.warning(t["select_warning"])
    else:
        if st.button(t["refresh"]):
            st.session_state.pop("orderbooks", None)

        if "orderbooks" not in st.session_state:
            try:
                st.session_state["orderbooks"] = fetch_orderbooks([m["token_id"] for m in selected])
            except Exception as exc:
                st.error(f"{t['orderbooks_failed']} {exc}")
                st.session_state["orderbooks"] = {}

        orderbooks = st.session_state.get("orderbooks", {})

        with st.expander(t["orderbooks_title"], expanded=False):
            tabs = st.tabs([m["question"] for m in selected])
            for tab, m in zip(tabs, selected):
                with tab:
                    book = orderbooks.get(m["token_id"], {})
                    asks = book.get("asks") or []
                    bids = book.get("bids") or []

                    asks_sorted = sorted(
                        asks,
                        key=lambda x: float(x.get("price")) if x.get("price") is not None else 0.0,
                    )
                    bids_sorted = sorted(
                        bids,
                        key=lambda x: float(x.get("price")) if x.get("price") is not None else 0.0,
                        reverse=True,
                    )

                    best_bid = bids_sorted[0]["price"] if bids_sorted else None
                    best_ask = asks_sorted[0]["price"] if asks_sorted else None
                    spread = None
                    if best_bid is not None and best_ask is not None:
                        try:
                            spread = round((float(best_ask) - float(best_bid)) * 100, 2)
                        except (TypeError, ValueError):
                            spread = None

                    if spread is not None:
                        st.caption(f"{t['spread']}: {spread}¢")
                    else:
                        st.caption(f"{t['spread']}: —")

                    col_bids, col_asks = st.columns(2)

                    ask_rows = []
                    for a in asks_sorted[:5]:
                        try:
                            price = round(float(a.get("price")) * 100, 2)
                        except (TypeError, ValueError):
                            price = a.get("price")
                        ask_rows.append({"Side": "ASK", t["price_cents"]: price, t["size_label"]: a.get("size")})

                    bid_rows = []
                    for b in bids_sorted[:5]:
                        try:
                            price = round(float(b.get("price")) * 100, 2)
                        except (TypeError, ValueError):
                            price = b.get("price")
                        bid_rows.append({"Side": "BID", t["price_cents"]: price, t["size_label"]: b.get("size")})

                    with col_bids:
                        st.dataframe(bid_rows, use_container_width=True, hide_index=True)
                    with col_asks:
                        st.dataframe(ask_rows, use_container_width=True, hide_index=True)

                    with st.expander(t["token_expander"]):
                        st.code(m["token_id"])

        st.subheader(t["set_prices"])
        price_inputs = []
        for m in selected:
            book = orderbooks.get(m["token_id"], {})
            default_price = None
            try:
                default_price = float((book.get("asks") or [{}])[0].get("price")) * 100
            except (TypeError, ValueError):
                default_price = 50.0
            if default_price < 1.0:
                default_price = 1.0
            if default_price > 99.9:
                default_price = 99.9
            price_cents = st.number_input(
                f"{m['question']} {t['price_label']}",
                min_value=1.0,
                max_value=99.9,
                value=default_price,
                step=0.1,
                key=f"price_{m['token_id']}",
            )
            price_inputs.append((m, float(price_cents) / 100.0))

        total_price = sum(p for _, p in price_inputs)
        if total_price <= 0:
            st.error(t["total_price_err"])
        else:
            shares = budget / total_price
            profit = shares - budget

            alloc_rows = []
            for m, price in price_inputs:
                cost = shares * price
                alloc_rows.append(
                    {
                        t["alloc_outcome"]: m["question"],
                        t["alloc_price"]: round(price * 100, 2),
                        t["alloc_shares"]: round(shares, 4),
                        t["alloc_cost"]: round(cost, 4),
                    }
                )
            with st.expander(t["allocation_title"], expanded=True):
                st.dataframe(alloc_rows, use_container_width=True)
                st.info(f"{t['equal_payout']} {shares:.4f} shares")
                st.info(f"{t['profit']} {profit:.4f} USD")

else:
    st.info(t["info_start"])


