import { useEffect } from "react";

const SLOT_ID = "cp-trader-combo";
const SCRIPT_ID = "cp-trader-combo-widget-script";

export default function CoupangTraderComboInline() {
  useEffect(() => {
    const base = String((import.meta as any).env?.BASE_URL || "/");
    const src = `${base.endsWith("/") ? base : `${base}/`}coupang-trader-combo-widget.js?v=20260223a`;

    const prev = document.getElementById(SCRIPT_ID);
    if (prev && prev.parentNode) {
      prev.parentNode.removeChild(prev);
    }

    const script = document.createElement("script");
    script.id = SCRIPT_ID;
    script.src = src;
    script.defer = true;
    document.body.appendChild(script);

    return () => {
      const el = document.getElementById(SCRIPT_ID);
      if (el && el.parentNode) {
        el.parentNode.removeChild(el);
      }
    };
  }, []);

  return (
    <div
      id={SLOT_ID}
      data-variant="inline"
      data-project="marketmonitor"
      data-endpoint="/site2/api/coupang-food-ads"
      data-products="/site2/coupang-products-2026.json"
      data-theme="auto"
      data-subid="marketmonitor"
      data-category="food"
      className="w-full"
      data-cp-ignore
    />
  );
}
