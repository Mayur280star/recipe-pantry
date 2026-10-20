"""Streamlit UI (docs/08 §6). Talks ONLY to the FastAPI service.

    API_URL=http://localhost:8000 streamlit run ui/app.py
"""

from __future__ import annotations

import string

import streamlit as st

from ui.api_client import API_URL, get

st.set_page_config(page_title="Recipe Platform", page_icon="🍳", layout="wide")

DIETS = ["vegan", "vegetarian", "dairy_free", "egg_free", "gluten_free", "nut_free", "low_sodium"]
UNITS = ["", "teaspoon", "tablespoon", "cup", "fluid_ounce", "milliliter", "liter", "gram", "kilogram", "ounce",
         "pound", "pinch", "clove", "slice", "piece", "can", "stick", "item"]
STATUS_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skipped": "➖", "info": "ℹ️",
               "up": "✅", "down": "❌", "degraded": "⚠️"}
PAGE = 10


# ---------------------------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def vocabulary() -> list[str]:
    """All canonical ingredient names, most common first (built from API-06 prefix queries)."""
    seen: dict[str, int] = {}
    for letter in string.ascii_lowercase:
        data, err = get("/api/v1/ingredients/suggest", {"q": letter, "limit": 50})
        if err:
            return []
        for s in data["suggestions"]:
            seen[s["canonical_name"]] = s["recipe_count"]
    return sorted(seen, key=lambda n: (-seen[n], n))


@st.cache_data(ttl=60, show_spinner=False)
def cuisines() -> list[dict]:
    data, err = get("/api/v1/cuisines")
    return [] if err else data["cuisines"]


@st.cache_data(ttl=60, show_spinner=False)
def detail(recipe_id: str) -> tuple[dict | None, str | None]:
    return get(f"/api/v1/recipes/{recipe_id}")


def fmt_minutes(r: dict) -> str:
    if r.get("prep_time_min") is not None:
        return f"prep {r['prep_time_min']} min · total {r.get('total_time_min') or '?'} min"
    return f"{r['total_time_min']} min total" if r.get("total_time_min") is not None else "time unknown"


def title(name: str) -> str:
    return name[:1].upper() + name[1:]


# ---------------------------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------------------------
@st.dialog("Substitutes", width="large")
def substitution_drawer(ingredient: str, pantry: list[str]) -> None:
    st.markdown(f"#### Missing: **{ingredient.replace('_', ' ')}**")
    context = st.segmented_control("Used for", ["any", "cooking", "baking", "raw"], default="any",
                                   key="dlg_ctx")
    diet = st.multiselect("Must be", DIETS, key="dlg_diet")
    with st.spinner("Looking up rules…"):
        data, err = get("/api/v1/substitute", {"ingredient": ingredient.replace("_", " "), "pantry": pantry,
                                               "diet": diet, "context": None if context == "any" else context})
    if err:
        st.error(err)
        return
    render_substitutes(data)


def render_substitutes(data: dict) -> None:
    subs = data["substitutes"]
    if not subs:
        st.info("No known substitute." if data.get("reason") != "unknown_ingredient"
                else "We don't recognise that ingredient yet.")
        return
    for s in subs:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            badge = " 🟢 **in your pantry**" if s["in_pantry"] else ""
            left.markdown(f"**{title(s['substitute_name'])}**{badge}")
            if s["ratio_basis"] == "same_unit":
                amount = s.get("suggested_amount")
                ratio = f"{s['ratio']:g} : 1" + (f" → use **{amount['amount']:g} {amount['unit']}**" if amount else "")
                left.markdown(f"Ratio {ratio}")
            left.caption(s["notes"])
            label = s["confidence_label"]
            right.markdown(f"{'⭐' * s['confidence']}  \n*{label}*")
            right.caption(f"context: {s['context']}")
            if s["diet_tags"]:
                left.markdown(" ".join(f"`{t}`" for t in s["diet_tags"]))
    st.caption(f"ℹ️ {data['disclaimer']}  ·  rules {data['rule_version']}")


def render_detail(recipe_id: str) -> None:
    d, err = detail(recipe_id)
    if err:
        st.warning(err)
        return
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown("**Ingredients**")
        for i in d["ingredients"]:
            qty = f"{i['quantity']:g} " if i["quantity"] is not None else ""
            unit = "" if i["unit"] == "item" else f"{i['unit']} "
            opt = " *(optional)*" if i["is_optional"] else ""
            st.markdown(f"- {qty}{unit}{i['ingredient_name']}{opt}")
        if d["utensils"]:
            st.markdown("**Utensils**  \n" + " · ".join(d["utensils"]))
    with c2:
        st.markdown("**Steps**")
        for s in d["steps"]:
            st.markdown(f"{s['step_no']}. {s['step_text']}")


