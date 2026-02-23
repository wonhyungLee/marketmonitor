import { useState, useMemo, useEffect, useRef } from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceArea,
  Cell,
  Brush,
} from "recharts";
import { format, parseISO, subYears, endOfWeek, endOfMonth } from "date-fns";
import { useWarRoomData } from "@/hooks/useWarRoomData";
import { cn } from "@/lib/utils";
import { RefreshCw, Search, Filter, Maximize2, X, RotateCcw, Languages, ChevronDown, ChevronUp } from "lucide-react";
import CycleRibbon from "@/components/CycleRibbon";
import CoupangTraderComboInline from "@/components/CoupangTraderComboInline";
import type { MarketStateRow } from "@/types";

const STATE_COLORS = {
  WARMUP: "#3498db",
  NORMAL: "#2ecc71",
  DEFCON2: "#e67e22",
  DEFCON1: "#e74c3c",
};

const STATE_ORDER = ["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"];

type Period = "day" | "week" | "month";
type Lang = "en" | "ko";

const US_HOLIDAY_CACHE = new Map<number, Set<string>>();

function observedFixedHoliday(year: number, month: number, day: number): string {
  const d = new Date(year, month - 1, day);
  const dow = d.getDay();
  if (dow === 6) d.setDate(d.getDate() - 1); // Saturday -> Friday observed
  else if (dow === 0) d.setDate(d.getDate() + 1); // Sunday -> Monday observed
  return format(d, "yyyy-MM-dd");
}

function nthWeekdayOfMonth(year: number, month: number, weekday: number, nth: number): string {
  const first = new Date(year, month - 1, 1);
  const offset = (weekday - first.getDay() + 7) % 7;
  const day = 1 + offset + (nth - 1) * 7;
  return format(new Date(year, month - 1, day), "yyyy-MM-dd");
}

function lastWeekdayOfMonth(year: number, month: number, weekday: number): string {
  const last = new Date(year, month, 0);
  const offset = (last.getDay() - weekday + 7) % 7;
  last.setDate(last.getDate() - offset);
  return format(last, "yyyy-MM-dd");
}

function easterSunday(year: number): Date {
  // Anonymous Gregorian algorithm
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(year, month - 1, day);
}

function usMarketHolidaySet(year: number): Set<string> {
  const cached = US_HOLIDAY_CACHE.get(year);
  if (cached) return cached;

  const set = new Set<string>();
  set.add(observedFixedHoliday(year, 1, 1)); // New Year
  set.add(nthWeekdayOfMonth(year, 1, 1, 3)); // MLK Day
  set.add(nthWeekdayOfMonth(year, 2, 1, 3)); // Presidents' Day
  const gf = easterSunday(year);
  gf.setDate(gf.getDate() - 2); // Good Friday
  set.add(format(gf, "yyyy-MM-dd"));
  set.add(lastWeekdayOfMonth(year, 5, 1)); // Memorial Day
  set.add(observedFixedHoliday(year, 6, 19)); // Juneteenth
  set.add(observedFixedHoliday(year, 7, 4)); // Independence Day
  set.add(nthWeekdayOfMonth(year, 9, 1, 1)); // Labor Day
  set.add(nthWeekdayOfMonth(year, 11, 4, 4)); // Thanksgiving
  set.add(observedFixedHoliday(year, 12, 25)); // Christmas

  US_HOLIDAY_CACHE.set(year, set);
  return set;
}

function isUsMarketHoliday(dateStr: string): boolean {
  const d = parseISO(dateStr);
  if (Number.isNaN(d.getTime())) return false;
  const y = d.getFullYear();
  return (
    usMarketHolidaySet(y - 1).has(dateStr) ||
    usMarketHolidaySet(y).has(dateStr) ||
    usMarketHolidaySet(y + 1).has(dateStr)
  );
}

function isUsTradingDay(dateStr: string): boolean {
  const d = parseISO(dateStr);
  if (Number.isNaN(d.getTime())) return false;
  const dow = d.getDay();
  if (dow === 0 || dow === 6) return false;
  return !isUsMarketHoliday(dateStr);
}

const TRANSLATIONS = {
  en: {
    headerTitle: "NASDAQ + Market State",
    headerSub: "Visualizing regime changes and portfolio allocations",
    reload: "Reload",
    chartTitle: "Chart",
    chartSub: "Line: NASDAQ Close | Background: Market State",
    rangeStart: "Range Start",
    rangeEnd: "Range End",
    reset: "Reset",
    onlyTradingDays: "Only trading days",
    dailyRecords: "Daily Records",
    searchPlaceholder: "Filter triggers...",
    searchPortfolioPlaceholder: "Filter portfolio...",
    stateMix: "State Mix",
    portfolioDetail: "Portfolio Detail",
    portfolioText: "Portfolio Text",
    grossExposure: "Gross",
    cash: "Cash",
    allocations: "ALLOCATIONS",
    noAssets: "Cash 100% (No Assets)",
    noPortfolioData: "No portfolio data available for this date.",
    copy: "Copy",
    copied: "Copied",
    clickHint: "Click a row in the table or a point on the chart to view details.",
    noRecords: "No records found matching your filters.",
    colDate: "Date",
    colState: "State",
    colScore: "Score",
    colTrend: "Trend",
    colEquity: "Equity",
    colAction: "Action",
    colTriggers: "Triggers",
    detailedAnalysis: "Detailed Analysis",
    marketState: "Market State",
    nasdaqClose: "NASDAQ Close",
    portfolio: "Portfolio",
    from: "from",
    forecastTitle: "Forecast (Risk-off & Overheat)",
    forecastSub: "For each date, the probability that the market will be in risk-off (crisis) or overheat (euphoria) H years later (not the same as Bull/Bear).",
    timingTitle: "Timing (When)",
    timingSub: "ETA/window & cumulative probabilities (not a guaranteed date; may not match Bull/Bear).",
    termsButton: "Terms",
    narrativeTitle: "Narrative",
    showNumbers: "Show numbers",
    hideNumbers: "Hide numbers",
    asOf: "As of",
    eta: "ETA (median)",
    modeWindow: "Likely window",
    withinProbTitle: "Within (cumulative)",
    within: "Within",
    horizon: "Horizon",
    crisis: "Risk-off",
    euphoria: "Overheat",
    net: "Net (Overheat - Risk-off)",
  },
  ko: {
    headerTitle: "나스닥 + 시장 상태 감지",
    headerSub: "시장 국면 변화 및 포트폴리오 배분 시각화",
    reload: "새로고침",
    chartTitle: "시장 차트",
    chartSub: "선: 나스닥 종가 | 배경: 시장 상태 (위험도)",
    rangeStart: "시작일",
    rangeEnd: "종료일",
    reset: "초기화",
    onlyTradingDays: "영업일만 보기",
    dailyRecords: "일별 기록",
    searchPlaceholder: "트리거 검색...",
    searchPortfolioPlaceholder: "포트폴리오 검색...",
    stateMix: "상태 분포",
    portfolioDetail: "포트폴리오 상세",
    portfolioText: "포트폴리오 텍스트",
    grossExposure: "총 노출",
    cash: "현금",
    allocations: "자산 배분",
    noAssets: "현금 100% (자산 없음)",
    noPortfolioData: "이 날짜의 포트폴리오 데이터가 없습니다.",
    copy: "복사",
    copied: "복사됨",
    clickHint: "테이블 행이나 차트를 클릭하여 상세 정보를 확인하세요.",
    noRecords: "조건에 맞는 데이터가 없습니다.",
    colDate: "날짜",
    colState: "상태",
    colScore: "점수",
    colTrend: "추세",
    colEquity: "주식비중",
    colAction: "액션",
    colTriggers: "상세 사유 (Triggers)",
    detailedAnalysis: "상세 분석",
    marketState: "시장 상태",
    nasdaqClose: "나스닥 종가",
    portfolio: "포트폴리오",
    from: "데이터 기준:",
    forecastTitle: "전망 (위기 & 환희)",
    forecastSub: "H년 뒤 시장이 위기(리스크오프) 또는 환희(과열) 상태일 확률",
    timingTitle: "시기 전망 (언제?)",
    timingSub: "위기/환희 시작 시기(윈도우) 및 누적확률",
    termsButton: "용어",
    narrativeTitle: "문장 해설",
    showNumbers: "수치 보기",
    hideNumbers: "수치 숨기기",
    asOf: "데이터 기준일",
    eta: "ETA(중앙값)",
    modeWindow: "유력 구간",
    withinProbTitle: "Within(기간 내) 누적확률",
    within: "이내",
    horizon: "기간",
    crisis: "위기",
    euphoria: "환희",
    net: "순확률(환희-위기)",
  },
};

