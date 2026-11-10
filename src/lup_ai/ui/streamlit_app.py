"""Interactive Streamlit dashboard consuming LUP AI FastAPI REST endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from lup_ai.ui.api_client import LupApiClient, LupApiError


def _get_client(api_url: str) -> LupApiClient:
    injected = st.session_state.get("lup_api_client")
    if injected is not None:
        return injected
    return LupApiClient(base_url=api_url)


def _render_overview_tab(client: LupApiClient, lookback_days: int) -> None:
    st.subheader("Panorama Multi-Dominio y Metas Activas")
    cross = client.get_catalog("cross_domain", days=lookback_days)
    agg = cross.get("aggregates", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Gasto Total", f"{agg.get('total_spend', 0):,.0f} COP")
    col2.metric("Ahorro Asignado", f"{agg.get('allocated_savings', 0):,.0f} COP")
    col3.metric("Sueno Promedio", f"{agg.get('avg_sleep_hours', 0)} h")
    col4.metric("Deep Work Total", f"{agg.get('total_deep_work_hours', 0)} h")

    goals = client.get_goals()
    if goals:
        st.markdown("#### Objetivos Activos")
        st.dataframe(pd.DataFrame(goals), use_container_width=True, hide_index=True)

    rows = cross.get("rows", [])
    if rows:
        df = pd.DataFrame(rows).sort_values("snapshot_date")
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df = df.set_index("snapshot_date")

        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("#### Flujo Financiero Diario (COP)")
            st.line_chart(df[["total_spend", "discretionary_spend", "allocated_savings"]])
        with c_right:
            st.markdown("#### Sueno, Energia y Foco (Horas / Puntaje)")
            st.line_chart(df[["sleep_hours", "energy_score", "deep_work_hours", "meeting_hours"]])


def _render_simulator_tab(client: LupApiClient) -> None:
    st.subheader("Simulador Determinista What-If")
    with st.form("what_if_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            horizon_days = st.slider("Horizonte (dias)", 14, 365, 90, step=7)
            disc_pct = st.slider("Cambio gasto discrecional (%)", -60, 30, -25, step=5)
        with c2:
            extra_sav = st.number_input("Ahorro semanal extra (COP)", 0, 2000000, 0, step=25000)
            use_sleep_target = st.checkbox("Definir meta de sueno", value=True)
            sleep_target = st.slider("Horas de sueno objetivo", 5.0, 9.5, 7.5, step=0.25)
        with c3:
            dw_delta = st.slider("Cambio semanal Deep Work (h)", -10.0, 15.0, 4.0, step=0.5)
            baseline_days = st.slider("Ventana historica base (dias)", 30, 365, 180, step=15)

        submitted = st.form_submit_button("Ejecutar Simulacion")

    if submitted or "last_simulation" not in st.session_state:
        scenario: dict[str, Any] = {
            "horizon_days": horizon_days,
            "discretionary_spend_delta_pct": disc_pct / 100.0,
            "extra_weekly_savings": float(extra_sav),
            "sleep_hours_target": sleep_target if use_sleep_target else None,
            "deep_work_delta_hours_per_week": dw_delta,
        }
        st.session_state["last_simulation"] = client.simulate(
            scenario=scenario, baseline_days=baseline_days
        )

    sim_resp = st.session_state["last_simulation"]
    sim = sim_resp["simulation"]

    for col in sim.get("constraint_collisions", []):
        if col["severity"] == "blocking":
            st.error(col["description"])
        else:
            st.warning(col["description"])

    traj_rows = list(sim.get("trajectories", {}).values())
    if traj_rows:
        st.markdown("#### Trayectorias Proyectadas (p25 / p50 / p75)")
        st.dataframe(pd.DataFrame(traj_rows), use_container_width=True, hide_index=True)

    goal_proj = sim.get("goal_projections", [])
    if goal_proj:
        st.markdown("#### Probabilidad de Cumplimiento de Metas")
        st.dataframe(pd.DataFrame(goal_proj), use_container_width=True, hide_index=True)

    for rec in sim_resp.get("recommendations", []):
        st.info(f"{rec['title']}: {rec['action']} | {rec['expected_impact']}")


def _render_trends_tab(client: LupApiClient, lookback_days: int) -> None:
    st.subheader("Tendencias y Correlaciones Cruzadas")
    data = client.get_trends(days=max(14, lookback_days))
    analysis = data["analysis"]

    trends = analysis.get("trends", [])
    if trends:
        st.dataframe(pd.DataFrame(trends), use_container_width=True, hide_index=True)

    corrs = analysis.get("correlations", [])
    if corrs:
        st.markdown("#### Correlaciones Spearman (Asociacion estadistica, no causalidad)")
        st.dataframe(pd.DataFrame(corrs), use_container_width=True, hide_index=True)


def _render_decisions_tab(client: LupApiClient, lookback_days: int) -> None:
    st.subheader("Anomalias, Oportunidades y Bandeja de Decisiones")
    anom_resp = client.get_anomalies(days=max(14, lookback_days))
    scan = anom_resp["scan"]

    c_a, c_b = st.columns(2)
    with c_a:
        st.markdown("#### Anomalias Detectadas")
        if scan.get("anomalies"):
            st.dataframe(pd.DataFrame(scan["anomalies"]), use_container_width=True, hide_index=True)
        else:
            st.write("Sin anomalias en la ventana.")
    with c_b:
        st.markdown("#### Oportunidades Detectadas")
        if scan.get("opportunities"):
            st.dataframe(
                pd.DataFrame(scan["opportunities"]), use_container_width=True, hide_index=True
            )
        else:
            st.write("Sin oportunidades nuevas en la ventana.")

    st.markdown("#### Bandeja de Decisiones (Decision Outcome Loop)")
    decisions = client.get_decisions()
    if not decisions:
        st.write(
            "No hay decisiones registradas. Ejecuta un escaneo proactivo desde la barra lateral."
        )
    for dec in decisions[:10]:
        with st.expander(f"[{dec['user_action'].upper()}] {dec['title']} ({dec['domain']})"):
            st.write(f"ID: `{dec['decision_id']}` | Metrica: `{dec['metric_name']}`")
            if dec["user_action"] == "pending":
                note = st.text_input("Nota opcional", key=f"note_{dec['decision_id']}")
                b1, b2 = st.columns(2)
                if b1.button("Aceptar", key=f"acc_{dec['decision_id']}"):
                    client.send_decision_feedback(dec["decision_id"], "accepted", note or None)
                    st.rerun()
                if b2.button("Descartar", key=f"dis_{dec['decision_id']}"):
                    client.send_decision_feedback(dec["decision_id"], "dismissed", note or None)
                    st.rerun()

    outcomes_resp = client.get_outcomes()
    adherence = outcomes_resp["adherence_summary"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Decisiones Totales", adherence["total_decisions"])
    m2.metric("Tasa de Aceptacion", f"{adherence['acceptance_rate_pct']}%")
    m3.metric("Tasa de Mejora Real", f"{adherence['improvement_rate_pct']}%")
    if outcomes_resp.get("outcomes"):
        st.dataframe(
            pd.DataFrame(outcomes_resp["outcomes"]), use_container_width=True, hide_index=True
        )


def _render_ask_tab(client: LupApiClient) -> None:
    st.subheader("Consulta en Lenguaje Natural (masked_cloud)")
    if "ask_session_id" not in st.session_state:
        st.session_state["ask_session_id"] = "streamlit-session"

    question = st.text_input(
        "Pregunta o escenario What-If",
        placeholder="Ej: Simula que pasa si reduzco gastos de ocio 25% y duermo 7.5h en 4 meses",
    )
    if st.button("Consultar a LUP AI") and question.strip():
        resp = client.ask(question.strip(), session_id=st.session_state["ask_session_id"])
        st.session_state["last_ask_response"] = resp

    last_resp = st.session_state.get("last_ask_response")
    if last_resp:
        st.success(last_resp["answer"])
        for obs in last_resp.get("key_observations", []):
            st.write(f"- {obs}")
        ev = last_resp["evidence"]
        st.caption(
            f"Vista: {ev['source_view']} | Backend: {ev['backend']} | "
            f"Privacidad: {last_resp['privacy_mode']} ({last_resp['anonymizer_engine']}) | "
            f"Modelo: {last_resp['model_used']}"
        )


def _render_connectors_tab(client: LupApiClient) -> None:
    st.subheader("Fuentes Simuladas y Captura Incremental")
    st.write(
        "Sincroniza feeds externos simulados (Banco, Wearable de Sueno/Habitos y Calendario) "
        "a traves de la cola local SQLite con flush por lotes."
    )

    c1, c2 = st.columns(2)
    with c1:
        sync_days = st.slider("Dias a sincronizar desde fuentes simuladas", 1, 14, 3)
        sync_seed = st.number_input("Seed simulacion", 1, 9999, 101)
        auto_flush = st.checkbox("Ejecutar flush batch automaticamente", value=True)
        if st.button("Sincronizar Fuentes Simuladas"):
            report = client.sync_simulated_sources(
                days=sync_days, seed=int(sync_seed), auto_flush=auto_flush
            )
            st.success(
                f"Sincronizados {report['events_captured']} eventos ({report['by_domain']}). "
                f"Flush={report['flushed']}"
            )

    with c2:
        pending = client.get_pending_ingest()
        st.metric("Eventos Pendientes en Buffer SQLite", pending["pending_count"])
        if st.button("Ejecutar Flush Batch Manual"):
            flushed = client.flush_ingest()
            st.info(
                f"Flush completado hacia {flushed['target_backend']}: {flushed['flushed_count']}"
            )

    st.markdown("#### Captura Rapida Manual")
    with st.form("manual_capture_form"):
        domain = st.selectbox("Dominio", ["finance", "habits_sleep", "time_calendar"])
        amount_or_hours = st.number_input(
            "Monto (COP) / Horas sueno / Minutos bloque", 1.0, 5000000.0, 45000.0
        )
        label = st.text_input("Categoria / Tipo bloque", value="DELIVERY")
        submitted = st.form_submit_button("Guardar en Cola Local")
    if submitted:
        if domain == "finance":
            payload = {
                "amount": float(amount_or_hours),
                "category_raw": label,
                "direction": "expense",
            }
        elif domain == "habits_sleep":
            payload = {"sleep_hours": min(24.0, float(amount_or_hours)), "exercise_minutes": 30}
        else:
            payload = {
                "duration_minutes": int(min(1440, amount_or_hours)),
                "block_type": "DEEP_WORK",
            }
        cap = client.capture_event(domain=domain, payload=payload)
        st.success(f"Evento encolado: {cap['entry_id']} ({cap['domain']})")


def run_dashboard() -> None:
    """Render the LUP AI Streamlit dashboard."""
    st.set_page_config(page_title="LUP AI - Decision Intelligence", layout="wide")
    st.title("LUP AI - Personal Decision Intelligence")
    st.caption("Snowflake SEMANTIC + Motores Deterministas Python + Gemini 3.8 (masked_cloud)")

    default_url = os.getenv("LUP_API_URL", "http://127.0.0.1:8000")
    with st.sidebar:
        api_url = st.text_input("LUP AI API URL", value=default_url)
        lookback_days = st.selectbox("Ventana de analisis (dias)", [30, 60, 90, 180], index=2)
        client = _get_client(api_url)
        try:
            health = client.health()
            st.success(f"Backend: {health['data_backend']}")
            st.write(f"Privacidad: `{health['privacy_mode']}`")
            st.write(f"Snowflake: `{health['snowflake_configured']}`")
            st.write(f"Gemini 3.8: `{health['gemini_configured']}`")
        except LupApiError as exc:
            st.error(str(exc))
            return

        if st.button("Ejecutar Escaneo Proactivo"):
            scan_rep = client.run_proactive_scan(days=lookback_days)
            st.info(
                f"Anomalias: {scan_rep['anomalies_found']} | "
                f"Oportunidades: {scan_rep['opportunities_found']}"
            )

    tabs = st.tabs(
        [
            "Resumen y Metas",
            "Simulador What-If",
            "Tendencias",
            "Anomalias y Decisiones",
            "Consultar LUP AI",
            "Fuentes Simuladas",
        ]
    )
    with tabs[0]:
        _render_overview_tab(client, lookback_days)
    with tabs[1]:
        _render_simulator_tab(client)
    with tabs[2]:
        _render_trends_tab(client, lookback_days)
    with tabs[3]:
        _render_decisions_tab(client, lookback_days)
    with tabs[4]:
        _render_ask_tab(client)
    with tabs[5]:
        _render_connectors_tab(client)


def get_app_path() -> Path:
    """Return filesystem path of the Streamlit entrypoint for CLI launch."""
    return Path(__file__).resolve()


if __name__ == "__main__":
    run_dashboard()