def pager(key: str, total: int | None, has_next: bool) -> None:
    off = st.session_state.get(key, 0)
    a, b, c = st.columns([1, 2, 1])
    if a.button("← Previous", key=f"{key}_prev", disabled=off == 0):
        st.session_state[key] = max(0, off - PAGE)
        st.rerun()
    shown = f"{off + 1}–{off + PAGE}" + (f" of {total}" if total is not None else "")
    b.markdown(f"<div style='text-align:center'>{shown}</div>", unsafe_allow_html=True)
    if c.button("Next →", key=f"{key}_next", disabled=not has_next):
        st.session_state[key] = off + PAGE
        st.rerun()


# ---------------------------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------------------------
def pantry_tab() -> None:
    vocab = vocabulary()
    with st.form("pantry"):
        pantry = st.multiselect("What's in your pantry?", vocab, default=[v for v in ("garlic", "butter", "spaghetti")
                                                                         if v in vocab],
                                accept_new_options=True, max_selections=30,
                                placeholder="type an ingredient…")
        f1, f2, f3, f4 = st.columns(4)
        max_prep = f1.number_input("Max time (min)", 0, 1440, 0, step=5, help="0 = no limit")
        max_cal = f2.number_input("Max calories", 0, 10000, 0, step=50, help="0 = no limit")
        cuisine_opts = ["any"] + [c["cuisine"] for c in cuisines() if not c["is_unspecified"]]
        cuisine = f3.selectbox("Cuisine", cuisine_opts)
        min_pct = f4.slider("Min match %", 0, 100, 0, step=10)
        staples = st.toggle("Assume salt, water & black pepper are available", value=True)
        submitted = st.form_submit_button("Find recipes", type="primary")
    if submitted:
        if not pantry:
            st.warning("Add at least one ingredient.")
            return
        st.session_state.pantry_q = {"ingredients": pantry, "max_prep_time": max_prep or None,
                                     "max_calories": max_cal or None,
                                     "cuisine": None if cuisine == "any" else cuisine,
                                     "min_match_pct": min_pct or None, "assume_staples": str(staples).lower()}
        st.session_state.pantry_off = 0
    q = st.session_state.get("pantry_q")
    if not q:
        st.info("Pick what you have and press **Find recipes**.")
        return
    with st.spinner("Searching…"):
        data, err = get("/api/v1/pantry-search", {**q, "limit": PAGE, "offset": st.session_state.pantry_off})
    if err:
        st.error(err)
        st.button("Retry", key="pantry_retry")
        return
    echo = data["pantry"]
    if echo["unrecognised"]:
        st.warning("Not recognised (try another spelling): " + ", ".join(echo["unrecognised"]))
    st.caption(f"Matching on: {', '.join(r['ingredient_id'].replace('_', ' ') for r in echo['resolved'])}"
               + (f"  ·  staples assumed: {', '.join(s.replace('_', ' ') for s in echo['staples_assumed'])}"
                  if echo["staples_assumed"] else ""))
    if not data["recipes"]:
        st.info("No recipes match. Try adding more ingredients or relaxing the filters.")
        return
    st.markdown(f"**{data['total_matches']} recipes** can use your pantry")
    pantry_ids = [r["ingredient_id"] for r in echo["resolved"]]
    for r in data["recipes"]:
        with st.container(border=True):
            head, meta = st.columns([3, 2])
            head.markdown(f"### {title(r['recipe_name'])}")
            meta.caption(f"🍽 {r['cuisine'] or '—'} · ⏱ {fmt_minutes(r)} · 🔥 "
                         f"{r['calories'] if r['calories'] is not None else '?'} kcal · "
                         f"{r['ingredient_count']} ingredients")
            st.progress(r["match_percentage"] / 100, text=f"**{r['match_percentage']:g}% match**")
            st.markdown(" ".join(f"✅ {m.replace('_', ' ')}" for m in r["matched_ingredients"]))
            if r["missing_ingredients"]:
                st.caption("Missing — click one for substitutes:")
                cols = st.columns(min(len(r["missing_ingredients"]), 6))
                for i, m in enumerate(r["missing_ingredients"]):
                    if cols[i % len(cols)].button(f"✗ {m.replace('_', ' ')}", key=f"miss_{r['recipe_id']}_{m}"):
                        substitution_drawer(m, pantry_ids)
            if st.toggle("Show recipe", key=f"show_{r['recipe_id']}"):
                render_detail(r["recipe_id"])
    total = data["total_matches"]
    pager("pantry_off", total, st.session_state.pantry_off + PAGE < total)


