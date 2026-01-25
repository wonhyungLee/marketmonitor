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
import { format, parseISO, subYears } from "date-fns";
import { useWarRoomData } from "@/hooks/useWarRoomData";
import { cn } from "@/lib/utils";
import { RefreshCw, Search, Filter, Maximize2, X } from "lucide-react";

const STATE_COLORS = {
  WARMUP: "#3498db",
  NORMAL: "#2ecc71",
  DEFCON2: "#e67e22",
  DEFCON1: "#e74c3c",
};

const STATE_ORDER = ["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"];

export default function App() {
  const { data, loading, error, reload } = useWarRoomData();
  const [dateRange, setDateRange] = useState({ start: "", end: "" });
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Filters
  const [enabledStates, setEnabledStates] = useState<Set<string>>(
    new Set(["WARMUP", "NORMAL", "DEFCON2", "DEFCON1"])
  );
  const [onlyTradingDays, setOnlyTradingDays] = useState(true);

  // Initialize date range
  useEffect(() => {
    if (data && !dateRange.start) {
      // Default to last 1 year
      const end = data.maxDate;
      const start = format(subYears(parseISO(end), 1), "yyyy-MM-dd");
      setDateRange({ start, end });
    }
  }, [data]);

  // Quick Range Handlers
  const setRange = (type: "1y" | "5y" | "max") => {
    if (!data) return;
    const end = data.maxDate;
    let start = data.minDate;
    if (type === "1y") start = format(subYears(parseISO(end), 1), "yyyy-MM-dd");
    if (type === "5y") start = format(subYears(parseISO(end), 5), "yyyy-MM-dd");
    
    // Clamp to minDate
    if (start < data.minDate) start = data.minDate;
    setDateRange({ start, end });
  };

  const toggleState = (st: string) => {
    const next = new Set(enabledStates);
    if (next.has(st)) next.delete(st);
    else next.add(st);
    setEnabledStates(next);
  };

  // Filter Data
  const filteredRows = useMemo(() => {
    if (!data) return [];
    let rows = data.states.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    
    // Filter by State checkbox
    rows = rows.filter(r => enabledStates.has(r.state));

    // Filter by Only Trading Days (requires NASDAQ data presence)
    if (onlyTradingDays) {
      const tradingDays = new Set(data.nasdaq.map(n => n.date));
      rows = rows.filter(r => tradingDays.has(r.date));
    }

    // Filter by Search
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
  }, [data, dateRange, enabledStates, onlyTradingDays, searchTerm]);

  // Chart Data
  const chartData = useMemo(() => {
    if (!data) return [];
    const rangeRows = data.states.filter((r) => r.date >= dateRange.start && r.date <= dateRange.end);
    
    const nasdaqMap = new Map(data.nasdaq.map((n) => [n.date, n.close]));
    return rangeRows.map((s) => ({
      date: s.date,
      close: nasdaqMap.get(s.date) || null,
      state: s.state,
      score: s.score,
      triggers: s.triggers,
    }));
  }, [data, dateRange]);

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
      setSelectedDate(e.activeLabel);
    }
  };

  // Custom Tooltip for Expanded View
  const ExpandedTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length && data) {
      const row = payload[0].payload;
      let port = data.portfolio.get(row.date);
      let displayDate = row.date;

      // Fallback for portfolio
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
              {row.state} (Score: {row.score})
            </span>
          </div>
          
          <div className="space-y-4">
            {/* Market Info */}
            <div>
              <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">Market State</div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-sm text-slate-600">NASDAQ Close</span>
                <span className="font-bold font-mono text-slate-900">{row.close?.toFixed(2) ?? "—"}</span>
              </div>
              {row.triggers && (
                <div className="text-xs text-slate-500 bg-slate-50 p-2 rounded leading-snug">
                  {row.triggers}
                </div>
              )}
            </div>

            {/* Portfolio Info */}
            {port && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                    Portfolio {displayDate !== row.date && `(from ${displayDate})`}
                  </span>
                </div>
                
                <div className="grid grid-cols-2 gap-2 mb-3">
                   <div className="bg-indigo-50/50 p-2 rounded border border-indigo-100/50">
                     <div className="text-[10px] text-indigo-400 font-semibold uppercase">Gross</div>
                     <div className="font-bold text-indigo-700 text-sm">{port.gross?.toFixed(2)}x</div>
                   </div>
                   <div className="bg-slate-50 p-2 rounded border border-slate-100">
                     <div className="text-[10px] text-slate-400 font-semibold uppercase">Cash</div>
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
                    <span className="text-xs text-slate-400 italic">No assets allocated</span>
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
      
      {/* Expanded Chart Overlay */}
      {isExpanded && (
        <div className="fixed inset-0 z-50 bg-white flex flex-col animate-in fade-in zoom-in-95 duration-200">
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-white/80 backdrop-blur sticky top-0 z-10">
            <div>
              <h2 className="text-xl font-bold text-slate-900 flex items-center gap-2">
                Detailed Analysis
                <span className="text-sm font-normal text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
                  {dateRange.start} ~ {dateRange.end}
                </span>
              </h2>
            </div>
            <button 
              onClick={() => setIsExpanded(false)}
              className="p-2 hover:bg-slate-100 rounded-full transition-colors text-slate-500 hover:text-slate-900"
            >
              <X className="h-6 w-6" />
            </button>
          </div>
          <div className="flex-1 p-6 bg-slate-50/30">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 80 }}>
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
                  dot={false}
                  activeDot={{ r: 8, stroke: "#fff", strokeWidth: 2 }}
                  connectNulls
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
            <h1 className="text-2xl font-bold text-slate-900">NASDAQ + Market State</h1>
            <p className="text-slate-500 text-sm mt-1">
              Visualizing regime changes and portfolio allocations
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={reload}
              className="flex items-center rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-200 transition-colors"
            >
              <RefreshCw className="mr-2 h-4 w-4" /> Reload
            </button>
          </div>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Main Chart Section (Left/Top) */}
          <section className="lg:col-span-8 bg-white p-5 rounded-2xl shadow-sm border border-slate-100 relative group transition-all hover:shadow-md">
            <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-4">
              <div>
                <h2 className="text-lg font-bold text-slate-900">Chart</h2>
                <p className="text-slate-400 text-xs">Line: NASDAQ Close | Background: Market State</p>
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
                {/* Date Range */}
                <div className="flex items-center gap-2 bg-slate-50 p-1.5 rounded-lg border border-slate-200">
                  <input
                    type="date"
                    className="bg-transparent text-sm font-medium text-slate-700 outline-none w-[110px]"
                    value={dateRange.start}
                    onChange={(e) => setDateRange((p) => ({ ...p, start: e.target.value }))}
                    max={data.maxDate}
                  />
                  <span className="text-slate-400 text-xs">to</span>
                  <input
                    type="date"
                    className="bg-transparent text-sm font-medium text-slate-700 outline-none w-[110px]"
                    value={dateRange.end}
                    onChange={(e) => setDateRange((p) => ({ ...p, end: e.target.value }))}
                    max={data.maxDate}
                  />
                </div>

                {/* Quick Buttons */}
                <div className="flex bg-slate-50 p-1 rounded-lg border border-slate-200">
                  {["1y", "5y", "max"].map((t) => (
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
                <span className="text-slate-600 text-xs font-medium">Only trading days (Table)</span>
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
                  <Tooltip
                    contentStyle={{ borderRadius: "12px", border: "none", boxShadow: "0 10px 15px -3px rgb(0 0 0 / 0.1)" }}
                    labelStyle={{ fontWeight: "bold", color: "#1e293b", marginBottom: "0.5rem" }}
                  />
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
                    dot={false}
                    activeDot={{ r: 6, strokeWidth: 0, fill: "#334155" }}
                    connectNulls
                  />
                  <Brush dataKey="date" height={20} stroke="#cbd5e1" fill="#f8fafc" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </section>

          {/* Side Panel (Stats + Portfolio) */}
          <section className="lg:col-span-4 space-y-6">
            {/* State Mix */}
            <div className="bg-white p-5 rounded-2xl shadow-sm border border-slate-100">
              <h2 className="text-lg font-bold text-slate-900 mb-4">State Mix</h2>
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
                <h2 className="text-lg font-bold text-slate-900">Portfolio Detail</h2>
                {selectedDate && <span className="text-xs font-mono bg-slate-100 px-2 py-1 rounded text-slate-600">{selectedDate}</span>}
              </div>
              
              {selectedDate ? (
                <div>
                  {(() => {
                    // Reuse fallback logic for side panel
                    let p = data.portfolio.get(selectedDate);
                    let displayDate = selectedDate;
                    if (!p) {
                      const dates = Array.from(data.portfolio.keys()).sort();
                      const idx = dates.findIndex(d => d > selectedDate);
                      let fallbackDate = null;
                      if (idx === -1 && dates.length > 0) fallbackDate = dates[dates.length - 1];
                      else if (idx > 0) fallbackDate = dates[idx - 1];
                      if (fallbackDate) {
                        p = data.portfolio.get(fallbackDate);
                        displayDate = `${fallbackDate} (Latest)`;
                      }
                    }

                    return p ? (
                      <div className="space-y-4 animate-in fade-in slide-in-from-bottom-2 duration-300">
                        {displayDate !== selectedDate && (
                          <div className="text-xs text-orange-500 font-medium mb-2">
                            * Data from {displayDate}
                          </div>
                        )}
                        <div className="grid grid-cols-2 gap-3">
                          <div className="bg-slate-50 p-3 rounded-lg">
                            <div className="text-xs text-slate-500 mb-1">Gross Exposure</div>
                            <div className="text-xl font-bold text-slate-900">{p.gross?.toFixed(2)}x</div>
                          </div>
                          <div className="bg-slate-50 p-3 rounded-lg">
                            <div className="text-xs text-slate-500 mb-1">Cash</div>
                            <div className="text-xl font-bold text-slate-900">{((p.cash || 0) * 100).toFixed(1)}%</div>
                          </div>
                        </div>
                        
                        <div>
                          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Allocations</div>
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
                                Cash 100% (No Assets)
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="text-slate-400 text-sm text-center py-8">
                        No portfolio data available for this date.
                      </div>
                    );
                  })()}
                </div>
              ) : (
                <div className="text-slate-400 text-sm text-center py-10 bg-slate-50 rounded-lg border border-dashed border-slate-200">
                  Click a row in the table or a point on the chart to view details.
                </div>
              )}
            </div>
          </section>
        </div>

        {/* Table Section */}
        <section className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
          <div className="border-b border-slate-100 p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <h2 className="text-lg font-bold text-slate-900">Daily Records</h2>
            <div className="relative w-full sm:w-72">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 h-4 w-4" />
              <input
                type="text"
                placeholder="Filter triggers, actions..."
                className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-10 pr-4 text-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none transition-all"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500 font-semibold">
                <tr>
                  <th className="px-6 py-3 whitespace-nowrap">Date</th>
                  <th className="px-6 py-3 whitespace-nowrap">State</th>
                  <th className="px-6 py-3 text-right whitespace-nowrap">Score</th>
                  <th className="px-6 py-3 whitespace-nowrap">Trend</th>
                  <th className="px-6 py-3 text-right whitespace-nowrap">Equity</th>
                  <th className="px-6 py-3 whitespace-nowrap">Action</th>
                  <th className="px-6 py-3 whitespace-nowrap">Triggers</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredRows.slice().reverse().map((row) => (
                  <tr
                    key={row.date}
                    onClick={() => setSelectedDate(row.date)}
                    className={cn(
                      "cursor-pointer hover:bg-slate-50 transition-colors",
                      selectedDate === row.date && "bg-indigo-50 hover:bg-indigo-100"
                    )}
                  >
                    <td className="px-6 py-3 font-mono text-slate-600 whitespace-nowrap font-medium">
                      {row.date}
                    </td>
                    <td className="px-6 py-3">
                      <span
                        className="inline-flex items-center rounded-md px-2 py-1 text-xs font-bold border"
                        style={{
                          backgroundColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "15",
                          color: STATE_COLORS[row.state as keyof typeof STATE_COLORS],
                          borderColor: STATE_COLORS[row.state as keyof typeof STATE_COLORS] + "30",
                        }}
                      >
                        {row.state}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-right font-mono text-slate-700">
                      {row.score?.toFixed(2) ?? "—"}
                    </td>
                    <td className="px-6 py-3">
                      {row.trend === "UP" ? (
                        <span className="text-green-600 font-bold text-xs bg-green-50 px-1.5 py-0.5 rounded">UP</span>
                      ) : row.trend === "DOWN" ? (
                        <span className="text-red-600 font-bold text-xs bg-red-50 px-1.5 py-0.5 rounded">DOWN</span>
                      ) : (
                        <span className="text-slate-400 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-right font-mono font-bold text-slate-900">
                      {row.equity !== null ? (row.equity * 100).toFixed(0) + "%" : "—"}
                    </td>
                    <td className="px-6 py-3 text-slate-600 font-medium text-xs truncate max-w-[150px]" title={row.action}>
                      {row.action}
                    </td>
                    <td className="px-6 py-3 text-slate-500 text-xs max-w-md truncate" title={row.triggers}>
                      {row.triggers}
                    </td>
                  </tr>
                ))}
                {filteredRows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="p-12 text-center text-slate-400 bg-slate-50/50">
                      <Filter className="mx-auto h-8 w-8 mb-2 opacity-50" />
                      <p>No records found matching your filters.</p>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
