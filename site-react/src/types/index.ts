export interface MarketStateRow {
  date: string; // YYYY-MM-DD
  state: "WARMUP" | "NORMAL" | "DEFCON2" | "DEFCON1";
  score: number | null;
  trend: string;
  equity: number | null;
  action: string;
  triggers: string;
}

export interface PortfolioRow {
  date: string;
  gross: number | null;
  cash: number | null;
  weights: { asset: string; w: number }[];
}

export interface NasdaqRow {
  date: string;
  close: number;
}

export interface DashboardData {
  states: MarketStateRow[];
  portfolio: Map<string, PortfolioRow>;
  nasdaq: NasdaqRow[];
  minDate: string;
  maxDate: string;
}