def cuisine_tab() -> None:
    cs = cuisines()
    if not cs:
        st.error("Cuisine list unavailable — is the API running?")
        return
    labels = {c["cuisine"]: f"{c['cuisine'].replace('_', ' ').title()} ({c['recipe_count']})" for c in cs}
    c1, c2, c3 = st.columns([2, 1, 1])
    cuisine = c1.selectbox("Cuisine", list(labels), format_func=labels.get)
    max_prep = c2.number_input("Max time (min)", 0, 1440, 0, step=5, key="cz_prep", help="0 = no limit")
    max_cal = c3.number_input("Max calories", 0, 10000, 0, step=50, key="cz_cal", help="0 = no limit")
    key = (cuisine, max_prep, max_cal)
    if st.session_state.get("cz_key") != key:
        st.session_state.cz_key, st.session_state.cz_off = key, 0
    data, err = get("/api/v1/cuisine-search", {"cuisine": cuisine, "max_prep_time": max_prep or None,
                                               "max_calories": max_cal or None, "limit": PAGE,
                                               "offset": st.session_state.cz_off})
    if err:
        st.error(err)
        return
    if not data["recipes"]:
        st.info("No recipes for these filters.")
        return
    for r in data["recipes"]:
        with st.expander(f"**{title(r['recipe_name'])}** — ⏱ {fmt_minutes(r)} · 🔥 "
                         f"{r['calories'] if r['calories'] is not None else '?'} kcal"):
            render_detail(r["recipe_id"])
    pager("cz_off", None, data["next_offset"] is not None)


def substitution_tab() -> None:
    with st.form("subs"):
        c1, c2, c3 = st.columns([2, 2, 1])
        ingredient = c1.text_input("Ingredient you are missing", "parmesan")
        diet = c2.multiselect("Substitute must be", DIETS)
        context = c3.selectbox("Used for", ["any", "cooking", "baking", "raw"])
        a1, a2, _ = st.columns([1, 1, 2])
        amount = a1.number_input("Amount (optional)", 0.0, 10000.0, 0.0, step=0.25)
        unit = a2.selectbox("Unit", UNITS)
        go = st.form_submit_button("Find substitutes", type="primary")
    if go:
        with_amount = amount > 0 and unit
        data, err = get("/api/v1/substitute", {"ingredient": ingredient, "diet": diet,
                                               "context": None if context == "any" else context,
                                               "amount": amount if with_amount else None,
                                               "unit": unit if with_amount else None})
        if err:
            st.error(err)
        else:
            ing = data["ingredient"]
            st.caption(f"Recognised as **{ing['canonical_name'] or '—'}** (`{ing['ingredient_id'] or 'unknown'}`)")
            render_substitutes(data)


def system_tab() -> None:
    ready, err = get("/health/ready")
    if err:
        st.error(err)
        return
    versions, _ = get("/health/versions")
    summary, s_err = get("/api/v1/system/summary")
    cols = st.columns(4)
    cols[0].metric("API status", f"{STATUS_ICON.get(ready['status'], '')} {ready['status']}")
    for col, (name, state) in zip(cols[1:], ready["components"].items(), strict=False):
        col.metric(name, f"{STATUS_ICON.get(state, '')} {state}")
    if versions:
        st.markdown(f"**Live snapshot** `{versions['sql_run_id']}` · search index `{versions['search_run_id']}` · "
                    f"aligned {'✅' if versions['aligned'] else '❌'} · rules `{versions['rule_version']}` · "
                    f"core `{versions['core_version']}` · profile `{ready['profile']}`")
    if s_err or not summary:
        st.warning(s_err or "no summary")
        return
    run, tables = summary["run"], summary["tables"]
    m = st.columns(5)
    m[0].metric("Input rows", f"{run.get('input_rows', 0):,}")
    m[1].metric("Quarantined", f"{run.get('quarantined', 0):,}")
    m[2].metric("Recipes served", f"{tables.get('recipes', 0):,}")
    m[3].metric("Ingredient lines", f"{tables.get('recipe_ingredients', 0):,}")
    m[4].metric("ETL duration", f"{run.get('duration_s', 0)} s")
    st.markdown("**Data-quality gates (this snapshot)**")
    st.dataframe([{"gate": g["gate_id"], "check": g["gate_name"],
                   "status": f"{STATUS_ICON.get(g['status'], '')} {g['status']}", "value": g["value"],
                   "detail": g["detail"]} for g in summary["gates"]], hide_index=True, width="stretch")
    st.caption(f"Previous snapshot: {run.get('previous_run_id') or '—'} · rollback: `python -m etl.run rollback`")
    if ready["profile"] == "standard":
        st.markdown("**Cluster UIs**: [HDFS NameNode](http://localhost:9870) · [Trino](http://localhost:8080) · "
                    "[Solr](http://localhost:8983/solr)")


# ---------------------------------------------------------------------------------------------
st.title("🍳 Recipe Platform")
st.caption("Pantry match · cuisine explorer · rule-based substitutions — Spark · Parquet · Solr/Trino · FastAPI")
with st.sidebar:
    st.markdown("### Backend")
    st.code(API_URL, language=None)
    health, herr = get("/health/ready")
    st.markdown(f"Status: {STATUS_ICON.get(health['status'], '') + ' ' + health['status'] if health else '❌ offline'}")
    if st.button("Refresh data caches"):
        st.cache_data.clear()
        st.rerun()

t1, t2, t3, t4 = st.tabs(["🥘 Pantry match", "🌍 Cuisine explorer", "🔁 Substitutions", "⚙️ System"])
with t1:
    pantry_tab()
with t2:
    cuisine_tab()
with t3:
    substitution_tab()
with t4:
    system_tab()