const CustomDot = (props: any) => {
  const { cx, cy, payload, selectedDates } = props;
  if (!cx || !cy) return null;
  if (selectedDates && selectedDates.has(payload.date)) {
    return (
      <circle cx={cx} cy={cy} r={5} fill="black" stroke="white" strokeWidth={2} />
    );
  }
  return null;
};

export default function App() {
  const { data, loading, error, reload } = useWarRoomData();
  const [dateRange, setDateRange] = useState({ start: "", end: "" });
  const [searchTerm, setSearchTerm] = useState("");
  const [portfolioSearchTerm, setPortfolioSearchTerm] = useState("");
  const [portfolioCopied, setPortfolioCopied] = useState(false);
  const [showForecastChart, setShowForecastChart] = useState(false);
  
  // Multi-select State
  const [selectedDates, setSelectedDates] = useState<Set<string>>(new Set());
  const [lastSelectedDate, setLastSelectedDate] = useState<string | null>(null);

  const [isExpanded, setIsExpanded] = useState(false);
  const [period, setPeriod] = useState<Period>("day");
  const [viewBasis, setViewBasis] = useState<"1x" | "2x">("2x");
  const [isPanning, setIsPanning] = useState(false);
  const chartWrapRef = useRef<HTMLDivElement | null>(null);
  const lastPanAtRef = useRef(0);
  const dragRef = useRef({
    active: false,
    startX: 0,
    startStart: 0,
    startEnd: 0,
    moved: false,
  });

  const transformPortfolio = (p: any) => {
    if (!p || viewBasis === "2x") return p;
    return {
      ...p,
      gross: (p.gross || 0) / 2,
      cash: 1 - ((p.gross || 0) / 2),
      weights: p.weights.map((w: any) => ({ ...w, w: w.w / 2 })),
    };
  };

  const portfolioDates = useMemo(() => {
    if (!data) return [] as string[];
    return Array.from(data.portfolio.keys()).sort();
  }, [data]);

  const findPortfolioDate = (date: string) => {
    if (!portfolioDates.length) return null;
    if (date < portfolioDates[0]) return null;
    const lastIdx = portfolioDates.length - 1;
    if (date >= portfolioDates[lastIdx]) return portfolioDates[lastIdx];
    let lo = 0;
    let hi = lastIdx;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const v = portfolioDates[mid];
      if (v <= date) lo = mid + 1;
      else hi = mid - 1;
    }
    return hi >= 0 ? portfolioDates[hi] : null;
  };

  const resolvePortfolio = (date: string) => {
    if (!data) return { portfolio: null as any, displayDate: date };
    let port = data.portfolio.get(date);
    let displayDate = date;
    if (!port) {
      const fallbackDate = findPortfolioDate(date);
      if (fallbackDate) {
        port = data.portfolio.get(fallbackDate);
        displayDate = fallbackDate;
      }
    }
    return { portfolio: port || null, displayDate };
  };

  const portfolioSearchText = (p: any) => {
    const port = transformPortfolio(p);
    if (!port) return "";
    const parts = port.weights.map((w: any) => `${w.asset} ${(w.w * 100).toFixed(1)}%`);
    if (typeof port.cash === "number") parts.push(`CASH ${(port.cash * 100).toFixed(1)}%`);
    if (typeof port.gross === "number") parts.push(`GROSS ${port.gross.toFixed(2)}x`);
    return parts.join(" | ").toLowerCase();
  };

  const portfolioDisplayText = (p: any) => {
    const port = transformPortfolio(p);
    if (!port) return "";
    const parts = port.weights.map((w: any) => `${w.asset} ${(w.w * 100).toFixed(1)}%`);
    if (typeof port.cash === "number") parts.push(`CASH ${(port.cash * 100).toFixed(1)}%`);
    if (typeof port.gross === "number") parts.push(`GROSS ${port.gross.toFixed(2)}x`);
    return parts.join(" | ");
  };

  const copyPortfolioText = async (text: string) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setPortfolioCopied(true);
      window.setTimeout(() => setPortfolioCopied(false), 1500);
    } catch {
      // noop
    }
  };
  
  // Language State
  const [lang, setLang] = useState<Lang>(() => {
    return (localStorage.getItem("warroom_lang") as Lang) || "en";
  });

  const t = TRANSLATIONS[lang];

  const toggleLang = () => {
    const next = lang === "en" ? "ko" : "en";
    setLang(next);
    localStorage.setItem("warroom_lang", next);
  };
  
  // Filters
  const [enabledStates, setEnabledStates] = useState<Set<string>>(
    new Set(["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"])
  );
  const [onlyTradingDays, setOnlyTradingDays] = useState(true);
  
  const [forecastHorizon, setForecastHorizon] = useState<"1y" | "2y" | "3y">("1y");
  const [showDetailPopup, setShowDetailPopup] = useState(false);

  // Initialize date range
  useEffect(() => {
    if (data && !dateRange.start) {
      const end = data.maxDate;
      const start = format(subYears(parseISO(end), 1), "yyyy-MM-dd");
      setDateRange({ start, end });
    }
  }, [data]);

  // Toggle Date Selection
  const toggleDate = (date: string, shouldOpenPopup = true) => {
    const newSet = new Set(selectedDates);
    if (newSet.has(date)) {
      newSet.delete(date);
      if (lastSelectedDate === date) {
        setLastSelectedDate(newSet.size > 0 ? Array.from(newSet).pop()! : null);
      }
    } else {
      newSet.add(date);
      setLastSelectedDate(date);
      if (shouldOpenPopup) {
        setShowDetailPopup(true); // Open detail popup on selection
      }
      setTimeout(() => {
        const row = document.getElementById(`row-${date}`);
        if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 50);
    }
    setSelectedDates(newSet);
  };

  // Quick Range Handlers
  const setRange = (type: "1y" | "5y" | "10y" | "max") => {
    if (!data) return;
    const end = data.maxDate;
    let start = data.minDate;
    if (type === "1y") start = format(subYears(parseISO(end), 1), "yyyy-MM-dd");
    if (type === "5y") start = format(subYears(parseISO(end), 5), "yyyy-MM-dd");
    if (type === "10y") start = format(subYears(parseISO(end), 10), "yyyy-MM-dd");
    
    if (start < data.minDate) start = data.minDate;
    setDateRange({ start, end });
  };

  const toggleState = (st: string) => {
    const next = new Set(enabledStates);
    if (next.has(st)) next.delete(st);
    else next.add(st);
    setEnabledStates(next);
  };

  // Aggregate Data by Period
  const aggregatedData = useMemo(() => {
    if (!data) return { states: [], nasdaq: [] };
    if (period === "day") return data;

    const grouped = new Map<string, MarketStateRow[]>();
    
    data.states.forEach(row => {
      const date = parseISO(row.date);
      let key = "";
      if (period === "week") key = format(endOfWeek(date, { weekStartsOn: 1 }), "yyyy-MM-dd");
      else key = format(endOfMonth(date), "yyyy-MM-dd");
      
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(row);
    });

    const newStates: MarketStateRow[] = [];
    const sortedKeys = Array.from(grouped.keys()).sort();

    sortedKeys.forEach(key => {
      const rows = grouped.get(key)!;
      const lastRow = rows[rows.length - 1];
      const representativeDate =
        [...rows].reverse().find((r) => isUsTradingDay(r.date))?.date || lastRow.date || key;
      
      const validScores = rows.map(r => r.score).filter(s => s !== null) as number[];
      const avgScore = validScores.length ? validScores.reduce((a, b) => a + b, 0) / validScores.length : null;

      const stateCounts: Record<string, number> = {};
      rows.forEach(r => { stateCounts[r.state] = (stateCounts[r.state] || 0) + 1; });
      const dominantState = Object.entries(stateCounts).sort((a, b) => b[1] - a[1])[0][0] as any;

      newStates.push({
        ...lastRow,
        date: representativeDate,
        state: dominantState,
        score: avgScore,
        triggers: `${rows.length} days aggregated. Last: ${lastRow.triggers}`
      });
    });

    return { ...data, states: newStates };
  }, [data, period]);


  const forecastMap = useMemo(() => {
    const m = new Map<string, any>();
    (data?.forecastV1 || []).forEach((r) => {
      if (r.date) m.set(r.date, r);
    });
    return m;
  }, [data?.forecastV1]);

  const forecastChart = useMemo(() => {
    const rows = data?.forecastV1 || [];
    if (!rows || rows.length === 0) return { data: [] as any[] };
    const within = rows.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    const step = Math.max(1, Math.ceil(within.length / 1200));
    const sampled = within.filter((_, i) => i % step === 0).map((r) => {
      const c = (r as any)[`p_crisis_${forecastHorizon}`] ?? null;
      const e = (r as any)[`p_euphoria_${forecastHorizon}`] ?? null;
      const net = (r as any)[`net_${forecastHorizon}`] ?? (e !== null && c !== null ? e - c : null);
      return {
        date: r.date,
        p_crisis: c,
        p_euphoria: e,
        net,
      };
    });
    return { data: sampled };
  }, [data?.forecastV1, dateRange.start, dateRange.end, forecastHorizon]);

  const forecastDiag = useMemo(() => {
    const d = forecastChart.data || [];
    const tail = d.slice(-Math.min(120, d.length));
    const euphoriaSaturated =
      tail.length >= 30 &&
      tail.every((r: any) => typeof r.p_euphoria === "number" && Number.isFinite(r.p_euphoria) && r.p_euphoria >= 0.999);
    return { euphoriaSaturated };
  }, [forecastChart.data]);




  const tradingDaySet = useMemo(() => {
    return new Set(
      (data?.nasdaq || [])
        .filter((n) => n.close !== null && isUsTradingDay(n.date))
        .map((n) => n.date)
    );
  }, [data?.nasdaq]);

  const latestTiming = useMemo(() => {
    const rows = data?.timingV1 || [];
    if (!rows || rows.length === 0) return null;
    return rows[rows.length - 1];
  }, [data?.timingV1]);

  const probLevel = (v: any) => {
    if (typeof v !== "number" || !Number.isFinite(v)) return lang === "ko" ? "알 수 없음" : "unknown";
    if (v < 0.1) return lang === "ko" ? "매우 낮음" : "very low";
    if (v < 0.25) return lang === "ko" ? "낮음" : "low";
    if (v < 0.5) return lang === "ko" ? "보통" : "medium";
    if (v < 0.75) return lang === "ko" ? "높음" : "high";
    return lang === "ko" ? "매우 높음" : "very high";
  };

  const timingNarrative = useMemo(() => {
    if (!latestTiming) return null;
    const win = (start?: string | null, end?: string | null) => (start && end ? `${start} ~ ${end}` : null);

    const crisisWin = win(latestTiming.crisis_mode_start, latestTiming.crisis_mode_end);
    const euphoriaWin = win(latestTiming.euphoria_mode_start, latestTiming.euphoria_mode_end);
    const crisisEta = latestTiming.eta_crisis_median_date || null;
    const euphoriaEta = latestTiming.eta_euphoria_median_date || null;

    const c1m = probLevel((latestTiming as any).p_crisis_within_1m);
    const c3m = probLevel((latestTiming as any).p_crisis_within_3m);
    const c6m = probLevel((latestTiming as any).p_crisis_within_6m);
    const c1y = probLevel((latestTiming as any).p_crisis_within_1y);

    const e1w = probLevel((latestTiming as any).p_euphoria_within_1w);
    const e1m = probLevel((latestTiming as any).p_euphoria_within_1m);
    const e3m = probLevel((latestTiming as any).p_euphoria_within_3m);
    const e1y = probLevel((latestTiming as any).p_euphoria_within_1y);

    if (lang === "ko") {
      const lines: string[] = [];
      lines.push(`기준일 ${latestTiming.date} 기준, 아래 문구는 ‘확정일’이 아니라 참고용 예상입니다.`);
      lines.push(
        `공포(위기) 전환기 예정: ${crisisWin ? crisisWin : "유력 구간 정보가 부족합니다"} (ETA: ${crisisEta || "—"}). 단기(1개월) ${c1m}, 중기(3~6개월) ${c3m}/${c6m}, 1년 ${c1y}.`
      );
      lines.push(
        `환희 전환기 예정: ${euphoriaWin ? euphoriaWin : "유력 구간 정보가 부족합니다"} (ETA: ${euphoriaEta || "—"}). 단기(1주~1개월) ${e1w}/${e1m}, 중기(3개월) ${e3m}, 1년 ${e1y}.`
      );
      lines.push("참고: 위기/환희는 상승장/하락장(BULL/BEAR)과 1:1로 일치하지 않을 수 있습니다.");
      return { lines };
    }

    const lines: string[] = [];
    lines.push(`As of ${latestTiming.date}: the sentences below are probability-based references, not guaranteed event days.`);
    lines.push(
      `Fear (risk-off) transition window: ${crisisWin || "missing a reliable window"} (ETA: ${crisisEta || "—"}). Short-term (1m): ${c1m}; mid-term (3–6m): ${c3m}/${c6m}; 1y: ${c1y}.`
    );
    lines.push(
      `Euphoria transition window: ${euphoriaWin || "missing a reliable window"} (ETA: ${euphoriaEta || "—"}). Short-term (1w–1m): ${e1w}/${e1m}; mid-term (3m): ${e3m}; 1y: ${e1y}.`
    );
    lines.push("Note: Risk-off/Overheat are not a 1:1 match with Bull/Bear (±20% rule).");
    return { lines };
  }, [lang, latestTiming]);


  // Filter Data
  const filteredRows = useMemo(() => {
    const sourceData = aggregatedData;
    if (!sourceData) return [];
    
    let rows = sourceData.states.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    
    rows = rows.filter(r => enabledStates.has(r.state));

    if (onlyTradingDays && period === "day") {
      rows = rows.filter((r) => tradingDaySet.has(r.date));
    }

    if (searchTerm) {
      const lower = searchTerm.toLowerCase();
      rows = rows.filter(
        (r) =>
          r.triggers.toLowerCase().includes(lower) ||
          r.action.toLowerCase().includes(lower) ||
          r.state.toLowerCase().includes(lower)
      );
    }
    if (portfolioSearchTerm) {
      const lower = portfolioSearchTerm.toLowerCase();
      rows = rows.filter((r) => {
        const { portfolio } = resolvePortfolio(r.date);
        const text = portfolioSearchText(portfolio);
        return text.includes(lower);
      });
    }
    

    // Append Forecast v1 snapshot into triggers (if available)
    rows = rows.map((r) => {
      const fo: any = forecastMap.get(r.date);
      if (!fo) return r;
      const c = fo[`p_crisis_${forecastHorizon}`] ?? null;
      const e = fo[`p_euphoria_${forecastHorizon}`] ?? null;
      const cc = fo[`conf_crisis_${forecastHorizon}`] ?? null;
      const ec = fo[`conf_euphoria_${forecastHorizon}`] ?? null;
      const parts: string[] = [];
      if (c !== null) parts.push(`Crisis ${Math.round(c * 100)}%`);
      if (e !== null) parts.push(`Euphoria ${Math.round(e * 100)}%`);
      if (cc !== null || ec !== null) {
        const ccs = cc !== null ? `${Math.round(cc * 100)}%` : "-";
        const ecs = ec !== null ? `${Math.round(ec * 100)}%` : "-";
        parts.push(`Conf ${ccs}/${ecs}`);
      }
      const extra = parts.length ? ` | Forecast(${forecastHorizon}): ` + parts.join(" · ") : "";
      return { ...r, triggers: (r.triggers || "") + extra };
    });
    return rows;
  }, [
    aggregatedData,
    data,
    dateRange,
    enabledStates,
    onlyTradingDays,
    searchTerm,
    portfolioSearchTerm,
    period,
    viewBasis,
    portfolioDates,
    forecastMap,
    forecastHorizon,
    tradingDaySet,
  ]);

  // Chart Data
  const chartData = useMemo(() => {
    if (!aggregatedData || !data) return [];
    let rangeRows = aggregatedData.states.filter(
      (r) => r.date >= dateRange.start && r.date <= dateRange.end && enabledStates.has(r.state)
    );

    if (onlyTradingDays && period === "day") {
      rangeRows = rangeRows.filter((r) => tradingDaySet.has(r.date));
    }
    
    const nasdaqMap = new Map(data.nasdaq.map((n) => [n.date, n.close]));
    
    return rangeRows.map((s) => {
      let close = nasdaqMap.get(s.date);
      if (close === undefined && period !== "day") {
         const dates = Array.from(nasdaqMap.keys()).sort();
         const idx = dates.findIndex(d => d > s.date);
         if (idx > 0) close = nasdaqMap.get(dates[idx-1]);
         else if (idx === -1) close = nasdaqMap.get(dates[dates.length-1]);
      }

      return {
        date: s.date,
        close: close ?? null,
        state: s.state,
        score: s.score,
        triggers: s.triggers,
      };
    });
  }, [aggregatedData, data, dateRange, period, enabledStates, onlyTradingDays, tradingDaySet]);

  // State Mix Stats
  const stateCounts = useMemo(() => {
    const counts: Record<string, number> = { WARMUP: 0, NORMAL: 0, DEFCON2: 0, DEFCON1: 0 };
    filteredRows.forEach((r) => {
      if (counts[r.state] !== undefined) counts[r.state]++;
    });
    const total = filteredRows.length || 1;
    return STATE_ORDER.map((st) => ({
      name: st,
      count: counts[st],
      pct: ((counts[st] / total) * 100).toFixed(1),
      fill: STATE_COLORS[st as keyof typeof STATE_COLORS],
    }));
  }, [filteredRows]);

  // Reference Areas
  const refAreas = useMemo(() => {
    if (!chartData.length) return [];
    const areas = [];
    let start = chartData[0].date;
    let currentState = chartData[0].state;

    for (let i = 1; i < chartData.length; i++) {
      if (chartData[i].state !== currentState) {
        areas.push({ start, end: chartData[i].date, state: currentState });
        currentState = chartData[i].state;
        start = chartData[i].date;
      }
    }
    areas.push({ start, end: chartData[chartData.length - 1].date, state: currentState });
    
    return areas.filter(a => enabledStates.has(a.state));
  }, [chartData, enabledStates]);

  const handleChartClick = (e: any) => {
    if (Date.now() - lastPanAtRef.current < 200) return;
    if (e && e.activeLabel) {
      toggleDate(e.activeLabel, false);
    }
  };

  const handleWheelZoom = (e: React.WheelEvent) => {
    if (!data) return;
    const ZOOM_SPEED = 0.1;
    const currentStart = parseISO(dateRange.start).getTime();
    const currentEnd = parseISO(dateRange.end).getTime();
    const totalDuration = currentEnd - currentStart;
    
    const zoomFactor = e.deltaY < 0 ? (1 - ZOOM_SPEED) : (1 + ZOOM_SPEED);
    let newDuration = totalDuration * zoomFactor;
    
    const minDuration = 30 * 24 * 60 * 60 * 1000;
    const maxDuration = parseISO(data.maxDate).getTime() - parseISO(data.minDate).getTime();
    
    if (newDuration < minDuration) newDuration = minDuration;
    if (newDuration > maxDuration) newDuration = maxDuration;

    const center = currentStart + totalDuration / 2;
    let newStart = center - newDuration / 2;
    let newEnd = center + newDuration / 2;

    const absMin = parseISO(data.minDate).getTime();
    const absMax = parseISO(data.maxDate).getTime();

    if (newStart < absMin) { newStart = absMin; newEnd = newStart + newDuration; }
    if (newEnd > absMax) { newEnd = absMax; newStart = newEnd - newDuration; }

    setDateRange({
      start: format(newStart, "yyyy-MM-dd"),
      end: format(newEnd, "yyyy-MM-dd"),
    });
  };

  const startPan = (e: React.MouseEvent | React.PointerEvent) => {
    if (!data || ("button" in e && e.button !== 0)) return;
    if (!dateRange.start || !dateRange.end) return;
    if (dragRef.current.active) return;
    const target = e.target as HTMLElement | null;
    if (target?.closest(".recharts-brush")) return;

    const container = chartWrapRef.current;
    if (!container) return;
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startStart: parseISO(dateRange.start).getTime(),
      startEnd: parseISO(dateRange.end).getTime(),
      moved: false,
    };
    setIsPanning(true);
    e.preventDefault();
  };

  useEffect(() => {
    const handleMove = (e: MouseEvent) => {
      if (!dragRef.current.active || !data) return;
      const container = chartWrapRef.current;
      if (!container) return;
      const width = container.clientWidth;
      if (!width) return;

      const { startX, startStart, startEnd } = dragRef.current;
      const duration = startEnd - startStart;
      const dx = e.clientX - startX;
      if (Math.abs(dx) > 2) dragRef.current.moved = true;
      const shift = (-dx / width) * duration;

      let newStart = startStart + shift;
      let newEnd = startEnd + shift;

      const minMs = parseISO(data.minDate).getTime();
      const maxMs = parseISO(data.maxDate).getTime();
      if (newStart < minMs) {
        newStart = minMs;
        newEnd = minMs + duration;
      }
      if (newEnd > maxMs) {
        newEnd = maxMs;
        newStart = maxMs - duration;
      }

      setDateRange({
        start: format(newStart, "yyyy-MM-dd"),
        end: format(newEnd, "yyyy-MM-dd"),
      });
    };

    const handleUp = () => {
      if (!dragRef.current.active) return;
      const moved = dragRef.current.moved;
      dragRef.current.active = false;
      dragRef.current.moved = false;
      setIsPanning(false);
      if (moved) lastPanAtRef.current = Date.now();
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [data]);

  // Custom Tooltip
  const ExpandedTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length && data) {
      const row = payload[0].payload;
      let port = data.portfolio.get(row.date);
      let displayDate = row.date;

      if (!port) {
        const dates = Array.from(data.portfolio.keys()).sort();
        const idx = dates.findIndex(d => d > row.date);
        let fallbackDate = null;
        if (idx === -1 && dates.length > 0) fallbackDate = dates[dates.length - 1];
        else if (idx > 0) fallbackDate = dates[idx - 1];
        if (fallbackDate) {
          port = data.portfolio.get(fallbackDate);
          displayDate = fallbackDate;
        }
      }

      port = transformPortfolio(port);

      return (
        <div className="bg-white/95 backdrop-blur border border-slate-200 p-4 rounded-xl shadow-xl max-w-md w-[320px]">
          <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-100">
            <span className="font-mono font-bold text-lg text-slate-800">{label}</span>
            <span 
              className="px-2 py-0.5 rounded text-xs font-bold"
              style={{
                backgroundColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "20",
                color: STATE_COLORS[row.state as keyof typeof STATE_COLORS],
              }}
            >
              {row.state} (Score: {row.score?.toFixed(2)})
            </span>
          </div>
          
          <div className="space-y-4">
            <div>
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">{t.marketState}</div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-sm text-slate-600">{t.nasdaqClose}</span>
                <span className="font-bold font-mono text-slate-900">{row.close?.toFixed(2) ?? "—"}</span>
              </div>
              {row.triggers && (
                <div className="text-xs text-slate-500 bg-slate-50 p-2 rounded leading-snug">
                  {row.triggers}
                </div>
              )}
            </div>

            {port && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                    {t.portfolio} {displayDate !== row.date && `(${t.from} ${displayDate})`}
                  </span>
                </div>
                
                <div className="grid grid-cols-2 gap-2 mb-3">
                   <div className="bg-indigo-50/50 p-2 rounded border border-indigo-100/50">
                     <div className="text-[10px] text-indigo-400 font-semibold uppercase">{t.grossExposure}</div>
                     <div className="font-bold text-indigo-700 text-sm">{port.gross?.toFixed(2)}x</div>
                   </div>
                   <div className="bg-slate-50 p-2 rounded border border-slate-100">
                     <div className="text-[10px] text-slate-400 font-semibold uppercase">{t.cash}</div>
                     <div className="font-bold text-slate-700 text-sm">{((port.cash||0)*100).toFixed(0)}%</div>
                   </div>
                </div>

                <div className="flex flex-wrap gap-1.5">
                  {port.weights.map((w: any) => (
                    <span key={w.asset} className="inline-flex items-center px-2 py-1 rounded border border-slate-200 text-xs text-slate-600 bg-white shadow-sm">
                      <span className="font-medium mr-1">{w.asset}</span>
                      <b className="text-indigo-600">{(w.w*100).toFixed(0)}%</b>
                    </span>
                  ))}
                  {port.weights.length === 0 && (
                    <span className="text-xs text-slate-400 italic">{t.noAssets}</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      );
    }
    return null;
  };

  if (loading)
    return (
      <div className="flex h-screen items-center justify-center text-slate-500 bg-slate-50">
        <RefreshCw className="mr-2 h-6 w-6 animate-spin" /> Loading WarRoom...
      </div>
    );
  if (error) return <div className="p-8 text-red-500">Error: {error}</div>;
  if (!data) return null;

  const cycleOpinion = data.cycleOpinion;
  const cycleOpinionLinesRaw = cycleOpinion?.lines?.[lang];
  const cycleOpinionLines = Array.isArray(cycleOpinionLinesRaw)
    ? cycleOpinionLinesRaw.filter((s): s is string => typeof s === "string" && s.trim().length > 0)
    : [];
  const cycleOpinionSummary = (cycleOpinion?.summary?.[lang] || "").trim();
  const cycleOpinionAsOf = cycleOpinion?.as_of_date || null;
  const asOfMismatch = Boolean(cycleOpinionAsOf && latestTiming?.date && cycleOpinionAsOf !== latestTiming.date);

  return (
    <div className={cn("min-h-screen bg-slate-50 p-4 md:p-6 lg:p-8 font-sans", isExpanded && "overflow-hidden")}>
      {/* Detail Popup */}
      {showDetailPopup && lastSelectedDate && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in duration-200" onClick={() => setShowDetailPopup(false)}>
           <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden flex flex-col relative" onClick={e => e.stopPropagation()}>
              <button 
                onClick={() => setShowDetailPopup(false)}
                className="absolute top-4 right-4 p-2 hover:bg-slate-100 rounded-full text-slate-400 hover:text-slate-600 transition-colors"
              >
                <X className="h-5 w-5" />
              </button>
              
              <div className="p-6 space-y-6">
                <div>
                   <h3 className="text-2xl font-bold text-slate-900 mb-2 flex items-center gap-3">
                      <span className="font-mono">{lastSelectedDate}</span>
                   </h3>
                   {(() => {
                      const row = aggregatedData?.states.find(r => r.date === lastSelectedDate);
                      if (!row) return <div className="text-slate-500">No data available</div>;
                      return (
                        <div className="space-y-4">
                           <div className="flex items-center gap-2">
                             <span 
                               className="px-3 py-1 rounded-md text-sm font-bold border"
                               style={{
                                 backgroundColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "15",
                                 color: STATE_COLORS[row.state as keyof typeof STATE_COLORS],
                                 borderColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "30",
                               }}
                             >
                               {row.state}
                             </span>
                             <span className="text-sm font-mono text-slate-500">Score: {row.score?.toFixed(2)}</span>
                           </div>
                           
                           <div className="bg-slate-50 p-3 rounded-xl border border-slate-100">
                              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">Trigger Details</div>
                              <p className="text-sm text-slate-700 leading-relaxed">{row.triggers || "No triggers"}</p>
                           </div>

                           <div className="grid grid-cols-2 gap-3">
                              <div className="p-3 border border-slate-100 rounded-xl">
                                <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">NASDAQ Close</div>
                                <div className="font-mono text-lg font-bold text-slate-900">
                                   {(() => {
                                      const n = data?.nasdaq.find(x => x.date === lastSelectedDate);
                                      return n && n.close !== null ? n.close.toFixed(2) : "—";
                                   })()}
                                </div>
                              </div>
                              <div className="p-3 border border-slate-100 rounded-xl">
                                <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">Trend</div>
                                <div className={cn(
                                  "font-bold text-lg",
                                  row.trend === "UP" ? "text-green-600" : row.trend === "DOWN" ? "text-red-600" : "text-slate-400"
                                )}>
                                  {row.trend || "—"}
                                </div>
                              </div>
                           </div>
                        </div>
                      );
                   })()}
                </div>

                {/* Portfolio Section in Popup */}
                {(() => {
                    const resolved = resolvePortfolio(lastSelectedDate);
                    const p = transformPortfolio(resolved.portfolio);
                    const displayDate = resolved.displayDate;

                    if (!p) return null;

                    const portText = portfolioDisplayText(p);

                    return (
                      <div className="pt-6 border-t border-slate-100">
                        <div className="flex items-center justify-between mb-3">
                           <h4 className="font-bold text-slate-900 text-sm uppercase tracking-wide">Portfolio</h4>
                           {displayDate !== lastSelectedDate && (
                             <span className="text-[10px] bg-orange-50 text-orange-600 px-2 py-0.5 rounded-full font-medium">
                               From {displayDate}
                             </span>
                           )}
                        </div>
                        
                        <div className="grid grid-cols-2 gap-3 mb-4">
                           <div className="bg-indigo-50/50 p-3 rounded-lg border border-indigo-100/50">
                             <div className="text-[10px] text-indigo-400 font-semibold uppercase">Gross Exposure</div>
                             <div className="font-bold text-indigo-700 text-lg">{p.gross?.toFixed(2)}x</div>
                           </div>
                           <div className="bg-slate-50 p-3 rounded-lg border border-slate-100">
                             <div className="text-[10px] text-slate-400 font-semibold uppercase">Cash</div>
                             <div className="font-bold text-slate-700 text-lg">{((p.cash||0)*100).toFixed(0)}%</div>
                           </div>
                        </div>

                        <div className="bg-slate-50 p-3 rounded-xl border border-slate-100 mb-4">
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">{t.portfolioText}</span>
                            <button
                              type="button"
                              onClick={() => copyPortfolioText(portText)}
                              disabled={!portText}
                              className="text-[10px] font-semibold uppercase tracking-wider px-2 py-1 rounded border border-slate-200 bg-white text-slate-500 hover:text-slate-700 disabled:opacity-50"
                            >
                              {portfolioCopied ? t.copied : t.copy}
                            </button>
                          </div>
                          <p className="text-sm text-slate-700 leading-relaxed">{portText || t.noPortfolioData}</p>
                        </div>

                        <div className="space-y-2">
                            {p.weights.map((w: any) => (
                              <div key={w.asset} className="flex items-center justify-between text-sm">
                                <span className="text-slate-600 font-medium">{w.asset}</span>
                                <div className="flex-1 mx-3 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                   <div className="h-full bg-indigo-500" style={{ width: `${Math.min(w.w * 100, 100)}%` }} />
                                </div>
                                <span className="font-mono font-bold text-slate-900 w-10 text-right">{(w.w*100).toFixed(0)}%</span>
                              </div>
                            ))}
                        </div>
                      </div>
                    );
                })()}
              </div>
           </div>
        </div>
      )}

      {/* Expanded Chart Overlay */}
      {isExpanded && (
        <div className="fixed inset-0 z-50 bg-white flex flex-col animate-in fade-in zoom-in-95 duration-200">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-white/80 backdrop-blur sticky top-0 z-10">
            <div className="flex items-center gap-6">
              <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
                {t.detailedAnalysis}
                <span className="text-sm font-normal text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                  {dateRange.start} ~ {dateRange.end}
                </span>
              </h2>
              {/* Period Toggle in Modal */}
              <div className="flex bg-slate-100 p-1 rounded-lg">
                {(["day", "week", "month"] as const).map((p) => (
                  <button
                    key={p}
                    onClick={() => setPeriod(p)}
                    className={cn(
                      "px-3 py-1 text-xs font-semibold uppercase rounded-md transition-all",
                      period === p ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500 hover:text-slate-700"
                    )}
                  >
                    {p}
                  </button>
                ))}
              </div>
              {/* Quick Buttons in Modal */}
              <div className="flex bg-slate-100 p-1 rounded-lg">
                {["1y", "5y", "10y", "max"].map((t) => (
                  <button
                    key={t}
                    onClick={() => setRange(t as any)}
                    className="px-3 py-1 text-xs font-semibold uppercase text-slate-600 hover:bg-white hover:shadow-sm rounded-md transition-all"
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <button 
              onClick={() => setIsExpanded(false)}
              className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900"
            >
              <X className="h-6 w-6" />
            </button>
          </div>
          <div
            ref={chartWrapRef}
            onMouseDownCapture={startPan}
            onPointerDownCapture={startPan}
            onWheel={handleWheelZoom}
            className={cn(
              "flex-1 p-6 bg-slate-50/30 select-none",
              isPanning ? "cursor-grabbing" : "cursor-grab"
            )}
            style={{ touchAction: "none" }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 80 }} onClick={handleChartClick}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} minTickGap={80} />
                <YAxis domain={["auto", "auto"]} orientation="right" tick={{ fontSize: 12 }} />
                <Tooltip content={<ExpandedTooltip />} />
                {refAreas.map((area, idx) => (
                  <ReferenceArea
                    key={idx}
                    x1={area.start}
                    x2={area.end}
                    fill={STATE_COLORS[area.state as keyof typeof STATE_COLORS]}
                    fillOpacity={0.1}
                  />
                ))}
                <Line
                  type="monotone"
                  dataKey="close"
                  stroke="#1e293b"
                  strokeWidth={3}
                  dot={<CustomDot selectedDates={selectedDates} />}
                  activeDot={{ r: 8, stroke: "#fff", strokeWidth: 2 }}
                  connectNulls
                  isAnimationActive={false}
                />
                <Brush dataKey="date" height={50} stroke="#64748b" fill="#f1f5f9" tickFormatter={() => ""} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="mx-auto max-w-[1600px] space-y-6">
        {/* Header */}
        <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between bg-white p-5 rounded-2xl shadow-sm border border-slate-100">
          <div>
            <div className="text-xs font-bold text-indigo-500 uppercase tracking-wider mb-1">MarketMonitor</div>
            <h1 className="text-2xl font-bold text-slate-900">{t.headerTitle}</h1>
            <p className="text-slate-500 text-sm mt-1">
              {t.headerSub}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* Language Toggle */}
            <button
              onClick={toggleLang}
              className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm font-bold text-slate-600 hover:bg-slate-100 transition-colors"
            >
              <Languages className="h-4 w-4" />
              {lang === "en" ? "EN" : "KO"}
            </button>
            <button
              onClick={reload}
              className="flex items-center rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors"
            >
              <RefreshCw className="mr-2 h-4 w-4" /> {t.reload}
            </button>
          </div>
        </header>

        <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
          <CoupangTraderComboInline />
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Main Chart + Table Section (Left/Top) */}
          <div className="lg:col-span-8 space-y-6">
            <section className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 relative group transition-all hover:shadow-md">
              <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-4">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">{t.chartTitle}</h2>
                  <p className="text-slate-400 text-xs">{t.chartSub}</p>
                </div>
                
                {/* Expand Button */}
                <button 
                  onClick={() => setIsExpanded(true)}
                  className="absolute top-5 right-5 p-2 bg-white shadow-sm border border-slate-200 rounded-lg text-slate-400 hover:text-indigo-600 hover:border-indigo-200 hover:shadow transition-all opacity-0 group-hover:opacity-100 z-10"
                  title="Expand Chart"
                >
                  <Maximize2 className="h-5 w-5" />
                </button>

                {/* Controls */}
                <div className="flex flex-wrap gap-4 items-end">
                  {/* Period Toggle */}
                  <div className="flex bg-slate-100 p-1 rounded-lg">
                    {(["day", "week", "month"] as const).map((p) => (
                      <button
                        key={p}
                        onClick={() => setPeriod(p)}
                        className={cn(
                          "px-3 py-1 text-xs font-semibold uppercase rounded-md transition-all",
                          period === p ? "bg-white text-indigo-600 shadow-sm" : "text-slate-500 hover:text-slate-700"
                        )}
                      >
                        {p === "day" && lang === "ko" ? "일" : p === "week" && lang === "ko" ? "주" : p === "month" && lang === "ko" ? "월" : p}
                      </button>
                    ))}
                  </div>

                  {/* Date Range */}
                  <div className="flex items-center gap-2 bg-slate-50 p-1.5 rounded-lg border border-slate-200">
                    <input
                      type="date"
                      className="bg-transparent text-sm font-medium text-slate-700 outline-none w-[110px]"
                      value={dateRange.start}
                      onChange={(e) => setDateRange((p) => ({ ...p, start: e.target.value }))}
                      max={data.maxDate}
                    />
                    <span className="text-slate-400 text-xs">~</span>
                    <input
                      type="date"
                      className="bg-transparent text-sm font-medium text-slate-700 outline-none w-[110px]"
                      value={dateRange.end}
                      onChange={(e) => setDateRange((p) => ({ ...p, end: e.target.value }))}
                      max={data.maxDate}
                    />
                  </div>

                  {/* Reset Button */}
                  {selectedDates.size > 0 && (
                    <button
                      onClick={() => {
                        setSelectedDates(new Set());
                        setLastSelectedDate(null);
                      }}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold text-slate-500 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 hover:text-slate-700 transition-all shadow-sm"
                    >
                      <RotateCcw className="h-3 w-3" /> {t.reset}
                    </button>
                  )}

                  {/* Quick Buttons */}
                  <div className="flex bg-slate-50 p-1 rounded-lg border border-slate-200">
                    {["1y", "5y", "10y", "max"].map((t) => (
                      <button
                        key={t}
                        onClick={() => setRange(t as any)}
                        className="px-3 py-1 text-xs font-semibold uppercase text-slate-600 hover:bg-white hover:shadow-sm rounded-md transition-all"
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* State Checkboxes */}
	              <div className="flex flex-wrap gap-3 mb-4 text-sm">
	                {STATE_ORDER.map(st => (
	                  <label key={st} className="flex items-center gap-1.5 cursor-pointer select-none">
	                    <input 
	                      type="checkbox" 
	                      checked={enabledStates.has(st)} 
	                      onChange={() => toggleState(st)}
	                      className="rounded text-indigo-600 focus:ring-indigo-500 w-4 h-4"
	                    />
	                    <span className={cn(
	                      "px-2 py-0.5 rounded text-xs font-bold",
	                      st === "WARMUP" && "bg-blue-100 text-blue-700",
	                      st === "NORMAL" && "bg-green-100 text-green-700",
	                      st === "DEFCON2" && "bg-orange-100 text-orange-700",
	                      st === "DEFCON1" && "bg-red-100 text-red-700",
	                    )}>
	                      {st}
	                    </span>
	                  </label>
	                ))}
	                <div className="w-px h-5 bg-slate-200 mx-1"></div>
	                <label className="flex items-center gap-1.5 cursor-pointer select-none">
	                  <input 
	                    type="checkbox" 
	                    checked={onlyTradingDays} 
	                    onChange={(e) => setOnlyTradingDays(e.target.checked)}
	                    className="rounded text-indigo-600 focus:ring-indigo-500 w-4 h-4"
	                  />
		                  <span className="text-slate-600 text-xs font-medium">{t.onlyTradingDays}</span>
		                </label>
		              </div>

		              <div className="h-[350px] w-full">
		                <ResponsiveContainer width="100%" height="100%">
		                  <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }} onClick={handleChartClick}>
		                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
	                    <XAxis 
                      dataKey="date" 
                      tick={{ fontSize: 11, fill: "#64748b" }} 
                      tickMargin={10} 
                      minTickGap={60} 
                    />
                    <YAxis 
                      domain={["auto", "auto"]} 
                      orientation="right" 
                      tick={{ fontSize: 11, fill: "#64748b" }} 
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip content={<ExpandedTooltip />} />
                    {refAreas.map((area, idx) => (
                      <ReferenceArea
                        key={idx}
                        x1={area.start}
                        x2={area.end}
                        fill={STATE_COLORS[area.state as keyof typeof STATE_COLORS]}
                        fillOpacity={0.12}
                      />
                    ))}
                    <Line
                      type="monotone"
                      dataKey="close"
                      stroke="#334155"
                      strokeWidth={2}
                      dot={<CustomDot selectedDates={selectedDates} />}
                      activeDot={{ r: 6, strokeWidth: 0, fill: "#334155" }}
                      connectNulls
                      isAnimationActive={false}
                    />
                    <Brush dataKey="date" height={20} stroke="#cbd5e1" fill="#f8fafc" />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Table (Inside Chart Card, at the bottom) */}
              <div className="mt-8 pt-6 border-t border-slate-100">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold text-slate-900 uppercase tracking-wider">{t.dailyRecords}</h3>
                  <div className="flex items-center gap-2 flex-wrap justify-end">
                    <div className="relative w-56">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 h-3.5 w-3.5" />
                      <input
                        type="text"
                        placeholder={t.searchPlaceholder}
                        className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-9 pr-3 text-xs focus:border-indigo-500 focus:outline-none"
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                      />
                    </div>
                    <div className="relative w-56">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 h-3.5 w-3.5" />
                      <input
                        type="text"
                        placeholder={t.searchPortfolioPlaceholder}
                        className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-9 pr-3 text-xs focus:border-indigo-500 focus:outline-none"
                        value={portfolioSearchTerm}
                        onChange={(e) => setPortfolioSearchTerm(e.target.value)}
                      />
                    </div>
                  </div>
                </div>
                <div className="overflow-x-auto max-h-[350px]">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs uppercase text-slate-500 font-semibold sticky top-0 z-10">
                      <tr>
                        <th className="px-4 py-2 whitespace-nowrap">{t.colDate}</th>
                        <th className="px-4 py-2 whitespace-nowrap">{t.colState}</th>
                        <th className="px-4 py-2 text-right whitespace-nowrap">{t.colScore}</th>
                        <th className="px-4 py-2 whitespace-nowrap">{t.colTrend}</th>
                        <th className="px-4 py-2 text-right whitespace-nowrap">{t.colEquity}</th>
                        <th className="px-4 py-2 whitespace-nowrap">{t.colAction}</th>
                        <th className="px-4 py-2 whitespace-nowrap">{t.colTriggers}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {filteredRows.slice().reverse().map((row) => (
                        <tr
                          key={row.date}
                          id={`row-${row.date}`}
                          onClick={() => toggleDate(row.date)}
                          className={cn(
                            "cursor-pointer hover:bg-slate-50 transition-colors",
                            selectedDates.has(row.date) && "bg-indigo-50 hover:bg-indigo-100"
                          )}
                        >
                          <td className="px-4 py-2.5 font-mono text-slate-600 whitespace-nowrap font-medium">
                            {row.date}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-bold border"
                              style={{
                                backgroundColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "15",
                                color: STATE_COLORS[row.state as keyof typeof STATE_COLORS],
                                borderColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "30",
                              }}
                            >
                              {row.state}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono text-slate-700">
                            {row.score?.toFixed(2) ?? "—"}
                          </td>
                          <td className="px-4 py-2.5">
                            {row.trend === "UP" ? (
                              <span className="text-green-600 font-bold text-[10px] bg-green-50 px-1.5 py-0.5 rounded">UP</span>
                            ) : row.trend === "DOWN" ? (
                              <span className="text-red-600 font-bold text-[10px] bg-red-50 px-1.5 py-0.5 rounded">DOWN</span>
                            ) : (
                              <span className="text-slate-400 text-[10px]">—</span>
                            )}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono font-bold text-slate-900">
                            {row.equity !== null ? (row.equity * 100).toFixed(0) + "%" : "—"}
                          </td>
                          <td className="px-4 py-2.5 text-slate-600 font-medium text-xs truncate max-w-[120px]" title={row.action}>
                            {row.action}
                          </td>
                          <td className="px-4 py-2.5 text-slate-500 text-xs max-w-xs truncate" title={row.triggers}>
                            {row.triggers}
                          </td>
                        </tr>
                      ))}
                      {filteredRows.length === 0 && (
                        <tr>
                          <td colSpan={7} className="p-8 text-center text-slate-400 bg-slate-50/50">
                            <Filter className="mx-auto h-6 w-6 mb-2 opacity-50" />
                            <p className="text-xs">{t.noRecords}</p>
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

              </div>
            </section>





	            {/* Fear/Euphoria cycle layer (ribbon) */}
	            <section className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 relative group transition-all hover:shadow-md">
	              <div className="mb-2">
	                <div>
	                  <h2 className="text-lg font-bold text-slate-900">{lang === "ko" ? "위기/환희 사이클" : "Fear/Euphoria Cycle"}</h2>
	                  <p className="text-xs text-slate-500">
                      {lang === "ko" ? "띠그래프(1년) + 엔진 종합 문구" : "1y ribbon + engine narrative"}
                    </p>
	                </div>
	              </div>

	              {!latestTiming ? (
	                <div className="text-sm text-slate-500">
	                  No timing data loaded (expected: <span className="font-mono">data/timing_v1_daily.csv</span>)
	                </div>
	              ) : (
	                <div className="rounded-xl border border-slate-100 bg-slate-50 p-4">
	                  <CycleRibbon timing={latestTiming} lang={lang} />
                    {asOfMismatch ? (
                      <div className="mt-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
                        {lang === "ko"
                          ? `주의: 엔진 의견 기준일(${cycleOpinionAsOf})과 타이밍 데이터 기준일(${latestTiming.date})이 다릅니다.`
                          : `Note: engine as-of (${cycleOpinionAsOf}) differs from timing as-of (${latestTiming.date}).`}
                      </div>
                    ) : null}
	                  {(() => {
                        const lines = cycleOpinionLines.length ? cycleOpinionLines : timingNarrative?.lines || [];
                        if (!cycleOpinionSummary && lines.length === 0) return null;
                        const shown = lines;
                        return (
                          <div className="mt-3 space-y-1.5 text-xs text-slate-700 leading-relaxed">
                            {cycleOpinionSummary ? <p className="font-semibold text-slate-900">{cycleOpinionSummary}</p> : null}
                            {shown.map((line, i) => (
                              <p key={`${i}-${line}`}>{line}</p>
                            ))}
                          </div>
                        );
                      })()}
	                </div>
	              )}
	            </section>

            {/* Forecast v1 (Crisis + Euphoria) */}
            <section className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 relative group transition-all hover:shadow-md">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-bold text-slate-900">{t.forecastTitle}</h2>
                   {(() => {
                      const last = data?.forecastV1?.[data.forecastV1.length - 1];
                      if (last?.status && last.status !== "OK") {
                        return (
                          <span className="px-2 py-0.5 rounded text-xs font-bold bg-amber-100 text-amber-700 border border-amber-200">
                            {last.status}
                          </span>
                        );
                      }
                      return null;
                    })()}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-slate-500 font-medium">{t.horizon}</span>
                  <select
                    className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700"
                    value={forecastHorizon}
                    onChange={(e) => setForecastHorizon(e.target.value as any)}
                    aria-label="Forecast horizon"
                  >
                    <option value="1y">1y</option>
                    <option value="2y">2y</option>
                    <option value="3y">3y</option>
                  </select>

                  <button
                    onClick={() => setShowForecastChart((v) => !v)}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    aria-label={showForecastChart ? "Hide forecast chart" : "Show forecast chart"}
                  >
                    {showForecastChart ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    <span>{showForecastChart ? (lang === "ko" ? "차트 숨기기" : "Hide") : (lang === "ko" ? "차트 보기" : "Show")}</span>
                  </button>
                </div>
              </div>



              <div className="text-[11px] text-slate-500 leading-relaxed mb-3">
                {lang === "ko" ? (
                  <>
                    전망은 각 날짜 기준으로 “{forecastHorizon} 뒤 시점”의 상태 확률을 뜻합니다. 기간 동안의 ‘확정 상태’가 아닙니다.
                    <br />
                    순확률(환희−위기)은 방향성 요약이며, 확정 판단이 아닙니다.
                    <br />
                    위기/환희 전망은 BULL/BEAR 구분과 1:1로 일치하지 않을 수 있습니다.
                  </>
                ) : (
                  <>
                    This forecast describes the market state “{forecastHorizon} later from each date”. It is not “guaranteed until year X”.
                    <br />
                    Net (Overheat − Risk-off) is a directional summary, not a guarantee.
                    <br />
                    Risk-off/Overheat may not match Bull/Bear phases 1:1.
                  </>
                )}

                {forecastDiag.euphoriaSaturated ? (
                  <div className="mt-2 inline-block rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-amber-800">
                    {lang === "ko"
                      ? "주의: 환희 확률이 장기간 100%에 가깝게 포화되어 보입니다. 데이터/모델(p_euphoria)을 점검하세요."
                      : "Warning: Overheat probability looks saturated near 100% for an extended period. Check data/model (p_euphoria)."}
                  </div>
                ) : null}
              </div>
              {(!data?.forecastV1 || data.forecastV1.length === 0) ? (
                <div className="text-sm text-slate-500">
                  No forecast data loaded (expected: <span className="font-mono">data/forecast_v1_daily.csv</span>)
                </div>
              ) : (
                <>
                  {!showForecastChart ? (
                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 bg-slate-50 border border-slate-100 rounded-xl p-4">
                        {(() => {
                          const last = data?.forecastV1?.[data.forecastV1.length - 1] as any;
                          if (!last) return <div className="text-sm text-slate-600">—</div>;
                          const c = last?.[`p_crisis_${forecastHorizon}`];
                          const e = last?.[`p_euphoria_${forecastHorizon}`];
                          const n = last?.[`net_${forecastHorizon}`] ?? (typeof c === "number" && typeof e === "number" ? e - c : null);

                          const label = (() => {
                            if (typeof n !== "number" || !Number.isFinite(n)) return lang === "ko" ? "해석 불가" : "Unknown";
                            if (n >= 0.15) return lang === "ko" ? "환희 우세(강)" : "Strong overheat bias";
                            if (n >= 0.05) return lang === "ko" ? "환희 우세" : "Overheat bias";
                            if (n <= -0.15) return lang === "ko" ? "위기 우세(강)" : "Strong risk-off bias";
                            if (n <= -0.05) return lang === "ko" ? "위기 우세" : "Risk-off bias";
                            return lang === "ko" ? "중립/혼조" : "Mixed/neutral";
                          })();

                          const pillClass = (() => {
                            if (typeof n !== "number" || !Number.isFinite(n)) return "bg-slate-100 text-slate-700 border-slate-200";
                            if (n >= 0.05) return "bg-green-50 text-green-700 border-green-100";
                            if (n <= -0.05) return "bg-red-50 text-red-700 border-red-100";
                            return "bg-blue-50 text-blue-700 border-blue-100";
                          })();

                          return (
                            <div className="w-full space-y-1">
                              <div className="flex items-center justify-between gap-2">
                                <div className="text-sm text-slate-600">
                                  {lang === "ko"
                                    ? `최근 데이터 기준으로, ${forecastHorizon} 뒤 시점은 “${label}”로 해석됩니다(확정 아님).`
                                    : `As of the latest row, the ${forecastHorizon}-ahead state reads as “${label}” (not a guarantee).`}
                                </div>
                                <span className={cn("px-2 py-1 rounded-lg text-xs font-bold border", pillClass)}>{label}</span>
                              </div>
                              <div className="text-[11px] text-slate-500">
                                {lang === "ko"
                                  ? "차트는 숨김 상태입니다. 필요하면 ‘차트 보기’를 눌러 확인하세요."
                                  : "Chart is hidden. Click ‘Show’ to view."}
                              </div>
                            </div>
                          );
                        })()}
                    </div>
                  ) : (
                    <div className="mt-3">
                      <div className="h-[320px]">
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={forecastChart.data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={30} />
                            <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
                            <Tooltip
                              formatter={(v: any) => (typeof v === "number" ? `${Math.round(v * 100)}%` : v)}
                              labelFormatter={(l: any) => `${l}`}
                              contentStyle={{ borderRadius: "10px" }}
                            />
                            <Line type="monotone" dataKey="p_crisis" name={t.crisis} dot={false} stroke="#ef4444" strokeWidth={1.8} isAnimationActive={false} />
                            <Line type="monotone" dataKey="p_euphoria" name={t.euphoria} dot={false} stroke="#22c55e" strokeWidth={1.8} isAnimationActive={false} />
                            <Line type="monotone" dataKey="net" name={t.net} dot={false} stroke="#1d4ed8" strokeWidth={1.4} isAnimationActive={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>

          </div>

          {/* Right Column: Stats + Portfolio */}
          <section className="lg:col-span-4 space-y-6">
            {/* State Mix */}
            <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100">
              <h2 className="text-lg font-bold text-slate-900 mb-4">{t.stateMix}</h2>
              <div className="h-[180px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={stateCounts} layout="vertical" margin={{ left: 40 }}>
                    <XAxis type="number" hide />
                    <YAxis 
                      dataKey="name" 
                      type="category" 
                      width={60} 
                      tick={{ fontSize: 10, fontWeight: 600, fill: "#64748b" }} 
                      axisLine={false} 
                      tickLine={false} 
                    />
                    <Tooltip 
                      cursor={{ fill: 'transparent' }}
                      contentStyle={{ borderRadius: "8px" }}
                    />
                    <Bar dataKey="count" radius={[0, 4, 4, 0]} barSize={20}>
                      {stateCounts.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={entry.fill} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                {stateCounts.map(s => (
                  <div key={s.name} className="flex justify-between text-xs px-2 py-1 bg-slate-50 rounded">
                    <span className="font-medium text-slate-600">{s.name}</span>
                    <span className="font-bold text-slate-900">{s.count} ({s.pct}%)</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Portfolio Detail */}
            <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold text-slate-900">{t.portfolioDetail}</h2>
                <div className="flex items-center gap-2">
                   <div className="flex bg-slate-100 p-0.5 rounded-lg">
                      {(["1x", "2x"] as const).map((b) => (
                        <button
                          key={b}
                          onClick={() => setViewBasis(b)}
                          className={cn(
                            "px-2 py-0.5 text-[10px] font-bold uppercase rounded-md transition-all",
                            viewBasis === b ? "bg-white text-indigo-600 shadow-sm" : "text-slate-400 hover:text-slate-600"
                          )}
                        >
                          {b}
                        </button>
                      ))}
                   </div>
                   {lastSelectedDate && <span className="text-xs font-mono bg-slate-100 px-2 py-1 rounded text-slate-600">{lastSelectedDate}</span>}
                </div>
              </div>
              
              {lastSelectedDate ? (
                <div>
                  {(() => {
                    let p = data.portfolio.get(lastSelectedDate);
                    let displayDate = lastSelectedDate;
                    if (!p) {
                      const dates = Array.from(data.portfolio.keys()).sort();
                      const idx = dates.findIndex(d => d > lastSelectedDate);
                      let fallbackDate = null;
                      if (idx === -1 && dates.length > 0) fallbackDate = dates[dates.length - 1];
                      else if (idx > 0) fallbackDate = dates[idx - 1];
                      if (fallbackDate) {
                        p = data.portfolio.get(fallbackDate);
                        displayDate = fallbackDate;
                      }
                    }

                    p = transformPortfolio(p);

                    return p ? (
                      <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                        {displayDate !== lastSelectedDate && (
                          <div className="text-xs text-orange-500 font-medium mb-2">
                            * Data from {displayDate}
                          </div>
                        )}
                        <div className="grid grid-cols-2 gap-3">
                          <div className="bg-slate-50 p-3 rounded-lg">
                            <div className="text-xs text-slate-500 mb-1">{t.grossExposure}</div>
                            <div className="text-xl font-bold text-slate-900">{p.gross?.toFixed(2)}x</div>
                          </div>
                          <div className="bg-slate-50 p-3 rounded-lg">
                            <div className="text-xs text-slate-500 mb-1">{t.cash}</div>
                            <div className="text-xl font-bold text-slate-900">{((p.cash || 0) * 100).toFixed(1)}%</div>
                          </div>
                        </div>
                        
                        <div>
                          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">{t.allocations}</div>
                          <div className="space-y-2">
                            {p.weights.map((w) => (
                              <div key={w.asset} className="flex items-center justify-between group">
                                <span className="text-sm font-medium text-slate-700">{w.asset}</span>
                                <div className="flex items-center flex-1 mx-3">
                                  <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                                    <div
                                      className="h-full bg-indigo-500 rounded-full"
                                      style={{ width: `${Math.min(w.w * 100, 100)}%` }}
                                    />
                                  </div>
                                </div>
                                <span className="text-sm font-mono font-bold text-slate-900 w-12 text-right">
                                  {(w.w * 100).toFixed(1)}%
                                </span>
                              </div>
                            ))}
                            {p.weights.length === 0 && (
                              <div className="text-center text-sm text-slate-400 py-4 bg-slate-50 rounded-lg">
                                {t.noAssets}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="text-slate-400 text-sm text-center py-8">
                        {t.noPortfolioData}
                      </div>
                    );
                  })()}
                </div>
              ) : (
                <div className="text-slate-400 text-sm text-center py-10 bg-slate-50 rounded-lg border border-dashed border-slate-200">
                  {t.clickHint}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
