import { useState, useEffect } from "react";
import Papa from "papaparse";
import { addDays, format, parseISO } from "date-fns";
import { fetchCsv } from "@/lib/utils";
import type { DashboardData, MarketStateRow, NasdaqRow, PortfolioRow } from "@/types";

const URLS = {
  // Use absolute paths to ensure correct fetching regardless of current route
  states: "/data/market_states_daily.csv",
  nasdaq: "/data/nasdaq_dly_ixic_1d.csv",
  portfolio: "/data/portfolio_daily.csv",
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
      const [csvStates, csvNasdaq, csvPortfolio] = await Promise.allSettled([
        fetchCsv(URLS.states),
        fetchCsv(URLS.nasdaq),
        fetchCsv(URLS.portfolio),
      ]);

      let states: MarketStateRow[] = [];
      let nasdaq: NasdaqRow[] = [];
      const portfolio = new Map<string, PortfolioRow>();

      if (csvStates.status === "fulfilled") {
        const parsed = Papa.parse(csvStates.value, { header: true, skipEmptyLines: true });
        states = parsed.data.map(parseStateRow).filter((r) => r.date);
      }

      if (csvNasdaq.status === "fulfilled") {
        const parsed = Papa.parse(csvNasdaq.value, { header: true, skipEmptyLines: true });
        nasdaq = parsed.data
          .map((r: any) => ({ date: r.time, close: parseFloat(r.close) }))
          .filter((r) => r.date && !isNaN(r.close))
          .sort((a, b) => (a.date < b.date ? -1 : 1));
      }

      if (csvPortfolio.status === "fulfilled") {
        const parsed = Papa.parse(csvPortfolio.value, { header: true, skipEmptyLines: true });
        parsed.data.forEach((r: any) => {
          if (r.date) portfolio.set(r.date.trim(), parsePortfolioRow(r));
        });
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
