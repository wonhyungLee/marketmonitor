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

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  };
}

function describeArc(cx: number, cy: number, r: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(cx, cy, r, endAngle);
  const end = polarToCartesian(cx, cy, r, startAngle);
  const sweep = endAngle - startAngle;
  const largeArcFlag = sweep <= 180 ? "0" : "1";
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArcFlag} 1 ${end.x} ${end.y}`;
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
      title: isKo ? "시장 국면 시계" : "Market Phase Clock",
      bear: isKo ? "하락장(BEAR)" : "Bear market",
      bull: isKo ? "상승장(BULL)" : "Bull market",
      crisisModel: isKo ? "위기(리스크오프)" : "Crisis (risk-off)",
      overheatModel: isKo ? "과열(환희)" : "Euphoria (overheat)",
      now: isKo ? "현재" : "Now",
      started: isKo ? "시작" : "Started",
      elapsed: isKo ? "경과" : "Elapsed",
      days: isKo ? "일" : "d",
      cycleEta: isKo ? "사이클(±20%) 전환 예상" : "Rule-based switch (±20%)",
      modelEta: isKo ? "타이밍 모델(위기/과열)" : "Timing model (risk-off/overheat)",
      windowCycle: isKo ? "전환 유력 구간" : "Switch window",
      windowModel: isKo ? "유력 구간" : "Likely window",
      recent: isKo ? "직전 구간" : "Recent",
      termsTitle: isKo ? "용어 안내" : "Terminology",
      termsLine1: isKo
        ? "상승장/하락장(BULL/BEAR): NASDAQ 종가의 ±20% 규칙으로 구간을 나눈 결과입니다."
        : "Bull/Bear: phases from NASDAQ close using the ±20% rule.",
      termsLine2: isKo
        ? "위기/과열(환희): 별도 예측 모델 결과로, 상승장/하락장과 1:1로 같지 않을 수 있습니다."
        : "Crisis/Euphoria: model outputs; they may not match Bull/Bear 1:1.",
      termsLine3: isKo
        ? "바늘은 '구간 진행률'(현재 구간 일수 ÷ 전형적 구간 일수)을 뜻하며, 시계 방향 자체는 의미가 없습니다."
        : "The hand shows phase progress (elapsed days ÷ typical phase days); the direction itself is not meaningful.",
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
    const angle = current.regime === "BEAR" ? 0 + progress * 180 : 180 + progress * 180;

    const phaseStart = current.start;
    const predictedSwitch = addDays(parseISO(phaseStart), Math.round(avgDays));
    const predictedSwitchDate = isNaN(predictedSwitch.getTime()) ? null : predictedSwitch;
    const winHalf = Math.max(1, Math.round(avgDays * 0.2));
    const winStart = predictedSwitchDate ? addDays(predictedSwitchDate, -winHalf) : null;
    const winEnd = predictedSwitchDate ? addDays(predictedSwitchDate, winHalf) : null;

    const phaseName = current.regime === "BEAR" ? "fear" : "euphoria";
    const prevText = prev ? prev : null;

    // Window arc in angle-space (last 20% of the current half)
    const winStartProgress = clamp01(1 - 0.2);
    const winStartAngle = current.regime === "BEAR" ? winStartProgress * 180 : 180 + winStartProgress * 180;
    const boundaryAngle = current.regime === "BEAR" ? 180 : 360;

    return {
      segments,
      current,
      prev: prevText,
      medBull,
      medBear,
      avgDays,
      progress,
      angle,
      winStartAngle,
      boundaryAngle,
      predictedSwitchDate,
      winStart,
      winEnd,
      phaseName,
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

  const size = 420;
  const cx = 210;
  const cy = 210;
  const r = 165;
  const strokeW = 18;
  const pointerR = 145;

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
  const pointer = polarToCartesian(cx, cy, pointerR, calc.angle);
  const bearBase = describeArc(cx, cy, r, 0, 180);
  const bullBase = describeArc(cx, cy, r, 180, 360);
  const progArc = describeArc(cx, cy, r, currentIsBear ? 0 : 180, calc.angle);
  const winArc = describeArc(cx, cy, r, calc.winStartAngle, calc.boundaryAngle);

  const currentLabel = currentIsBear ? labels.bear : labels.bull;
  const predictedLabel = currentIsBear ? labels.bull : labels.bear;

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
        <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-auto">
          {/* base ring */}
          <path d={bearBase} fill="none" stroke="#fee2e2" strokeWidth={strokeW} strokeLinecap="round" />
          <path d={bullBase} fill="none" stroke="#dcfce7" strokeWidth={strokeW} strokeLinecap="round" />

          {/* progress */}
          <path
            d={progArc}
            fill="none"
            stroke={currentIsBear ? "#ef4444" : "#22c55e"}
            strokeWidth={strokeW}
            strokeLinecap="round"
          />

          {/* expected window (cycle-based) */}
          <path
            d={winArc}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={strokeW}
            strokeDasharray="4 6"
            strokeLinecap="round"
            opacity={0.9}
          />

          {/* pointer */}
          <line x1={cx} y1={cy} x2={pointer.x} y2={pointer.y} stroke="#0f172a" strokeWidth={3} strokeLinecap="round" />
          <circle cx={cx} cy={cy} r={6} fill="#0f172a" />

          {/* labels */}
          <text x={cx} y={42} textAnchor="middle" fontSize="14" fill="#b91c1c" fontWeight="700">
            {labels.bear}
          </text>
          <text x={cx} y={size - 24} textAnchor="middle" fontSize="14" fill="#15803d" fontWeight="700">
            {labels.bull}
          </text>

          {/* center text */}
          <text x={cx} y={cy - 24} textAnchor="middle" fontSize="14" fill="#0f172a" fontWeight="700">
            {labels.now}: {currentLabel}
          </text>
          <text x={cx} y={cy - 4} textAnchor="middle" fontSize="12" fill="#334155">
            {labels.started}: {calc.current.start}
          </text>
          <text x={cx} y={cy + 14} textAnchor="middle" fontSize="12" fill="#334155">
            {labels.elapsed}: {calc.current.days}{labels.days}
          </text>
          <text x={cx} y={cy + 34} textAnchor="middle" fontSize="12" fill="#334155">
            {labels.cycleEta}: {predictedLabel} → {fmtDate(calc.predictedSwitchDate)}
          </text>
        </svg>

        <div className="w-full mt-2 space-y-2">
          <div className="rounded-xl border border-slate-100 bg-white p-3">
            <div className="text-xs font-bold text-slate-900 mb-1">{labels.recent}</div>
            <div className="grid grid-cols-1 gap-1 text-xs text-slate-600">
              {calc.prev ? (
                <div>
                  {calc.prev.regime === "BEAR" ? labels.bear : labels.bull}: {calc.prev.start} → {calc.prev.end} ({calc.prev.days}{labels.days})
                </div>
              ) : (
                <div className="text-slate-400">—</div>
              )}
              <div>
                {labels.windowCycle}: {calc.winStart ? fmtDate(calc.winStart) : "—"} → {calc.winEnd ? fmtDate(calc.winEnd) : "—"}
                <span className="text-slate-400"> (cycle-based)</span>
              </div>
              {model ? (
                <div className="text-slate-500">
                  {labels.modelEta}:
                  <div className="mt-1 space-y-0.5 pl-2">
                    <div>
                      • {labels.crisisModel}: {model.crisisEta || "—"}
                      {model.crisisWin ? ` · ${labels.windowModel}: ${model.crisisWin}` : ""}
                    </div>
                    <div>
                      • {labels.overheatModel}: {model.euphoriaEta || "—"}
                      {model.euphoriaWin ? ` · ${labels.windowModel}: ${model.euphoriaWin}` : ""}
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="mt-2 text-[11px] text-slate-500 leading-relaxed">
                <div className="font-semibold text-slate-600 mb-0.5">{labels.termsTitle}</div>
                <div>• {labels.termsLine1}</div>
                <div>• {labels.termsLine2}</div>
                <div>• {labels.termsLine3}</div>
                <div className="mt-1 text-slate-400">* {labels.ruleDetail}</div>
                <div className="text-slate-400">* {labels.ruleNote}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
