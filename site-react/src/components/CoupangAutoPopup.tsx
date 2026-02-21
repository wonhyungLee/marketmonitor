import { useEffect, useMemo, useRef, useState } from "react";

const STORAGE_KEY_NEXT_AT = "cp_auto_promo_next_at_v1";
const COOLDOWN_MS = 6 * 60 * 60 * 1000;

const PROMO_LINKS = [
  "https://link.coupang.com/a/dPJvzF",
  "https://link.coupang.com/a/dPJzZu",
  "https://link.coupang.com/a/dPJC4g",
  "https://link.coupang.com/a/dPJQFz",
  "https://link.coupang.com/a/dPJVxr",
  "https://link.coupang.com/a/dPJ2jt",
  "https://link.coupang.com/a/dPKcZs",
  "https://link.coupang.com/a/dPKgU0",
  "https://link.coupang.com/a/dPKjlp",
  "https://link.coupang.com/a/dPKIZ9",
  "https://link.coupang.com/a/dPKoN6",
  "https://link.coupang.com/a/dPKr4O",
  "https://link.coupang.com/a/dPKvE3",
  "https://link.coupang.com/a/dPKzjf",
  "https://link.coupang.com/a/dPKFV8",
  "https://link.coupang.com/a/dPKI7T",
];

function clampInt(value: unknown, fallback: number) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function readNextAt(): number {
  try {
    return clampInt(localStorage.getItem(STORAGE_KEY_NEXT_AT), 0);
  } catch {
    return 0;
  }
}

function writeNextAt(ts: number) {
  try {
    localStorage.setItem(STORAGE_KEY_NEXT_AT, String(ts));
  } catch {
    // ignore storage failures
  }
}

function pickRandomLink() {
  if (!PROMO_LINKS.length) return "";
  return PROMO_LINKS[Math.floor(Math.random() * PROMO_LINKS.length)];
}

function isEligibleClickTarget(target: EventTarget | null) {
  if (!target || !(target instanceof Element)) return false;
  if (target.closest("[data-cp-modal]")) return false;
  const clickable = target.closest("button, [role='button'], a");
  if (!clickable) return false;
  if (clickable.closest("[data-cp-ignore]")) return false;
  return true;
}

export default function CoupangAutoPopup({ disabled = false }: { disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [activeLink, setActiveLink] = useState<string>("");
  const openRef = useRef(open);

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  const close = () => setOpen(false);

  const openIfAllowed = () => {
    if (disabled) return;
    if (openRef.current) return;

    const now = Date.now();
    const nextAt = readNextAt();
    if (nextAt && now < nextAt) return;

    writeNextAt(now + COOLDOWN_MS);
    setActiveLink(pickRandomLink());
    setOpen(true);
  };

  useEffect(() => {
    if (disabled) return;

    const handler = (event: MouseEvent) => {
      if (disabled || openRef.current) return;
      if (!isEligibleClickTarget(event.target)) return;
      window.setTimeout(() => openIfAllowed(), 0);
    };

    document.addEventListener("click", handler, true);
    return () => document.removeEventListener("click", handler, true);
  }, [disabled]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const link = String(activeLink || "").trim();
  const hasLink = Boolean(link);

  const modal = useMemo(() => {
    if (!open) return null;

    return (
      <div
        data-cp-modal
        className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-200"
        role="dialog"
        aria-modal="true"
        aria-label="쿠팡 프로모션 (광고)"
        onClick={close}
      >
        <div
          className="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
            <div className="font-extrabold text-slate-900">
              쿠팡 프로모션{" "}
              <span className="ml-2 text-[10px] font-black px-2 py-1 rounded-full border border-slate-200 text-slate-500">
                AD
              </span>
            </div>
            <button
              type="button"
              className="p-2 hover:bg-slate-100 rounded-full text-slate-400 hover:text-slate-700 transition-colors"
              onClick={close}
              aria-label="닫기"
            >
              ✕
            </button>
          </div>

          <div className="p-5 space-y-3">
            <div className="text-sm text-slate-600">진행 중인 쿠팡 이벤트/프로모션을 확인해 보세요.</div>

            {hasLink ? (
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                onClick={close}
                className="block w-full text-center px-4 py-3 rounded-xl bg-slate-900 hover:bg-slate-800 text-white font-extrabold transition-colors"
              >
                쿠팡에서 프로모션 확인
              </a>
            ) : (
              <div className="text-sm text-slate-500">프로모션 링크를 불러오지 못했습니다.</div>
            )}

            <div className="text-[11px] text-slate-500 leading-relaxed">
              쿠팡파트너스 활동으로 수수료를 제공받을 수 있습니다.
            </div>

            <button
              type="button"
              className="w-full px-4 py-2 rounded-xl border border-slate-200 text-slate-600 font-bold hover:bg-slate-50 transition-colors"
              onClick={close}
            >
              닫기
            </button>
          </div>
        </div>
      </div>
    );
  }, [open, hasLink, link]);

  return modal;
}
