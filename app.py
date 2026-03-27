import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import math
import time

import requests
import streamlit as st

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"


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
                "market_id": m.get("id") or "",
                "condition_id": m.get("conditionId") or "",
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


def fetch_trades(condition_id: str, limit: int = 200):
    if not condition_id:
        return []
    try:
        resp = requests.get(
            f"{DATA_API}/trades",
            params={"market": condition_id, "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data.get("data") or []
        return []
    except Exception:
        return []


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


def _sorted_book(book_side, reverse=False):
    return sorted(
        book_side or [],
        key=lambda x: float(x.get("price")) if x.get("price") is not None else 0.0,
        reverse=reverse,
    )


def suggest_price_from_book(book, mode: str) -> float | None:
    asks_sorted = _sorted_book(book.get("asks"), reverse=False)
    bids_sorted = _sorted_book(book.get("bids"), reverse=True)
    if not asks_sorted:
        return None

    best_ask = float(asks_sorted[0]["price"])
    best_bid = float(bids_sorted[0]["price"]) if bids_sorted else max(best_ask - 0.01, 0.01)
    spread = max(best_ask - best_bid, 0.0)

    # Base aggressiveness mapped to target fill chance (heuristic).
    # Lower alpha => closer to best ask (higher fill chance).
    base_alpha = {"safe": 0.25, "normal": 0.50, "risk": 0.65}.get(mode, 0.50)

    # Orderbook imbalance adjustment (more bids => slightly lower price ok).
    bid_depth = sum(float(x.get("size", 0)) for x in bids_sorted[:5]) if bids_sorted else 0.0
    ask_depth = sum(float(x.get("size", 0)) for x in asks_sorted[:5]) if asks_sorted else 0.0
    imbalance = 0.0
    if bid_depth + ask_depth > 0:
        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
    adj = 0.10 * (-imbalance)  # more asks => be more aggressive (closer to ask)
    alpha = min(max(base_alpha + adj, 0.0), 1.0)

    price = best_ask - spread * alpha

    # Snap into book levels if available.
    if asks_sorted:
        # Find nearest ask price >= computed price (more likely to fill).
        for a in asks_sorted:
            p = float(a.get("price"))
            if p >= price:
                price = p
                break

    return max(min(price, 0.999), 0.001)


def pick_price_with_target(
    book,
    trades,
    token_id: str,
    target_prob: float,
    horizon_min: float,
    mode: str,
) -> float | None:
    asks_sorted = _sorted_book(book.get("asks"), reverse=False)
    bids_sorted = _sorted_book(book.get("bids"), reverse=True)
    if not asks_sorted:
        return None

    best_ask = float(asks_sorted[0]["price"])
    best_bid = float(bids_sorted[0]["price"]) if bids_sorted else max(best_ask - 0.01, 0.01)
    mid = (best_ask + best_bid) / 2

    candidates = [best_ask, mid]
    candidates.extend([float(b.get("price")) for b in bids_sorted[:5] if b.get("price") is not None])
    candidates = sorted(set([max(min(p, 0.999), 0.001) for p in candidates]), reverse=False)

    if not trades:
        return suggest_price_from_book(book, mode)

    # Choose the lowest price that still meets target probability.
    best = None
    for p in candidates:
        p_est = estimate_fill_probability(book, trades, token_id, p, horizon_min)
        if p_est >= target_prob:
            best = p
            break
    if best is None:
        best = best_ask
    return max(min(best, 0.999), 0.001)


def estimate_fill_probability(book, trades, token_id: str, price: float, horizon_min: float) -> float:
    asks_sorted = _sorted_book(book.get("asks"), reverse=False)
    bids_sorted = _sorted_book(book.get("bids"), reverse=True)
    if not asks_sorted:
        return 0.0

    best_ask = float(asks_sorted[0]["price"])
    best_bid = float(bids_sorted[0]["price"]) if bids_sorted else max(best_ask - 0.01, 0.01)
    if price >= best_ask:
        return 1.0
    if price <= 0:
        return 0.0

    # Queue ahead on bid side (conservative).
    queue = 0.0
    for b in bids_sorted:
        try:
            p = float(b.get("price"))
            if p >= price:
                queue += float(b.get("size", 0))
        except (TypeError, ValueError):
            continue

    # Estimate flow from recent trades for this asset.
    asset_trades = [t for t in trades if str(t.get("asset")) == str(token_id)]
    if not asset_trades:
        return 0.0

    now = time.time()
    ts = [float(t.get("timestamp", now)) for t in asset_trades]
    window_sec = max(now - min(ts), 60.0)
    total_size = 0.0
    for t in asset_trades:
        try:
            total_size += float(t.get("size", 0))
        except (TypeError, ValueError):
            continue
    flow_per_min = total_size / (window_sec / 60.0) if window_sec > 0 else 0.0
    if flow_per_min <= 0:
        return 0.0

    # Expected time to fill (minutes).
    t_fill = (queue + 1.0) / flow_per_min
    if t_fill <= 0:
        return 0.0

    # Poisson-style fill probability within horizon.
    return 1.0 - math.exp(-horizon_min / t_fill)


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
        "mode_title": "Автоподбор цен",
        "mode_safe": "Safe 75%",
        "mode_normal": "Normal 50%",
        "mode_risk": "Risk 35%",
        "mode_hint": "Режим подбирает цены по спреду и объёмам (эвристика).",
        "auto_refresh": "Автообновление",
        "refresh_sec": "Интервал (сек)",
        "horizon": "Горизонт исполнения (мин)",
        "prob_est": "Оценка P(исп.)",
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
        "mode_title": "Auto price mode",
        "mode_safe": "Safe 75%",
        "mode_normal": "Normal 50%",
        "mode_risk": "Risk 35%",
        "mode_hint": "Mode suggests prices using spread and depth (heuristic).",
        "auto_refresh": "Auto refresh",
        "refresh_sec": "Interval (sec)",
        "horizon": "Fill horizon (min)",
        "prob_est": "Est. P(fill)",
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

    auto_refresh = st.checkbox(t["auto_refresh"], value=False)
    refresh_sec = st.number_input(t["refresh_sec"], min_value=5, max_value=120, value=15, step=5)
    horizon_min = st.number_input(t["horizon"], min_value=1, max_value=120, value=15, step=1)

st.title(t["title"])
st.caption(t["caption"])

if auto_refresh:
    if hasattr(st, "autorefresh"):
        st.autorefresh(interval=int(refresh_sec * 1000), key="autorefresh")
    else:
        if "last_refresh" not in st.session_state:
            st.session_state["last_refresh"] = time.time()
        if time.time() - st.session_state["last_refresh"] >= refresh_sec:
            st.session_state["last_refresh"] = time.time()
            st.experimental_rerun()

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

        if auto_refresh or "orderbooks" not in st.session_state:
            try:
                st.session_state["orderbooks"] = fetch_orderbooks([m["token_id"] for m in selected])
            except Exception as exc:
                st.error(f"{t['orderbooks_failed']} {exc}")
                st.session_state["orderbooks"] = {}

        if "trades" not in st.session_state:
            st.session_state["trades"] = {}
        for m in selected:
            if not m["condition_id"]:
                continue
            if auto_refresh or m["condition_id"] not in st.session_state["trades"]:
                st.session_state["trades"][m["condition_id"]] = fetch_trades(m["condition_id"], limit=200)

        orderbooks = st.session_state.get("orderbooks", {})
        trades_cache = st.session_state.get("trades", {})

        with st.expander(t["orderbooks_title"], expanded=False):
            tabs = st.tabs([m["question"] for m in selected])
            for tab, m in zip(tabs, selected):
                with tab:
                    book = orderbooks.get(m["token_id"], {})
                    asks = book.get("asks") or []
                    bids = book.get("bids") or []

                    asks_sorted = _sorted_book(asks, reverse=False)
                    bids_sorted = _sorted_book(bids, reverse=True)

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
        st.markdown(f"**{t['mode_title']}**")
        st.caption(t["mode_hint"])
        mode_cols = st.columns(3)
        if mode_cols[0].button(t["mode_safe"]):
            for m in selected:
                book = orderbooks.get(m["token_id"], {})
                trades = trades_cache.get(m["condition_id"], [])
                price = pick_price_with_target(
                    book, trades, m["token_id"], 0.75, float(horizon_min), "safe"
                )
                if price is None:
                    continue
                st.session_state[f"price_{m['token_id']}"] = round(price * 100, 2)
            st.rerun()
        if mode_cols[1].button(t["mode_normal"]):
            for m in selected:
                book = orderbooks.get(m["token_id"], {})
                trades = trades_cache.get(m["condition_id"], [])
                price = pick_price_with_target(
                    book, trades, m["token_id"], 0.50, float(horizon_min), "normal"
                )
                if price is None:
                    continue
                st.session_state[f"price_{m['token_id']}"] = round(price * 100, 2)
            st.rerun()
        if mode_cols[2].button(t["mode_risk"]):
            for m in selected:
                book = orderbooks.get(m["token_id"], {})
                trades = trades_cache.get(m["condition_id"], [])
                price = pick_price_with_target(
                    book, trades, m["token_id"], 0.35, float(horizon_min), "risk"
                )
                if price is None:
                    continue
                st.session_state[f"price_{m['token_id']}"] = round(price * 100, 2)
            st.rerun()

        price_inputs = []
        for m in selected:
            book = orderbooks.get(m["token_id"], {})
            default_price = None
            try:
                default_price = float((book.get("asks") or [{}])[0].get("price")) * 100
            except (TypeError, ValueError):
                default_price = 50.0
            state_key = f"price_{m['token_id']}"
            if state_key in st.session_state:
                default_price = float(st.session_state[state_key])
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
                key=state_key,
            )
            price_inputs.append((m, float(price_cents) / 100.0))
            trades = trades_cache.get(m["condition_id"], [])
            p_est = estimate_fill_probability(
                book,
                trades,
                m["token_id"],
                float(price_cents) / 100.0,
                float(horizon_min),
            )
            st.caption(f"{t['prob_est']}: {p_est:.0%}")

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


