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
  close: number | null;
}

export interface ForecastV1Row {
  date: string; // YYYY-MM-DD
  model: string;
  status?: string;

  p_crisis_1y: number | null;
  p_crisis_2y: number | null;
  p_crisis_3y: number | null;

  p_euphoria_1y: number | null;
  p_euphoria_2y: number | null;
  p_euphoria_3y: number | null;

  net_1y: number | null;
  net_2y: number | null;
  net_3y: number | null;

  conf_crisis_1y: number | null;
  conf_crisis_2y: number | null;
  conf_crisis_3y: number | null;

  conf_euphoria_1y: number | null;
  conf_euphoria_2y: number | null;
  conf_euphoria_3y: number | null;
}

export interface TimingV1Row {
  date: string; // YYYY-MM-DD
  model?: string;

  // Status
  status_crisis?: string;
  status_euphoria?: string;

  // Cumulative probabilities: "event begins within X"
  p_crisis_1m: number | null;
  p_crisis_3m: number | null;
  p_crisis_6m: number | null;
  p_crisis_1y: number | null;
  p_crisis_2y: number | null;
  p_crisis_3y: number | null;
  p_crisis_5y: number | null;
  p_crisis_10y: number | null;

  p_euphoria_1w: number | null;
  p_euphoria_1m: number | null;
  p_euphoria_3m: number | null;
  p_euphoria_6m: number | null;
  p_euphoria_1y: number | null;
  p_euphoria_2y: number | null;

  // ETA + mode window
  eta_crisis_median_days: number | null;
  eta_crisis_median_date?: string | null;
  crisis_mode_start?: string | null;
  crisis_mode_end?: string | null;

  eta_euphoria_median_days: number | null;
  eta_euphoria_median_date?: string | null;
  euphoria_mode_start?: string | null;
  euphoria_mode_end?: string | null;
}

export interface DashboardData {
  states: MarketStateRow[];
  portfolio: Map<string, PortfolioRow>;
  nasdaq: NasdaqRow[];
  forecastV1?: ForecastV1Row[];
  timingV1?: TimingV1Row[];
  minDate: string;
  maxDate: string;
}
