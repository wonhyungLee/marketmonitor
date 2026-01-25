import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fetchCsv(url: string) {
  // Cache busting
  const target = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
  return fetch(target, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error("Failed to fetch " + url);
    return r.text();
  });
}
