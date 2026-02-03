import { useMemo } from "react";
import { addDays, differenceInCalendarDays, parseISO } from "date-fns";

type Lang = "en" | "ko";

type NasdaqPoint = {
  date: string;
  close: number | null;
};

type Regime = "BULL" | "BEAR";

type Segment = {
  regime: Regime;
  start: string;
  end: string;
  days: number;
};

function median(values: number[]): number | null {
  const v = values.filter((n) => Number.isFinite(n)).slice().sort((a, b) => a - b);
  if (v.length === 0) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 === 1 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

function clamp01(x: number): number {
  if (!Number.isFinite(x)) return 0;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

function daysBetween(start: string, end: string): number {
  try {
    return Math.max(0, differenceInCalendarDays(parseISO(end), parseISO(start)));
  } catch {
    return 0;
  }
}

// Bear starts when index drops >=20% from a prior peak.
// Bull starts when index rises >=20% from a prior trough.
function buildBullBearSegments(nasdaq: NasdaqPoint[]): { segments: Segment[]; current?: Segment; prev?: Segment } {
  const pts = nasdaq
    .filter((p) => typeof p.close === "number" && Number.isFinite(p.close))
    .slice()
    .sort((a, b) => (a.date < b.date ? -1 : 1)) as Array<{ date: string; close: number }>;

  if (pts.length < 2) return { segments: [] };

  let regime: Regime = "BULL";
  let segStart = pts[0].date;
  let peak = pts[0].close;
  let trough = pts[0].close;

  const segs: Segment[] = [];

  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (regime === "BULL") {
      if (p.close > peak) peak = p.close;
      // Switch to BEAR
      if (p.close <= peak * 0.8) {
        const end = p.date;
        segs.push({
          regime: "BULL",
          start: segStart,
          end,
          days: daysBetween(segStart, end),
        });
        regime = "BEAR";
        segStart = end;
        trough = p.close;
      }
    } else {
      if (p.close < trough) trough = p.close;
      // Switch to BULL
      if (p.close >= trough * 1.2) {
        const end = p.date;
        segs.push({
          regime: "BEAR",
          start: segStart,
          end,
          days: daysBetween(segStart, end),
        });
        regime = "BULL";
        segStart = end;
        peak = p.close;
      }
    }
  }

  // Open segment (ends at last date)
  const lastDate = pts[pts.length - 1].date;
  segs.push({
    regime,
    start: segStart,
    end: lastDate,
    days: daysBetween(segStart, lastDate),
  });

  const current = segs[segs.length - 1];
  const prev = segs.length >= 2 ? segs[segs.length - 2] : undefined;
  return { segments: segs, current, prev };
}

export default function BigCycleClock({
  nasdaq,
  timing,
  lang = "ko",
}: {
  nasdaq: NasdaqPoint[];
  timing?: any | null;
  lang?: Lang;
}) {
  const labels = useMemo(() => {
    const isKo = lang === "ko";
    return {
      title: isKo ? "시장 국면 게이지" : "Market Phase Gauge",
      bear: isKo ? "하락장(BEAR)" : "Bear market",
      bull: isKo ? "상승장(BULL)" : "Bull market",
      crisisModel: isKo ? "위기(리스크오프)" : "Crisis (risk-off)",
      overheatModel: isKo ? "과열(환희)" : "Euphoria (overheat)",
      now: isKo ? "현재" : "Now",
      asOf: isKo ? "데이터 기준일" : "As of",
      started: isKo ? "시작" : "Started",
      elapsed: isKo ? "경과" : "Elapsed",
      days: isKo ? "일" : "d",
      cycleEta: isKo ? "다음 전환 참고일(확정 아님)" : "Reference switch date (not guaranteed)",
      windowCycle: isKo ? "전환 유력 구간" : "Switch window",
      windowModel: isKo ? "유력 구간" : "Likely window",
      windowCycleNote: isKo ? "전형적 길이±20% 참고" : "±20% of typical length",
      summary: isKo ? "요약" : "Summary",
      recent: isKo ? "직전 구간" : "Recent",
      note: isKo ? "※ 날짜는 확정이 아니라 참고/확률 정보입니다." : "Note: dates are references/probabilities, not guarantees.",
      gaugeProgress: isKo ? "진행" : "Progress",
      stageEarly: isKo ? "초반" : "Early",
      stageMid: isKo ? "중반" : "Mid",
      stageLate: isKo ? "후반" : "Late",
      windowBefore: isKo ? "전환 유력 구간 이전" : "Before likely window",
      windowInside: isKo ? "전환 유력 구간 안" : "Inside likely window",
      gaugeStart: isKo ? "시작" : "Start",
      gaugeLikely: isKo ? "유력" : "Likely",
      gaugeRef: isKo ? "참고 전환" : "Ref",
      gaugeHint: isKo
        ? "게이지는 현재 구간이 ‘전형적 길이’ 대비 어느 정도 진행됐는지를 보여줍니다. (확정 예측 아님)"
        : "The gauge shows phase progress vs a ‘typical’ duration. (Not a guarantee)",
      ruleDetail: isKo
        ? "기준: 고점 대비 -20%면 하락장 시작, 저점 대비 +20%면 상승장 시작."
        : "Rule: Bear starts at -20% from peak; Bull starts at +20% from trough.",
      ruleNote: isKo
        ? "점선: 과거 전형적 구간 길이를 기준으로 한 전환 유력 구간(±20%)."
        : "Dashed arc: cycle-based switch window (±20% of typical phase length).",
    };
  }, [lang]);

  const calc = useMemo(() => {
    const { segments, current, prev } = buildBullBearSegments(nasdaq);
    if (!current) return null;

    // Median durations from completed segments (exclude current open segment)
    const completed = segments.slice(0, Math.max(0, segments.length - 1));
    const bullDays = completed.filter((s) => s.regime === "BULL" && s.days >= 20).map((s) => s.days);
    const bearDays = completed.filter((s) => s.regime === "BEAR" && s.days >= 20).map((s) => s.days);
    const medBull = median(bullDays.slice(-12)) ?? median(bullDays) ?? 252; // fallback ~1y
    const medBear = median(bearDays.slice(-12)) ?? median(bearDays) ?? 126; // fallback ~6m

    const avgDays = current.regime === "BULL" ? medBull : medBear;
    const progress = clamp01(avgDays > 0 ? current.days / avgDays : 0);

    const phaseStart = current.start;
    const predictedSwitch = addDays(parseISO(phaseStart), Math.round(avgDays));
    const predictedSwitchDate = isNaN(predictedSwitch.getTime()) ? null : predictedSwitch;
    const winHalf = Math.max(1, Math.round(avgDays * 0.2));
    const winStart = predictedSwitchDate ? addDays(predictedSwitchDate, -winHalf) : null;
    const winEnd = predictedSwitchDate ? addDays(predictedSwitchDate, winHalf) : null;

    const prevText = prev ? prev : null;
    const winStartProgress = clamp01(1 - 0.2); // last 20% of typical duration

    return {
      segments,
      current,
      prev: prevText,
      medBull,
      medBear,
      avgDays,
      progress,
      predictedSwitchDate,
      winStart,
      winEnd,
      winStartProgress,
    };
  }, [nasdaq]);

  const model = useMemo(() => {
    if (!timing) return null;
    return {
      crisisEta: timing.eta_crisis_median_date || null,
      crisisWin: timing.crisis_mode_start && timing.crisis_mode_end ? `${timing.crisis_mode_start} ~ ${timing.crisis_mode_end}` : null,
      euphoriaEta: timing.eta_euphoria_median_date || null,
      euphoriaWin: timing.euphoria_mode_start && timing.euphoria_mode_end ? `${timing.euphoria_mode_start} ~ ${timing.euphoria_mode_end}` : null,
    };
  }, [timing]);

  if (!calc) {
    return (
      <div className="w-full max-w-[520px]">
        <div className="rounded-2xl border border-slate-100 bg-slate-50 p-4 text-sm text-slate-500">
          {lang === "ko" ? "사이클을 계산하기에 데이터가 부족합니다." : "Not enough data to compute cycles."}
        </div>
      </div>
    );
  }

  const currentIsBear = calc.current.regime === "BEAR";
  const currentLabel = currentIsBear ? labels.bear : labels.bull;
  const predictedLabel = currentIsBear ? labels.bull : labels.bear;
  const stage =
    calc.progress < 0.33 ? labels.stageEarly : calc.progress < 0.66 ? labels.stageMid : labels.stageLate;
  const inLikelyWindow = calc.progress >= calc.winStartProgress;

  const fmtDate = (d: Date | null) => {
    if (!d) return "—";
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${dd}`;
  };

  return (
    <div className="w-full max-w-[520px]">
      <div className="flex flex-col items-center">
        <div className="w-full rounded-2xl border border-slate-100 bg-white p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-bold text-slate-900">{labels.title}</div>
            <span
              className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold border"
              style={{
                backgroundColor: (currentIsBear ? "#fee2e2" : "#dcfce7"),
                color: currentIsBear ? "#b91c1c" : "#15803d",
                borderColor: currentIsBear ? "#fecaca" : "#bbf7d0",
              }}
            >
              {currentLabel}
            </span>
          </div>

          <div className="mt-1 text-xs text-slate-500">
            {labels.asOf}: <span className="font-mono text-slate-900">{calc.current.end}</span> · {labels.started}:{" "}
            <span className="font-mono text-slate-900">{calc.current.start}</span>
          </div>

          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-slate-600 mb-2">
              <div>
                {labels.gaugeProgress}: <span className="font-semibold text-slate-900">{stage}</span>
                <span className="text-slate-500"> · {inLikelyWindow ? labels.windowInside : labels.windowBefore}</span>
              </div>
              <div className="text-slate-400">{labels.cycleEta}: {fmtDate(calc.predictedSwitchDate)}</div>
            </div>

            <div className="relative h-3 rounded-full bg-slate-100 overflow-hidden">
              <div
                className="absolute inset-y-0 left-0"
                style={{
                  width: `${Math.round(calc.progress * 1000) / 10}%`,
                  backgroundColor: currentIsBear ? "#ef4444" : "#22c55e",
                }}
              />
              <div
                className="absolute inset-y-0 border border-slate-400/60 border-dashed bg-slate-200/40"
                style={{
                  left: `${Math.round(calc.winStartProgress * 1000) / 10}%`,
                  width: `${Math.round((1 - calc.winStartProgress) * 1000) / 10}%`,
                }}
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3.5 w-3.5 rounded-full border-2 border-white bg-slate-900"
                style={{ left: `${Math.round(calc.progress * 1000) / 10}%` }}
              />
            </div>

            <div className="mt-2 flex justify-between text-[11px] text-slate-400">
              <span>{labels.gaugeStart}</span>
              <span>{labels.gaugeLikely}</span>
              <span>{labels.gaugeRef}</span>
            </div>

            <div className="mt-2 text-[11px] text-slate-500 leading-relaxed">{labels.gaugeHint}</div>
          </div>
        </div>

        <div className="w-full mt-2 space-y-2">
          <div className="rounded-xl border border-slate-100 bg-white p-3">
            <div className="text-xs font-bold text-slate-900 mb-1">{labels.summary}</div>
            <div className="space-y-1.5 text-xs text-slate-600 leading-relaxed">
              <div>
                {labels.asOf}: <span className="font-mono text-slate-900">{calc.current.end}</span>
              </div>
              <div>
                {labels.now}: <span className="font-semibold text-slate-900">{currentLabel}</span> · {labels.started}:{" "}
                <span className="font-mono text-slate-900">{calc.current.start}</span>
              </div>
              <div>
                {labels.cycleEta}: <span className="font-mono text-slate-900">{fmtDate(calc.predictedSwitchDate)}</span>{" "}
                <span className="text-slate-500">({predictedLabel})</span>
              </div>
              <div>
                {labels.windowCycle}: <span className="font-mono text-slate-900">{calc.winStart ? fmtDate(calc.winStart) : "—"}</span> ~{" "}
                <span className="font-mono text-slate-900">{calc.winEnd ? fmtDate(calc.winEnd) : "—"}</span>{" "}
                <span className="text-slate-400">({labels.windowCycleNote})</span>
              </div>

              {model ? (
                <div className="pt-1">
                  {labels.crisisModel}: <span className="font-mono text-slate-900">{model.crisisWin || model.crisisEta || "—"}</span>
                  <span className="text-slate-400">{model.crisisWin ? "" : lang === "ko" ? " (ETA 참고)" : " (ETA ref.)"}</span>
                  <br />
                  {labels.overheatModel}: <span className="font-mono text-slate-900">{model.euphoriaWin || model.euphoriaEta || "—"}</span>
                  <span className="text-slate-400">{model.euphoriaWin ? "" : lang === "ko" ? " (ETA 참고)" : " (ETA ref.)"}</span>
                </div>
              ) : null}

              {calc.prev ? (
                <div className="pt-1 text-slate-500">
                  {labels.recent}: {calc.prev.regime === "BEAR" ? labels.bear : labels.bull} {calc.prev.start} → {calc.prev.end}
                </div>
              ) : null}

              <div className="pt-1 text-[11px] text-slate-500">{labels.note}</div>
              <div className="text-[11px] text-slate-400">* {labels.ruleDetail}</div>
              <div className="text-[11px] text-slate-400">* {labels.ruleNote}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
