type Props = {
  title: string;
  subtitle?: string;
  monthsUntil?: number | null;
  periodMonths?: number | null;
  confidence?: number | null;
  size?: number;
};

function clamp01(x: number) {
  if (!Number.isFinite(x)) return 0;
  return Math.max(0, Math.min(1, x));
}

export default function CycleClock({
  title,
  subtitle,
  monthsUntil,
  periodMonths,
  confidence,
  size = 96,
}: Props) {
  const has = typeof monthsUntil === "number" && Number.isFinite(monthsUntil) && typeof periodMonths === "number" && Number.isFinite(periodMonths) && periodMonths > 0;
  const conf = clamp01(typeof confidence === "number" && Number.isFinite(confidence) ? confidence : 0.0);

  // Map "months until event" into a clock hand angle.
  // - At monthsUntil = periodMonths -> just after the last peak (angle ~ 0)
  // - At monthsUntil = 0 -> peak now (angle ~ 0)
  // We show progress through the cycle: progress = 1 - (monthsUntil / period)
  const progress = has ? (1 - (monthsUntil! / periodMonths!)) : 0;
  const angle = -90 + 360 * progress; // 0 progress points up

  const r = size / 2;
  const cx = r;
  const cy = r;
  const handLen = r * 0.72;

  const rad = (angle * Math.PI) / 180;
  const x2 = cx + handLen * Math.cos(rad);
  const y2 = cy + handLen * Math.sin(rad);

  return (
    <div className="flex items-center gap-3">
      <div className="relative" style={{ width: size, height: size, opacity: has ? 0.35 + 0.65 * conf : 0.4 }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          <circle cx={cx} cy={cy} r={r - 3} fill="none" stroke="currentColor" strokeOpacity={0.15} strokeWidth={2} />

          {/* 12 ticks */}
          {Array.from({ length: 12 }).map((_, i) => {
            const a = (-90 + (360 / 12) * i) * (Math.PI / 180);
            const r1 = r - 6;
            const r2 = r - 12;
            const x1 = cx + r1 * Math.cos(a);
            const y1 = cy + r1 * Math.sin(a);
            const x2t = cx + r2 * Math.cos(a);
            const y2t = cy + r2 * Math.sin(a);
            return (
              <line
                key={i}
                x1={x1}
                y1={y1}
                x2={x2t}
                y2={y2t}
                stroke="currentColor"
                strokeOpacity={0.22}
                strokeWidth={2}
                strokeLinecap="round"
              />
            );
          })}

          {/* Hand */}
          <line x1={cx} y1={cy} x2={x2} y2={y2} stroke="currentColor" strokeOpacity={0.55} strokeWidth={3} strokeLinecap="round" />
          <circle cx={cx} cy={cy} r={4} fill="currentColor" fillOpacity={0.6} />
        </svg>

        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[10px] font-semibold text-slate-700">{has ? `${Math.max(0, Math.round(monthsUntil!))}m` : "—"}</div>
          <div className="text-[9px] text-slate-400">{has ? `conf ${(conf * 100).toFixed(0)}%` : "no data"}</div>
        </div>
      </div>

      <div className="min-w-[120px]">
        <div className="text-xs font-bold text-slate-900">{title}</div>
        {subtitle ? <div className="text-[11px] text-slate-500">{subtitle}</div> : null}
      </div>
    </div>
  );
}
