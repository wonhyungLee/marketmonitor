import { useState, useMemo, useEffect } from "react";
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
import { RefreshCw, Search, Filter, Maximize2, X, RotateCcw, Languages } from "lucide-react";
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
    stateMix: "State Mix",
    portfolioDetail: "Portfolio Detail",
    grossExposure: "Gross",
    cash: "Cash",
    allocations: "ALLOCATIONS",
    noAssets: "Cash 100% (No Assets)",
    noPortfolioData: "No portfolio data available for this date.",
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
    stateMix: "상태 분포",
    portfolioDetail: "포트폴리오 상세",
    grossExposure: "총 노출",
    cash: "현금",
    allocations: "자산 배분",
    noAssets: "현금 100% (자산 없음)",
    noPortfolioData: "이 날짜의 포트폴리오 데이터가 없습니다.",
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
  
  // Multi-select State
  const [selectedDates, setSelectedDates] = useState<Set<string>>(new Set());
  const [lastSelectedDate, setLastSelectedDate] = useState<string | null>(null);

  const [isExpanded, setIsExpanded] = useState(false);
  const [period, setPeriod] = useState<Period>("day");
  const [viewBasis, setViewBasis] = useState<"1x" | "2x">("2x");

  const transformPortfolio = (p: any) => {
    if (!p || viewBasis === "2x") return p;
    return {
      ...p,
      gross: (p.gross || 0) / 2,
      cash: 1 - ((p.gross || 0) / 2),
      weights: p.weights.map((w: any) => ({ ...w, w: w.w / 2 })),
    };
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
  
  // Popup State
  const [showPopup, setShowPopup] = useState(false);
  const [showDetailPopup, setShowDetailPopup] = useState(false);
  const [dontShowToday, setDontShowToday] = useState(false);

  useEffect(() => {
    const hideUntil = localStorage.getItem("hidePopupUntil");
    if (!hideUntil || new Date().getTime() > parseInt(hideUntil)) {
      setShowPopup(true);
    }
  }, []);

  const handleClosePopup = () => {
    if (dontShowToday) {
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      localStorage.setItem("hidePopupUntil", tomorrow.getTime().toString());
    }
    setShowPopup(false);
  };

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
      
      const validScores = rows.map(r => r.score).filter(s => s !== null) as number[];
      const avgScore = validScores.length ? validScores.reduce((a, b) => a + b, 0) / validScores.length : null;

      const stateCounts: Record<string, number> = {};
      rows.forEach(r => { stateCounts[r.state] = (stateCounts[r.state] || 0) + 1; });
      const dominantState = Object.entries(stateCounts).sort((a, b) => b[1] - a[1])[0][0] as any;

      newStates.push({
        ...lastRow,
        date: key,
        state: dominantState,
        score: avgScore,
        triggers: `${rows.length} days aggregated. Last: ${lastRow.triggers}`
      });
    });

    return { ...data, states: newStates };
  }, [data, period]);

  // Filter Data
  const filteredRows = useMemo(() => {
    const sourceData = aggregatedData;
    if (!sourceData) return [];
    
    let rows = sourceData.states.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    
    rows = rows.filter(r => enabledStates.has(r.state));

    if (onlyTradingDays && period === "day") {
      const tradingDays = new Set(data?.nasdaq.map(n => n.date));
      rows = rows.filter(r => tradingDays.has(r.date));
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
    return rows;
  }, [aggregatedData, data, dateRange, enabledStates, onlyTradingDays, searchTerm, period]);

  // Chart Data
  const chartData = useMemo(() => {
    if (!aggregatedData || !data) return [];
    const rangeRows = aggregatedData.states.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    
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
        close: close || null,
        state: s.state,
        score: s.score,
        triggers: s.triggers,
      };
    });
  }, [aggregatedData, data, dateRange, period]);

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

  return (
    <div className={cn("min-h-screen bg-slate-50 p-4 md:p-6 lg:p-8 font-sans", isExpanded && "overflow-hidden")}>
      
      {/* Promo Popup */}
      {showPopup && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-2xl shadow-2xl max-w-sm md:max-w-2xl lg:max-w-3xl w-full overflow-hidden flex flex-col">
            <div className="relative">
              <img 
                src="popup.png" 
                alt="Notification" 
                className="w-full h-auto object-cover"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                  e.currentTarget.parentElement!.innerHTML += '<div class="p-8 text-center text-slate-500 font-bold">Notice</div>';
                }}
              />
              <button 
                onClick={() => setShowPopup(false)}
                className="absolute top-3 right-3 p-1.5 bg-black/20 hover:bg-black/40 text-white rounded-full transition-colors"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="p-4 bg-slate-50 flex items-center justify-between border-t border-slate-100">
              <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-slate-600">
                <input 
                  type="checkbox" 
                  checked={dontShowToday}
                  onChange={(e) => setDontShowToday(e.target.checked)}
                  className="rounded text-indigo-600 focus:ring-indigo-500 w-4 h-4"
                />
                Don't show today
              </label>
              <button 
                onClick={handleClosePopup}
                className="px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white text-sm font-bold rounded-lg transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

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
                                      return n ? n.close.toFixed(2) : "—";
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
                    let p = data?.portfolio.get(lastSelectedDate);
                    let displayDate = lastSelectedDate;
                    if (!p && data) {
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

                    if (!p) return null;

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
          <div className="flex-1 p-6 bg-slate-50/30" onWheel={handleWheelZoom}>
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
                  <div className="relative w-64">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 h-3.5 w-3.5" />
                    <input
                      type="text"
                      placeholder={t.searchPlaceholder}
                      className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-9 pr-3 text-xs focus:border-indigo-500 focus:outline-none"
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                    />
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
            <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 sticky top-6">
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
