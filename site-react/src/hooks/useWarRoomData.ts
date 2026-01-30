import { useState, useEffect } from "react";
import * as Papa from "papaparse";
import { addDays, format, parseISO } from "date-fns";
import { fetchCsv } from "@/lib/utils";
import type { DashboardData, MarketStateRow, NasdaqRow, PortfolioRow, CyclesRow, FearEuphoriaRow, FearCalendarRow } from "@/types";

const URLS = {
  // Use absolute paths to ensure correct fetching regardless of current route
  states: "/data/market_states_daily.csv",
  nasdaq: "/data/nasdaq_dly_ixic_1d.csv",
  portfolio: "/data/portfolio_daily.csv",
  cycles: "/data/cycles_daily.csv",
  fearEuphoria: "/data/fear_euphoria_daily.csv",
  fearCalendar: "/data/fear_euphoria_calendar.csv",
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
function toBool(v: any): boolean {
  if (v === true) return true;
  const s = (v ?? "").toString().trim().toLowerCase();
  return s === "1" || s === "true" || s === "yes" || s === "y";
}

function parseCycleRow(row: any): CyclesRow {
  return {
    date: row.date || row.time || "",
    risk_multiplier: toNum(row.risk_multiplier),
    price_cycle_z: toNum(row.price_cycle_z),
    vol_z: toNum(row.vol_z),
    wave_7y: toNum(row.wave_7y),
    wave_7y_phase: toNum(row.wave_7y_phase),
    vol_wave_10y: toNum(row.vol_wave_10y),
    vol_wave_10y_phase: toNum(row.vol_wave_10y_phase),
  };
}

function parseFearEuphoriaRow(row: any): FearEuphoriaRow {
  return {
    date: row.date || row.time || "",
    months_until_fear: toNum(row.months_until_fear),
    months_until_euphoria: toNum(row.months_until_euphoria),
    confidence: toNum(row.confidence),
    fear_window_24m: toBool(row.fear_window_24m),
    fear_window_36m: toBool(row.fear_window_36m),
    euphoria_window_24m: toBool(row.euphoria_window_24m),
    euphoria_window_36m: toBool(row.euphoria_window_36m),
    fear_trigger: toBool(row.fear_trigger),
    euphoria_trigger: toBool(row.euphoria_trigger),
    fear_level: toNum(row.fear_level),
    euphoria_level: toNum(row.euphoria_level),
  };
}

function parseFearCalendarRow(row: any): FearCalendarRow {
  return {
    month: row.month || row.month_end || row.time || "",
    f24: toNum(row.f24),
    f36: toNum(row.f36),
    e24: toNum(row.e24),
    e36: toNum(row.e36),
    as_of: row.as_of || "",
    fear_peak: row.fear_peak || "",
    euph_trough: row.euph_trough || "",
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
      const [csvStates, csvNasdaq, csvPortfolio, csvCycles, csvFearEuph, csvFearCal] = await Promise.allSettled([
        fetchCsv(URLS.states),
        fetchCsv(URLS.nasdaq),
        fetchCsv(URLS.portfolio),
        fetchCsv(URLS.cycles),
        fetchCsv(URLS.fearEuphoria),
        fetchCsv(URLS.fearCalendar),
      ]);

      let states: MarketStateRow[] = [];
      let nasdaq: NasdaqRow[] = [];
      let cycles: CyclesRow[] | undefined = undefined;
      let fearEuphoria: FearEuphoriaRow[] | undefined = undefined;
      let fearCalendar: FearCalendarRow[] | undefined = undefined;
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
      if (csvCycles.status === "fulfilled") {
        const parsed = Papa.parse(csvCycles.value, { header: true, skipEmptyLines: true });
        cycles = (parsed.data as any[]).map(parseCycleRow).filter((r) => r.date).sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      if (csvFearEuph.status === "fulfilled") {
        const parsed = Papa.parse(csvFearEuph.value, { header: true, skipEmptyLines: true });
        fearEuphoria = (parsed.data as any[]).map(parseFearEuphoriaRow).filter((r) => r.date).sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      if (csvFearCal.status === "fulfilled") {
        const parsed = Papa.parse(csvFearCal.value, { header: true, skipEmptyLines: true });
        fearCalendar = (parsed.data as any[]).map(parseFearCalendarRow).filter((r) => r.month);
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
        cycles,
        fearEuphoria,
        fearCalendar,
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
