import { useState, useEffect } from "react";
import * as Papa from "papaparse";
import { addDays, format, parseISO } from "date-fns";
import { fetchCsv } from "@/lib/utils";
import type { DashboardData, MarketStateRow, NasdaqRow, PortfolioRow, ForecastV1Row, TimingV1Row } from "@/types";

const URLS = {
  // Use absolute paths to ensure correct fetching regardless of current route
  states: "/data/market_states_daily.csv",
  nasdaq: "/data/nasdaq_dly_ixic_1d.csv",
  portfolio: "/data/portfolio_daily.csv",
  forecastV1: "/data/forecast_v1_daily.csv",
  timingV1: "/data/timing_v1_daily.csv",
};

function parseStateRow(row: any): MarketStateRow {
  return {
    date: row.as_of_date || "",
    state: (row.state as any) || "WARMUP",
    score: row.score ? parseFloat(row.score) : null,
    trend: row.trend_signal || "",
    equity: row.equity_weight ? parseFloat(row.equity_weight) : null,
    action: row.action || "",
    triggers: row.triggers || "",
  };
}


function toNum(v: any): number | null {
  const n = v === "" || v === null || v === undefined ? NaN : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function parseForecastV1Row(row: any): ForecastV1Row {
  return {
    date: row.date || "",
    model: row.model || "forecast_v1",
    status: row.status,
    p_crisis_1y: toNum(row.p_crisis_1y),
    p_crisis_2y: toNum(row.p_crisis_2y),
    p_crisis_3y: toNum(row.p_crisis_3y),
    p_euphoria_1y: toNum(row.p_euphoria_1y),
    p_euphoria_2y: toNum(row.p_euphoria_2y),
    p_euphoria_3y: toNum(row.p_euphoria_3y),
    net_1y: toNum(row.net_1y),
    net_2y: toNum(row.net_2y),
    net_3y: toNum(row.net_3y),
    conf_crisis_1y: toNum(row.conf_crisis_1y),
    conf_crisis_2y: toNum(row.conf_crisis_2y),
    conf_crisis_3y: toNum(row.conf_crisis_3y),
    conf_euphoria_1y: toNum(row.conf_euphoria_1y),
    conf_euphoria_2y: toNum(row.conf_euphoria_2y),
    conf_euphoria_3y: toNum(row.conf_euphoria_3y),
  };
}

function parseTimingV1Row(row: any): TimingV1Row {
  return {
    date: row.date || "",
    model: row.model || "timing_v1",
    status_crisis: row.status_crisis,
    status_euphoria: row.status_euphoria,

    p_crisis_1m: toNum(row.p_crisis_1m),
    p_crisis_3m: toNum(row.p_crisis_3m),
    p_crisis_6m: toNum(row.p_crisis_6m),
    p_crisis_1y: toNum(row.p_crisis_1y),
    p_crisis_2y: toNum(row.p_crisis_2y),
    p_crisis_3y: toNum(row.p_crisis_3y),
    p_crisis_5y: toNum(row.p_crisis_5y),
    p_crisis_10y: toNum(row.p_crisis_10y),

    p_euphoria_1w: toNum(row.p_euphoria_1w),
    p_euphoria_1m: toNum(row.p_euphoria_1m),
    p_euphoria_3m: toNum(row.p_euphoria_3m),
    p_euphoria_6m: toNum(row.p_euphoria_6m),
    p_euphoria_1y: toNum(row.p_euphoria_1y),
    p_euphoria_2y: toNum(row.p_euphoria_2y),

    eta_crisis_median_days: toNum(row.eta_crisis_median_days),
    eta_crisis_median_date: row.eta_crisis_median_date || null,
    crisis_mode_start: row.crisis_mode_start || null,
    crisis_mode_end: row.crisis_mode_end || null,

    eta_euphoria_median_days: toNum(row.eta_euphoria_median_days),
    eta_euphoria_median_date: row.eta_euphoria_median_date || null,
    euphoria_mode_start: row.euphoria_mode_start || null,
    euphoria_mode_end: row.euphoria_mode_end || null,
  };
}

function parsePortfolioRow(row: any): PortfolioRow {
  const weights = [];
  for (const k of Object.keys(row)) {
    if (k.startsWith("w_")) {
      const val = parseFloat(row[k]);
      if (val > 0) weights.push({ asset: k.substring(2), w: val });
    }
  }
  weights.sort((a, b) => b.w - a.w);
  return {
    date: row.date || "",
    gross: row.gross_exposure ? parseFloat(row.gross_exposure) : null,
    cash: row.cash_weight ? parseFloat(row.cash_weight) : null,
    weights,
  };
}

export function useWarRoomData() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [csvStates, csvNasdaq, csvPortfolio, csvForecast, csvTiming] = await Promise.allSettled([
        fetchCsv(URLS.states),
        fetchCsv(URLS.nasdaq),
        fetchCsv(URLS.portfolio),
        fetchCsv(URLS.forecastV1),
        fetchCsv(URLS.timingV1),
      ]);

      let states: MarketStateRow[] = [];
      let nasdaq: NasdaqRow[] = [];
      let forecastV1: ForecastV1Row[] | undefined = undefined;
      let timingV1: TimingV1Row[] | undefined = undefined;
      const portfolio = new Map<string, PortfolioRow>();

      if (csvStates.status === "fulfilled") {
        const parsed = Papa.parse(csvStates.value, { header: true, skipEmptyLines: true });
        states = parsed.data.map(parseStateRow).filter((r) => r.date);
      }

      if (csvNasdaq.status === "fulfilled") {
        const parsed = Papa.parse(csvNasdaq.value, { header: true, skipEmptyLines: true });
        nasdaq = parsed.data
          .map((r: any) => {
            const rawClose = r.close;
            const close = rawClose === "" || rawClose === null || rawClose === undefined ? null : parseFloat(rawClose);
            const cleanClose = typeof close === "number" && Number.isFinite(close) ? close : null;
            return { date: r.time, close: cleanClose };
          })
          .filter((r) => r.date)
          .sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      if (csvPortfolio.status === "fulfilled") {
        const parsed = Papa.parse(csvPortfolio.value, { header: true, skipEmptyLines: true });
        parsed.data.forEach((r: any) => {
          if (r.date) portfolio.set(r.date.trim(), parsePortfolioRow(r));
        });
      }
      if (csvForecast.status === "fulfilled") {
        const parsed = Papa.parse(csvForecast.value, { header: true, skipEmptyLines: true });
        forecastV1 = (parsed.data as any[])
          .map(parseForecastV1Row)
          .filter((r) => r.date)
          .sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      if (csvTiming.status === "fulfilled") {
        const parsed = Papa.parse(csvTiming.value, { header: true, skipEmptyLines: true });
        timingV1 = (parsed.data as any[])
          .map(parseTimingV1Row)
          .filter((r) => r.date)
          .sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      // Determine date range
      let minDate = "2020-01-01";
      let maxDate = format(new Date(), "yyyy-MM-dd");

      if (states.length > 0) {
        minDate = states[0].date;
        maxDate = states[states.length - 1].date;
      }
      if (nasdaq.length > 0) {
        if (nasdaq[0].date < minDate) minDate = nasdaq[0].date;
        // Don't limit maxDate by NASDAQ, as it might lag on weekends
      }

      // Force extend maxDate by 7 days to allow viewing "future" or "lagged" days
      const extendedMax = format(addDays(parseISO(maxDate), 7), "yyyy-MM-dd");

      setData({
        states: states.sort((a, b) => (a.date < b.date ? -1 : 1)),
        nasdaq,
        portfolio,
        forecastV1,
        timingV1,
        minDate,
        maxDate: extendedMax,
      });
    } catch (err: any) {
      setError(err.message || "Failed to load data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return { data, loading, error, reload: load };
}
