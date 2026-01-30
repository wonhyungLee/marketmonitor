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


export interface CyclesRow {
  date: string; // YYYY-MM-DD
  risk_multiplier: number | null;
  price_cycle_z: number | null;
  vol_z: number | null;
  wave_7y: number | null;
  wave_7y_phase: number | null;
  vol_wave_10y: number | null;
  vol_wave_10y_phase: number | null;
}

export interface FearEuphoriaRow {
  date: string; // YYYY-MM-DD
  months_until_fear: number | null;
  months_until_euphoria: number | null;
  confidence: number | null;
  fear_window_24m?: boolean;
  fear_window_36m?: boolean;
  euphoria_window_24m?: boolean;
  euphoria_window_36m?: boolean;
  fear_trigger: boolean;
  euphoria_trigger: boolean;
  fear_level: number | null;
  euphoria_level: number | null;
}

export interface FearCalendarRow {
  month: string; // YYYY-MM (or month-end date)
  f24?: number | null;
  f36?: number | null;
  e24?: number | null;
  e36?: number | null;
  as_of?: string;
  fear_peak?: string;
  euph_trough?: string;
}

export interface DashboardData {
  states: MarketStateRow[];
  portfolio: Map<string, PortfolioRow>;
  nasdaq: NasdaqRow[];
  cycles?: CyclesRow[];
  fearEuphoria?: FearEuphoriaRow[];
  fearCalendar?: FearCalendarRow[];
  minDate: string;
  maxDate: string;
}
